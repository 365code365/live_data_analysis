from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

from ..config import settings
from ..db import session_scope
from ..models import Device, DeviceStatus, MonitorTask
from . import events
from .collector import run_task

log = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None
JOB_PREFIX = "task_"


def _job_id(task_id: int) -> str:
    return f"{JOB_PREFIX}{task_id}"


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=max(2, settings.max_concurrent_tasks))},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
            timezone="Asia/Shanghai",
        )
    return _scheduler


def start() -> None:
    sched = get_scheduler()
    if sched.running:
        return
    sched.add_job(
        _housekeeping,
        trigger=IntervalTrigger(seconds=60),
        id="housekeeping",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(seconds=20),
    )
    sched.add_job(
        _billing_housekeeping,
        trigger=IntervalTrigger(seconds=120),
        id="billing_housekeeping",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(seconds=45),
    )
    sched.start()
    reload_jobs()
    log.info("调度器已启动")


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("调度器已停止")
    _scheduler = None


def reload_jobs() -> int:
    """按数据库里的任务重建全部 job。"""
    sched = get_scheduler()
    for job in list(sched.get_jobs()):
        if job.id.startswith(JOB_PREFIX):
            job.remove()
    count = 0
    with session_scope() as session:
        for task in session.exec(select(MonitorTask).where(MonitorTask.enabled == True)).all():  # noqa: E712
            _add_job(sched, task)
            count += 1
    log.info("已装载 %s 个采集任务", count)
    return count


def sync_task(task: MonitorTask) -> None:
    """任务增删改后调用，保持 job 与数据库一致。"""
    sched = get_scheduler()
    job_id = _job_id(int(task.id))
    existing = sched.get_job(job_id)
    if not task.enabled:
        if existing:
            existing.remove()
            log.info("任务 %s 已停用，移除调度", task.id)
        return
    if existing:
        existing.remove()
    _add_job(sched, task)


def remove_task(task_id: int) -> None:
    sched = get_scheduler()
    job = sched.get_job(_job_id(task_id))
    if job:
        job.remove()


def _add_job(sched: BackgroundScheduler, task: MonitorTask) -> None:
    interval = max(10, int(task.interval_seconds))
    sched.add_job(
        run_task,
        trigger=IntervalTrigger(seconds=interval, jitter=min(15, interval // 4 or 1)),
        args=[int(task.id)],
        id=_job_id(int(task.id)),
        name=f"{task.platform.value}:{task.name}",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(seconds=5),
    )


def jobs() -> list[dict[str, Any]]:
    sched = get_scheduler()
    out = []
    for job in sched.get_jobs():
        out.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )
    return out


def _billing_housekeeping() -> None:
    """关闭超时订单、失效过期权益，并推进仍在等待支付的订单。"""
    from . import billing

    if not settings.billing_enabled:
        return
    try:
        with session_scope() as session:
            stats = billing.expire_stale(session)
            if stats["orders_closed"] or stats["entitlements_expired"]:
                log.info("计费清理: %s", stats)
            # 回调可能丢失（本地无公网/回调被拦），这里主动补一次查询
            from ..models import Order, OrderStatus

            pending = session.exec(
                select(Order).where(Order.status == OrderStatus.pending)
            ).all()
            for order in pending[:20]:
                try:
                    billing.sync_order(session, order)
                except Exception as exc:
                    log.debug("同步订单 %s 失败: %s", order.order_no, exc)
    except Exception:
        log.exception("billing housekeeping 执行异常")


def _housekeeping() -> None:
    """周期性把容器实际状态同步进数据库，顺带发现挂掉的设备。"""
    from .device_service import sync_status  # 避免循环导入

    try:
        with session_scope() as session:
            devices = session.exec(
                select(Device).where(Device.status.in_([DeviceStatus.running, DeviceStatus.starting]))  # type: ignore[attr-defined]
            ).all()
            for device in devices:
                before = device.status
                try:
                    sync_status(session, device)
                except Exception as exc:
                    log.debug("同步设备状态失败 %s: %s", device.id, exc)
                    continue
                if before != device.status:
                    events.emit(
                        f"设备 {device.name} 状态变化: {before} → {device.status}",
                        level="warning",
                        source="housekeeping",
                        device_id=device.id,
                    )
    except Exception:
        log.exception("housekeeping 执行异常")
