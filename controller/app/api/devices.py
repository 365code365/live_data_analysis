from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlmodel import Session, select

from ..config import settings
from ..core import collector, device_service
from ..core.android import get_device
from ..core.device_service import DeviceError
from ..core.docker_manager import DockerError, get_docker
from ..core.recorder import recorder
from ..db import get_session
from ..models import Device, ProxyProfile
from ..schemas import (
    ApkInstall,
    DeeplinkAction,
    DeviceCreate,
    DeviceUpdate,
    Ok,
    RecordStart,
    ShellCommand,
    SwipeAction,
    TapAction,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


def _get(session: Session, device_id: int) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "设备不存在")
    return device


def _out(session: Session, device: Device) -> dict[str, Any]:
    proxy = session.get(ProxyProfile, device.proxy_id) if device.proxy_id else None
    return {
        "id": device.id,
        "name": device.name,
        "status": device.status,
        "width": device.width,
        "height": device.height,
        "dpi": device.dpi,
        "android_image": device.android_image,
        "proxy_id": device.proxy_id,
        "proxy_name": proxy.name if proxy else None,
        "proxy_url_masked": proxy.url(mask=True) if proxy else None,
        "adb_port": device.adb_port,
        "novnc_port": device.novnc_port,
        "adb_addr": device.adb_addr,
        "vnc_password": device.vnc_password,
        "egress_ip": device.egress_ip,
        "egress_region": device.egress_region,
        "containers": {
            "gw": device.gw_container,
            "android": device.android_container,
            "vnc": device.vnc_container,
        },
        "data_volume": device.data_volume,
        "recording": recorder.is_recording(int(device.id)) if device.id else False,
        "last_error": device.last_error,
        "booted_at": device.booted_at,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────
@router.get("")
def list_devices(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    devices = session.exec(select(Device).order_by(Device.id)).all()
    return [_out(session, d) for d in devices]


@router.post("", status_code=201)
def create_device(payload: DeviceCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        device = device_service.create_device(session, **payload.model_dump())
    except DeviceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _out(session, device)


@router.get("/apks")
def list_apks() -> list[dict[str, Any]]:
    """列出 apks/ 目录下可安装的包。"""
    apk_dir = settings.apk_dir
    if not apk_dir.exists():
        return []
    out = []
    for p in sorted(apk_dir.glob("*.apk")):
        out.append({"filename": p.name, "size_mb": round(p.stat().st_size / 1048576, 1)})
    return out


@router.get("/{device_id}")
def get_device_detail(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    return _out(session, device)


@router.patch("/{device_id}")
def update_device(device_id: int, payload: DeviceUpdate, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(device, key, value)
    session.add(device)
    session.commit()
    session.refresh(device)
    hint = ""
    if {"proxy_id", "width", "height", "dpi", "android_image", "vnc_password"} & set(changes):
        hint = "改动需要重启设备后生效"
    return {**_out(session, device), "hint": hint}


@router.delete("/{device_id}")
def delete_device(
    device_id: int,
    purge_data: bool = Query(False, description="同时删除安卓 /data 数据卷（会丢登录态）"),
    session: Session = Depends(get_session),
) -> Ok:
    device = _get(session, device_id)
    device_service.delete_device(session, device, purge_data=purge_data)
    return Ok(message="已删除")


# ── 生命周期 ──────────────────────────────────────────────────────────────
@router.post("/{device_id}/start")
def start_device(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    try:
        device = device_service.start_device(session, device)
    except DeviceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _out(session, device)


@router.post("/{device_id}/stop")
def stop_device(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    return _out(session, device_service.stop_device(session, device))


@router.post("/{device_id}/restart")
def restart_device(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    try:
        return _out(session, device_service.restart_device(session, device))
    except DeviceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{device_id}/status")
def device_status(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    detail = device_service.sync_status(session, device)
    return {**_out(session, device), **detail}


@router.get("/{device_id}/egress")
def device_egress(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    try:
        return device_service.refresh_egress(session, device)
    except DockerError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{device_id}/logs")
def device_logs(
    device_id: int,
    role: str = Query("gw", pattern="^(gw|android|vnc)$"),
    tail: int = Query(200, ge=10, le=2000),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    device = _get(session, device_id)
    name = {"gw": device.gw_container, "android": device.android_container, "vnc": device.vnc_container}[role]
    if not name:
        raise HTTPException(400, "该角色容器尚未创建")
    return {"container": name, "logs": get_docker().logs(name, tail=tail)}


# ── 应用 ──────────────────────────────────────────────────────────────────
@router.post("/{device_id}/apk/install")
def install_apk(device_id: int, payload: ApkInstall, session: Session = Depends(get_session)) -> Ok:
    device = _get(session, device_id)
    # 只允许安装 apks/ 目录内的文件，避免路径穿越
    apk_path = (settings.apk_dir / Path(payload.filename).name).resolve()
    if not str(apk_path).startswith(str(settings.apk_dir.resolve())) or not apk_path.exists():
        raise HTTPException(404, f"apks/ 下找不到 {payload.filename}")
    try:
        out = device_service.install_apk(device, str(apk_path))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return Ok(message=out.strip()[:300] or "安装完成")


@router.post("/{device_id}/apk/upload")
async def upload_and_install(
    device_id: int,
    file: UploadFile,
    install: bool = Query(True),
    session: Session = Depends(get_session),
) -> Ok:
    device = _get(session, device_id)
    filename = Path(file.filename or "upload.apk").name
    if not filename.endswith(".apk"):
        raise HTTPException(400, "只接受 .apk 文件")
    settings.apk_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.apk_dir / filename
    try:
        dest.write_bytes(await file.read())
    except OSError as exc:
        raise HTTPException(500, f"保存失败（apks/ 目录是否只读？）: {exc}") from exc
    if not install:
        return Ok(message=f"已上传 {filename}")
    try:
        out = device_service.install_apk(device, str(dest))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return Ok(message=out.strip()[:300] or "安装完成")


@router.get("/{device_id}/packages")
def list_packages(
    device_id: int,
    keyword: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    return {"packages": dev.list_packages(keyword)}


# ── 交互 ──────────────────────────────────────────────────────────────────
@router.post("/{device_id}/shell")
def run_shell(device_id: int, payload: ShellCommand, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    try:
        return {"output": dev.shell(payload.command, timeout=payload.timeout)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{device_id}/tap")
def tap(device_id: int, payload: TapAction, session: Session = Depends(get_session)) -> Ok:
    device = _get(session, device_id)
    get_device(device.adb_addr).tap(payload.x, payload.y)
    return Ok()


@router.post("/{device_id}/swipe")
def swipe(device_id: int, payload: SwipeAction, session: Session = Depends(get_session)) -> Ok:
    device = _get(session, device_id)
    get_device(device.adb_addr).swipe(payload.x1, payload.y1, payload.x2, payload.y2, payload.duration_ms)
    return Ok()


@router.post("/{device_id}/key/{keycode}")
def keyevent(device_id: int, keycode: str, session: Session = Depends(get_session)) -> Ok:
    device = _get(session, device_id)
    get_device(device.adb_addr).key(keycode)
    return Ok()


@router.post("/{device_id}/deeplink")
def open_deeplink(device_id: int, payload: DeeplinkAction, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    out = get_device(device.adb_addr).open_deeplink(payload.uri, payload.package)
    return {"output": out}


@router.get("/{device_id}/screenshot")
def screenshot(device_id: int, session: Session = Depends(get_session)) -> Response:
    device = _get(session, device_id)
    tmp = settings.screenshots_dir / f"_live_{device_id}.png"
    try:
        get_device(device.adb_addr).screenshot(tmp)
        data = tmp.read_bytes()
    except Exception as exc:
        raise HTTPException(400, f"截图失败: {exc}") from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{device_id}/ui")
def ui_dump(
    device_id: int,
    platform: Optional[str] = Query(None, description="传 douyin/xiaohongshu 会顺带跑一遍提取规则"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _get(session, device_id)
    try:
        return collector.preview_ui(device_id, platform)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


# ── 录屏 ──────────────────────────────────────────────────────────────────
@router.post("/{device_id}/record/start")
def start_record(device_id: int, payload: RecordStart, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_booted():
        raise HTTPException(400, "安卓还没启动完成")
    size = payload.size or f"{device.width}x{device.height}"
    recording_id = recorder.start(
        device_id=int(device.id),
        addr=device.adb_addr,
        task_id=payload.task_id,
        bitrate=payload.bitrate,
        segment_seconds=payload.segment_seconds,
        size=size,
        max_duration_seconds=payload.max_duration_seconds,
    )
    return {"recording_id": recording_id, "size": size}


@router.post("/{device_id}/record/stop")
def stop_record(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    recording_id = recorder.stop(int(device.id), wait=True, timeout=180)
    if recording_id is None:
        raise HTTPException(404, "该设备当前没有在录屏")
    return {"recording_id": recording_id}


@router.get("/{device_id}/vnc")
def vnc_info(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    return {
        "novnc_port": device.novnc_port,
        "password": device.vnc_password,
        "path": f"/vnc.html?autoconnect=1&resize=scale&password={device.vnc_password or ''}",
        "hint": "用浏览器打开 http://<部署机IP>:<novnc_port>/vnc.html",
    }
