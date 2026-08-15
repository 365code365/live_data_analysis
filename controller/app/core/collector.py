from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from ..db import session_scope
from ..models import (
    Device,
    LiveSnapshot,
    MonitorTask,
    ProductRecord,
    RunStatus,
    utcnow,
)
from ..platforms import get_adapter
from ..platforms.uitree import normalize_key
from . import events
from .android import get_device
from .device_service import DeviceError, pick_ready_device
from .recorder import recorder

log = logging.getLogger(__name__)

# 同一台设备同一时刻只允许一个采集任务操作界面，否则会互相点乱
_device_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_concurrency = threading.BoundedSemaphore(max(1, settings.max_concurrent_tasks))

KEEP_DUMPS_PER_TASK = 20
KEEP_SHOTS_PER_TASK = 200


def _device_lock(device_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_locks[device_id] = lock
        return lock


def _prune(directory: Path, keep: int, pattern: str = "*") -> None:
    try:
        files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except Exception:
        log.debug("清理旧文件失败: %s", directory, exc_info=True)


# ──────────────────────────────────────────────────────────────────────────
def run_task(task_id: int) -> dict[str, Any]:
    """执行一次采集。被调度器和「立即执行」接口共用。"""
    with session_scope() as session:
        task = session.get(MonitorTask, task_id)
        if task is None:
            return {"ok": False, "error": f"任务不存在: {task_id}"}
        if not task.enabled:
            return {"ok": False, "skipped": True, "error": "任务已停用"}
        try:
            device = pick_ready_device(session, task.device_id)
        except DeviceError as exc:
            _mark_failed(session, task, str(exc))
            return {"ok": False, "error": str(exc)}
        snapshot_ctx = {
            "task_id": int(task.id),
            "task_name": task.name,
            "platform": task.platform.value,
            "target": task.target,
            "device_id": int(device.id),
            "device_addr": device.adb_addr,
            "collect_products": task.collect_products,
            "collect_comments": task.collect_comments,
            "record_video": task.record_video,
            "keep_screenshot": task.keep_screenshot,
        }

    acquired = _concurrency.acquire(timeout=300)
    if not acquired:
        return {"ok": False, "error": "采集并发已满，本轮跳过"}
    lock = _device_lock(snapshot_ctx["device_id"])
    if not lock.acquire(timeout=600):
        _concurrency.release()
        return {"ok": False, "error": "设备正忙（另一个任务在操作界面）"}

    try:
        return _do_collect(**snapshot_ctx)
    finally:
        lock.release()
        _concurrency.release()


def _do_collect(
    *,
    task_id: int,
    task_name: str,
    platform: str,
    target: str,
    device_id: int,
    device_addr: str,
    collect_products: bool,
    collect_comments: bool,
    record_video: bool,
    keep_screenshot: bool,
) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shot_dir = settings.screenshots_dir / f"task_{task_id}"
    dump_dir = settings.dumps_dir / f"task_{task_id}"
    shot_path = shot_dir / f"{stamp}.png" if keep_screenshot else None
    dump_path = dump_dir / f"{stamp}.xml"

    adapter = get_adapter(platform)
    dev = get_device(device_addr)

    try:
        result = adapter.collect(
            dev,
            target=target,
            want_products=collect_products,
            want_comments=collect_comments,
            screenshot_path=shot_path,
            dump_path=dump_path,
            max_scrolls=settings.max_product_scrolls,
        )
    except Exception as exc:
        log.warning("任务 %s 采集失败: %s", task_id, exc)
        with session_scope() as session:
            task = session.get(MonitorTask, task_id)
            if task:
                _mark_failed(session, task, str(exc))
        events.emit(f"任务「{task_name}」采集失败: {exc}", level="error", source="collector", task_id=task_id, device_id=device_id)
        return {"ok": False, "error": str(exc)}

    # ── 落库 ──────────────────────────────────────────────────────────
    with session_scope() as session:
        snapshot = LiveSnapshot(
            task_id=task_id,
            device_id=device_id,
            captured_at=utcnow(),
            is_live=result.live.is_live,
            room_id=result.live.room_id,
            room_title=_clip(result.live.room_title, 300),
            anchor_name=_clip(result.live.anchor_name, 120),
            viewer_count=result.live.viewer_count,
            like_count=result.live.like_count,
            follower_count=result.live.follower_count,
            product_count=len(result.products),
            screenshot_path=result.screenshot_path,
            dump_path=result.dump_path,
            comments_json=json.dumps(result.comments, ensure_ascii=False) if result.comments else None,
            raw_json=json.dumps(
                {"viewer_text": result.live.viewer_text, "warnings": result.warnings, **result.live.extra},
                ensure_ascii=False,
            ),
        )
        session.add(snapshot)
        session.flush()

        for item in result.products:
            session.add(
                ProductRecord(
                    task_id=task_id,
                    snapshot_id=int(snapshot.id),
                    captured_at=snapshot.captured_at,
                    position=item.position,
                    product_key=normalize_key(item.title or "") or None,
                    product_id=item.product_id,
                    title=_clip(item.title, 300),
                    price=item.price,
                    price_text=_clip(item.price_text, 60),
                    origin_price=item.origin_price,
                    sales_text=_clip(item.sales_text, 60),
                    stock_text=_clip(item.stock_text, 60),
                    coupon_text=_clip(item.coupon_text, 60),
                    raw_json=json.dumps(item.labels, ensure_ascii=False),
                )
            )

        task = session.get(MonitorTask, task_id)
        if task:
            task.last_run_at = utcnow()
            task.run_count += 1
            task.last_error = "; ".join(result.warnings)[:1000] if result.warnings else None
            task.last_status = RunStatus.partial if result.warnings else RunStatus.success
            if not result.live.is_live:
                task.last_status = RunStatus.partial
            task.next_run_at = utcnow() + timedelta(seconds=task.interval_seconds)
            session.add(task)

        snapshot_id = int(snapshot.id)

    # ── 录屏联动 ──────────────────────────────────────────────────────
    recording_id: Optional[int] = None
    if record_video:
        if result.live.is_live:
            recording_id = recorder.start(device_id=device_id, addr=device_addr, task_id=task_id)
        elif recorder.is_recording(device_id):
            recorder.stop(device_id, wait=False)
            events.emit("直播已结束，自动停止录屏", source="collector", task_id=task_id, device_id=device_id)

    if shot_path is not None:
        _prune(shot_dir, KEEP_SHOTS_PER_TASK, "*.png")
    _prune(dump_dir, KEEP_DUMPS_PER_TASK, "*.xml")

    summary = (
        f"任务「{task_name}」采集完成：{'直播中' if result.live.is_live else '未在直播'}"
        f" 在线{result.live.viewer_count if result.live.viewer_count is not None else '-'}"
        f" 商品{len(result.products)}件"
    )
    events.emit(summary, source="collector", task_id=task_id, device_id=device_id)

    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "is_live": result.live.is_live,
        "live": result.live.to_dict(),
        "products": [p.to_dict() for p in result.products],
        "comments": result.comments,
        "warnings": result.warnings,
        "screenshot": result.screenshot_path,
        "dump": result.dump_path,
        "recording_id": recording_id,
    }


def _mark_failed(session, task: MonitorTask, error: str) -> None:  # noqa: ANN001
    task.last_run_at = utcnow()
    task.run_count += 1
    task.fail_count += 1
    task.last_status = RunStatus.failed
    task.last_error = error[:1000]
    task.next_run_at = utcnow() + timedelta(seconds=task.interval_seconds)
    session.add(task)


def _clip(value: Optional[str], length: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value[:length] if value else None


def preview_ui(device_id: int, platform: Optional[str] = None) -> dict[str, Any]:
    """调试用：把当前界面的控件文本吐出来，方便调 selectors。"""
    with session_scope() as session:
        device = session.get(Device, device_id)
        if device is None:
            raise DeviceError(f"设备不存在: {device_id}")
        addr = device.adb_addr

    from ..platforms.uitree import parse_hierarchy

    dev = get_device(addr)
    tree = parse_hierarchy(dev.dump_hierarchy(compressed=True))
    payload: dict[str, Any] = {
        "current_package": dev.current_package(),
        "screen": {"width": tree.width, "height": tree.height},
        "labels": tree.all_labels()[:200],
        "nodes": [
            {
                "text": n.text,
                "desc": n.desc,
                "rid": n.rid,
                "cls": n.cls.rsplit(".", 1)[-1],
                "bounds": list(n.bounds),
                "clickable": n.clickable,
            }
            for n in tree.text_nodes()[:300]
        ],
    }
    if platform:
        adapter = get_adapter(platform)
        payload["in_live_room"] = adapter.in_live_room(tree)
        payload["live_info"] = adapter.extract_live_info(tree).to_dict()
        payload["products_on_screen"] = [p.to_dict() for p in adapter.extract_products(tree)]
    return payload
