from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from sqlmodel import Session, select

from ..config import settings
from ..models import Device, DeviceStatus, ProxyProfile, utcnow
from . import events
from .android import drop_device, get_device
from .docker_manager import DockerError, get_docker
from .recorder import recorder

log = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    pass


# ── 端口分配 ──────────────────────────────────────────────────────────────
def allocate_ports(session: Session, *, count: int = 2) -> list[int]:
    used: set[int] = set()
    for dev in session.exec(select(Device)).all():
        for p in (dev.adb_port, dev.novnc_port):
            if p:
                used.add(p)
    try:
        used |= get_docker().used_host_ports()
    except DockerError as exc:
        log.warning("读取 docker 端口占用失败: %s", exc)

    out: list[int] = []
    for port in range(settings.device_port_base, settings.device_port_max + 1):
        if port not in used:
            out.append(port)
            used.add(port)
            if len(out) == count:
                return out
    raise DeviceError(
        f"端口区间 {settings.device_port_base}-{settings.device_port_max} 已用尽，请调大 DEVICE_PORT_MAX"
    )


def _proxy_url(session: Session, proxy_id: Optional[int]) -> Optional[str]:
    if not proxy_id:
        return None
    proxy = session.get(ProxyProfile, proxy_id)
    if proxy is None:
        raise DeviceError(f"代理不存在: id={proxy_id}")
    if not proxy.enabled:
        raise DeviceError(f"代理已禁用: {proxy.name}")
    return proxy.url()


# ── 生命周期 ──────────────────────────────────────────────────────────────
def create_device(
    session: Session,
    *,
    name: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    dpi: Optional[int] = None,
    proxy_id: Optional[int] = None,
    android_image: Optional[str] = None,
    vnc_password: Optional[str] = None,
    autostart: bool = True,
) -> Device:
    adb_port, novnc_port = allocate_ports(session, count=2)
    device = Device(
        name=name,
        width=width or settings.device_width,
        height=height or settings.device_height,
        dpi=dpi or settings.device_dpi,
        proxy_id=proxy_id,
        android_image=android_image or settings.redroid_image,
        adb_port=adb_port,
        novnc_port=novnc_port,
        vnc_password=vnc_password if vnc_password is not None else secrets.token_urlsafe(9),
        status=DeviceStatus.created,
    )
    session.add(device)
    session.commit()
    session.refresh(device)

    names = get_docker().names(int(device.id))
    device.gw_container = names.gw
    device.android_container = names.android
    device.vnc_container = names.vnc
    device.data_volume = names.volume
    session.add(device)
    session.commit()
    session.refresh(device)

    events.emit(f"新建设备 {device.name}（adb:{adb_port} novnc:{novnc_port}）", device_id=device.id)

    if autostart:
        start_device(session, device)
    return device


def start_device(session: Session, device: Device) -> Device:
    docker = get_docker()
    images = docker.ensure_images()
    missing = [k for k, ok in images.items() if not ok]
    if missing:
        hint = {
            "gateway": "make build-gateway",
            "vnc": "make build-vnc",
            "android": "make pull-android",
        }
        raise DeviceError(
            "缺少镜像: " + ", ".join(f"{m}（{hint.get(m, '')}）" for m in missing)
        )

    proxy_url = _proxy_url(session, device.proxy_id)

    device.status = DeviceStatus.starting
    device.last_error = None
    device.updated_at = utcnow()
    session.add(device)
    session.commit()

    try:
        names = docker.start_stack(
            device_id=int(device.id),
            width=device.width,
            height=device.height,
            dpi=device.dpi,
            adb_port=int(device.adb_port),
            novnc_port=int(device.novnc_port),
            proxy_url=proxy_url,
            android_image=device.android_image,
            vnc_password=device.vnc_password,
        )
    except Exception as exc:
        device.status = DeviceStatus.error
        device.last_error = str(exc)[:1000]
        device.updated_at = utcnow()
        session.add(device)
        session.commit()
        events.emit(f"设备启动失败: {exc}", level="error", device_id=device.id)
        raise DeviceError(str(exc)) from exc

    device.gw_container = names["gw"]
    device.android_container = names["android"]
    device.vnc_container = names["vnc"]
    device.data_volume = names["volume"]
    device.status = DeviceStatus.running
    device.booted_at = None
    device.updated_at = utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)

    events.emit(
        f"设备 {device.name} 已启动，安卓首次启动通常需要 1-3 分钟"
        + (f"，走代理 {proxy_url.split('@')[-1]}" if proxy_url else "，直连出网"),
        device_id=device.id,
    )
    return device


def stop_device(session: Session, device: Device) -> Device:
    if device.id is not None:
        recorder.stop(int(device.id), wait=False)
    try:
        get_docker().stop_stack(int(device.id))
    except DockerError as exc:
        log.warning("停止设备容器失败: %s", exc)
    drop_device(device.adb_addr)
    device.status = DeviceStatus.stopped
    device.updated_at = utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    events.emit(f"设备 {device.name} 已停止", device_id=device.id)
    return device


def restart_device(session: Session, device: Device) -> Device:
    stop_device(session, device)
    return start_device(session, device)


def delete_device(session: Session, device: Device, *, purge_data: bool = False) -> None:
    device_id = int(device.id)
    if device_id is not None:
        recorder.stop(device_id, wait=False)
    try:
        get_docker().remove_stack(device_id, purge_data=purge_data)
    except DockerError as exc:
        log.warning("删除设备容器失败: %s", exc)
    drop_device(device.adb_addr)
    name = device.name
    session.delete(device)
    session.commit()
    events.emit(f"设备 {name} 已删除" + ("（含数据卷）" if purge_data else ""), device_id=device_id)


# ── 状态 ──────────────────────────────────────────────────────────────────
def sync_status(session: Session, device: Device) -> dict[str, Any]:
    """把容器实际状态同步回数据库，返回给前端的详细信息。"""
    docker = get_docker()
    try:
        containers = docker.stack_status(int(device.id))
    except DockerError as exc:
        containers = {"error": str(exc)}

    android_state = containers.get("android")
    if android_state == "running" and device.status != DeviceStatus.running:
        device.status = DeviceStatus.running
    elif android_state in {"exited", "dead"} and device.status == DeviceStatus.running:
        device.status = DeviceStatus.stopped
    elif android_state is None and device.status in {DeviceStatus.running, DeviceStatus.starting}:
        device.status = DeviceStatus.stopped

    android: dict[str, Any] = {"state": "offline"}
    if android_state == "running":
        dev = get_device(device.adb_addr)
        android = dev.device_info()
        if android.get("booted") and device.booted_at is None:
            device.booted_at = utcnow()

    device.updated_at = utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)

    return {
        "containers": containers,
        "android": android,
        "recording": recorder.is_recording(int(device.id)),
    }


def refresh_egress(session: Session, device: Device) -> dict[str, Any]:
    info = get_docker().egress_ip(int(device.id))
    if "ip" in info:
        device.egress_ip = info.get("ip")
        device.egress_region = " / ".join(x for x in (info.get("country"), info.get("city")) if x) or None
        session.add(device)
        session.commit()
        session.refresh(device)
    return info


def install_apk(device: Device, apk_path: str) -> str:
    dev = get_device(device.adb_addr)
    if not dev.is_booted():
        raise DeviceError("安卓还没启动完成，稍等 1-2 分钟再试")
    out = dev.install_apk(apk_path)
    events.emit(f"已安装 APK {apk_path.rsplit('/', 1)[-1]}", device_id=device.id)
    return out


def pick_ready_device(session: Session, device_id: Optional[int] = None) -> Device:
    """任务执行时挑一台可用设备：指定优先，否则取第一台 running 的。"""
    if device_id:
        device = session.get(Device, device_id)
        if device is None:
            raise DeviceError(f"设备不存在: id={device_id}")
        if device.status != DeviceStatus.running:
            raise DeviceError(f"设备 {device.name} 当前状态为 {device.status}，不可用")
        return device

    devices = session.exec(select(Device).where(Device.status == DeviceStatus.running)).all()
    if not devices:
        raise DeviceError("没有处于运行状态的设备")
    return devices[0]
