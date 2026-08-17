from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Optional

from sqlmodel import Session, select

from .. import catalogs
from ..config import settings
from ..models import Device, DeviceStatus, ProxyProfile, utcnow
from . import billing, events
from . import host
from .android import drop_device, get_device
from .docker_manager import DockerError, get_docker
from .recorder import recorder

log = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    pass


# ── 端口分配 ──────────────────────────────────────────────────────────────
def allocate_ports(session: Session, *, count: int = 3) -> list[int]:
    used: set[int] = set()
    for dev in session.exec(select(Device)).all():
        for p in (dev.adb_port, dev.novnc_port, dev.audio_port):
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
    enable_audio: bool = True,
    plan_id: Optional[int] = None,
    perf: Optional[str] = None,
    screen: Optional[str] = None,
    disk_gb: Optional[int] = None,
    autostart: bool = True,
) -> Device:
    # ── 档位 → 具体参数 ───────────────────────────────────────────────
    # 优先级：套餐规格 > 显式传的宽高 > 选中的档位 > 全局默认
    memory_mb, cpu_limit = 0, 0.0
    disk = catalogs.valid_disk(disk_gb)
    tier = catalogs.performance(perf)
    if tier:
        memory_mb = int(tier["memory_mb"])
        cpu_limit = float(tier["cpu_limit"])
        if not disk:
            disk = catalogs.valid_disk(tier["disk_gb"])
    preset = catalogs.screen(screen)
    if preset:
        width = width or int(preset["width"])
        height = height or int(preset["height"])
        dpi = dpi or int(preset["dpi"])

    # ── 计费：按套餐规格开机并占用权益名额 ────────────────────────────
    entitlement_id: Optional[int] = None
    if settings.billing_enabled and (plan_id or settings.billing_enforce):
        try:
            ent = billing.pick_entitlement(session, plan_id)
        except billing.BillingError as exc:
            raise DeviceError(str(exc)) from exc
        entitlement_id = ent.id
        spec = json.loads(ent.spec_snapshot) if ent.spec_snapshot else {}
        # 规格以套餐为准，避免用低价套餐开高配实例
        width = int(spec.get("width") or width or settings.device_width)
        height = int(spec.get("height") or height or settings.device_height)
        dpi = int(spec.get("dpi") or dpi or settings.device_dpi)
        memory_mb = int(spec.get("memory_mb") or 0)
        cpu_limit = float(spec.get("cpu_limit") or 0)
        if not spec.get("allow_audio", True):
            enable_audio = False
        if proxy_id and not spec.get("allow_proxy", True):
            raise DeviceError(f"套餐「{ent.plan_name}」不含独立出口 IP，请升级套餐或不要绑定代理")

    adb_port, novnc_port, audio_port = allocate_ports(session, count=3)
    device = Device(
        name=name,
        width=width or settings.device_width,
        height=height or settings.device_height,
        dpi=dpi or settings.device_dpi,
        proxy_id=proxy_id,
        android_image=android_image or settings.redroid_image,
        adb_port=adb_port,
        novnc_port=novnc_port,
        audio_port=audio_port,
        enable_audio=enable_audio,
        entitlement_id=entitlement_id,
        memory_mb=memory_mb,
        cpu_limit=cpu_limit,
        disk_gb=disk,
        perf_code=perf if tier else None,
        screen_code=screen if preset else None,
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

    # 先做内核能力预检：binder 缺失时 redroid 会秒退并无限重启，
    # 与其让用户对着 "Restarting (129)" 猜，不如直接说清楚。
    caps = host.capabilities()
    if not caps["android_supported"]:
        device.status = DeviceStatus.error
        device.last_error = host.BINDER_HELP[:1000]
        device.updated_at = utcnow()
        session.add(device)
        session.commit()
        raise DeviceError(host.BINDER_HELP)

    # 老设备记录没有音频端口，补一个（升级前建的设备也能听声音）
    if not device.audio_port:
        device.audio_port = allocate_ports(session, count=1)[0]
        session.add(device)
        session.commit()

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
            audio_port=device.audio_port,
            proxy_url=proxy_url,
            android_image=device.android_image,
            vnc_password=device.vnc_password,
            enable_audio=device.enable_audio,
            memory_mb=device.memory_mb,
            cpu_limit=device.cpu_limit,
            disk_gb=device.disk_gb,
        )
    except Exception as exc:
        device.status = DeviceStatus.error
        device.last_error = str(exc)[:1000]
        device.updated_at = utcnow()
        session.add(device)
        session.commit()
        events.emit(f"设备启动失败: {exc}", level="error", device_id=device.id)
        raise DeviceError(str(exc)) from exc

    # 安卓容器起不来时会秒退，等几秒确认它真的活着再报成功
    problem = _verify_android_alive(int(device.id))
    if problem:
        try:
            get_docker().remove_stack(int(device.id))
        except DockerError:
            pass
        device.status = DeviceStatus.error
        device.last_error = problem[:1000]
        device.updated_at = utcnow()
        session.add(device)
        session.commit()
        events.emit(f"设备启动失败: {problem[:200]}", level="error", device_id=device.id)
        raise DeviceError(problem)

    device.gw_container = names["gw"]
    device.android_container = names["android"]
    device.vnc_container = names["vnc"]
    device.data_volume = names["volume"]
    # 卷是第一次创建时才可能带上配额，这里如实记下来给界面显示
    if names.get("disk_quota"):
        device.disk_quota = True
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


def _verify_android_alive(device_id: int, wait_seconds: float = 8.0) -> Optional[str]:
    """安卓容器活着返回 None，否则返回带日志的失败原因。"""
    docker = get_docker()
    names = docker.names(device_id)
    deadline = time.time() + wait_seconds
    state: Optional[str] = None
    while time.time() < deadline:
        state = docker.stack_status(device_id).get("android")
        if state in {"exited", "dead", "restarting"}:
            break
        time.sleep(1.5)

    if state not in {"exited", "dead", "restarting"}:
        return None

    logs = docker.logs(names.android, tail=30).strip()
    reason = f"安卓容器启动后立即退出（状态 {state}）"
    if not logs:
        reason += "，且没有任何日志输出。"
        if not host.capabilities()["binder"]:
            reason += "\n" + host.BINDER_HELP
        else:
            reason += "\n常见原因：宿主 binder 不可用、镜像架构与宿主不匹配（arm64 宿主要用 *_64only 镜像）。"
    else:
        reason += f"\n容器日志：\n{logs[:800]}"
    return reason


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
            # 开机后立刻把屏幕设成常亮，否则息屏后投屏就是一片黑
            try:
                android["display"] = dev.prepare_display()
                events.emit("已设置屏幕常亮", source="device", device_id=device.id)
            except Exception as exc:
                log.warning("设置屏幕常亮失败 device=%s: %s", device.id, exc)
        elif android.get("booted") and not dev.screen_on():
            # 运行期被误关屏（比如手动按了电源键）也自动救回来
            try:
                dev.prepare_display()
            except Exception as exc:
                log.debug("重新点亮屏幕失败: %s", exc)

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
