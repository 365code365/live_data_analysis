from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlmodel import Session, select

from ..config import settings
from ..core import apps, collector, device_service, events, host
from ..core.android import get_device
from ..core.device_service import DeviceError
from ..core.docker_manager import DockerError, get_docker
from ..core.recorder import recorder
from ..db import get_session
from ..models import Device, ProxyProfile
from .deps import require_admin
from ..schemas import (
    AppInstall,
    DeeplinkAction,
    DeviceCreate,
    DeviceUpdate,
    Ok,
    PasteText,
    RecordStart,
    RotateAction,
    ScreenReport,
    ShellCommand,
    SwipeAction,
    TapAction,
    VolumeAction,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


def _get(session: Session, device_id: int) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "设备不存在")
    return device


def _container_states() -> dict[str, dict[str, str]]:
    """一次 docker 调用拿到全部设备容器的真实状态：{device_id: {role: state}}。"""
    out: dict[str, dict[str, str]] = {}
    try:
        for c in get_docker().list_managed():
            did, role = c.get("device_id"), c.get("role")
            if did and role:
                out.setdefault(did, {})[role] = c["status"]
    except DockerError as exc:
        log.warning("读取容器状态失败: %s", exc)
    return out


def _out(session: Session, device: Device, states: Optional[dict[str, dict[str, str]]] = None) -> dict[str, Any]:
    proxy = session.get(ProxyProfile, device.proxy_id) if device.proxy_id else None
    cstate = (states or {}).get(str(device.id), {}) if states is not None else None
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
        "memory_mb": device.memory_mb,
        "cpu_limit": device.cpu_limit,
        "entitlement_id": device.entitlement_id,
        "adb_port": device.adb_port,
        "novnc_port": device.novnc_port,
        "audio_port": device.audio_port,
        "enable_audio": device.enable_audio,
        "adb_addr": device.adb_addr,
        "vnc_password": device.vnc_password,
        "egress_ip": device.egress_ip,
        "egress_region": device.egress_region,
        "containers": {
            "gw": device.gw_container,
            "android": device.android_container,
            "vnc": device.vnc_container,
        },
        # 容器真实状态；None 表示本次没查
        "container_states": cstate,
        "android_running": (cstate or {}).get("android") == "running" if cstate is not None else None,
        "vnc_running": (cstate or {}).get("vnc") == "running" if cstate is not None else None,
        # 画面能不能连：安卓和画面容器都得在跑
        "screen_ready": (
            (cstate or {}).get("android") == "running" and (cstate or {}).get("vnc") == "running"
            if cstate is not None
            else None
        ),
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
    states = _container_states()
    return [_out(session, d, states) for d in devices]


@router.post("", status_code=201)
def create_device(payload: DeviceCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        device = device_service.create_device(session, **payload.model_dump())
    except DeviceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _out(session, device)


@router.get("/{device_id}")
def get_device_detail(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    # 详情也要带容器真实状态：设备控制台靠它判断画面能不能连，
    # 之前只有列表接口有，控制台就永远显示「设备还没准备好」。
    return _out(session, device, _container_states())


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
    if {
        "proxy_id", "width", "height", "dpi", "android_image",
        "vnc_password", "enable_audio", "memory_mb", "cpu_limit",
    } & set(changes):
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


@router.get("/{device_id}/logs", dependencies=[Depends(require_admin)])
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
@router.get("/{device_id}/apps")
def list_device_apps(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """设备上已安装的第三方应用 + 当前安装任务进度。"""
    device = _get(session, device_id)
    try:
        installed = apps.installed_apps(device.adb_addr)
    except Exception as exc:
        raise HTTPException(409, _offline_reason(device) if not get_device(device.adb_addr).is_online() else str(exc)) from exc
    return {"items": installed, "job": apps.app_jobs.get(device_id)}


@router.post("/{device_id}/apps/install")
def install_app(device_id: int, payload: AppInstall, session: Session = Depends(get_session)) -> dict[str, Any]:
    """三种来源：应用目录(catalog) / 已上传的本地包(local) / 任意直链(url)。"""
    device = _get(session, device_id)
    source = payload.source
    url = payload.url
    filename = payload.filename
    name = payload.filename or payload.url or ""

    if source == "catalog":
        entry = apps.catalog_entry(payload.key or "")
        if entry is None:
            raise HTTPException(404, f"应用目录里没有 {payload.key}")
        if not entry["url"]:
            raise HTTPException(
                400,
                f"「{entry['name']}」没有配置直链。{entry['note'] or ''} "
                f"官方下载页: {entry['page'] or '无'}",
            )
        source, url, name = "url", entry["url"], entry["name"]
    elif source == "url":
        if not url:
            raise HTTPException(400, "缺少 url")
    elif source == "local":
        if not filename:
            raise HTTPException(400, "缺少 filename")
    else:
        raise HTTPException(400, f"未知来源: {source}")

    try:
        return apps.app_jobs.start(
            device_id=device_id,
            addr=device.adb_addr,
            source=source,
            name=name or "apk",
            url=url,
            filename=filename,
            keep_file=payload.keep_file,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{device_id}/apps/job")
def app_job(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    _get(session, device_id)
    return {"job": apps.app_jobs.get(device_id)}


@router.delete("/{device_id}/apps/{package}")
def uninstall_app(device_id: int, package: str, session: Session = Depends(get_session)) -> Ok:
    device = _get(session, device_id)
    try:
        out = apps.uninstall_app(device.adb_addr, package)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return Ok(message=out.strip()[:200] or "已卸载")


@router.post("/{device_id}/launch/{package}")
def launch_app(device_id: int, package: str, session: Session = Depends(get_session)) -> Ok:
    device = _get(session, device_id)
    get_device(device.adb_addr).start_app(package)
    return Ok(message=f"已启动 {package}")


# ── 交互 ──────────────────────────────────────────────────────────────────
@router.post("/{device_id}/shell", dependencies=[Depends(require_admin)])
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


@router.post("/{device_id}/deeplink", dependencies=[Depends(require_admin)])
def open_deeplink(device_id: int, payload: DeeplinkAction, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    out = get_device(device.adb_addr).open_deeplink(payload.uri, payload.package)
    return {"output": out}


def _offline_reason(device: Device) -> str:
    """安卓不在线时，给出能直接照着做的原因说明，而不是干巴巴的 device offline。"""
    caps = host.capabilities()
    states = _container_states().get(str(device.id), {})
    android = states.get("android")

    if not caps["android_supported"]:
        return (
            "安卓容器无法在当前宿主运行：内核缺少 binder。\n"
            + host.BINDER_HELP
        )
    if android is None:
        return "安卓容器不存在，请先启动设备。"
    if android != "running":
        logs = get_docker().logs(device.android_container or "", tail=20).strip()
        return (
            f"安卓容器当前状态为 {android}，不是 running。\n"
            + (f"容器日志：\n{logs[:600]}" if logs else "容器没有日志输出。")
        )
    return (
        "安卓容器在跑，但 adb 还连不上。首次开机需要 1-3 分钟，"
        "稍等后重试；一直如此就看安卓容器日志。"
    )


@router.post("/{device_id}/paste")
def paste_text(device_id: int, payload: PasteText, session: Session = Depends(get_session)) -> dict[str, Any]:
    """把浏览器里的文本送进安卓（支持中文，多级回退）。"""
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_online():
        raise HTTPException(409, _offline_reason(device))
    result = dev.paste_text(payload.text, submit=payload.submit)
    if not result.get("ok"):
        raise HTTPException(422, result.get("error") or "文本注入失败")
    return result


@router.get("/{device_id}/volume")
def get_volume(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_online():
        raise HTTPException(409, _offline_reason(device))
    return dev.volume_info()


@router.post("/{device_id}/volume")
def set_volume(device_id: int, payload: VolumeAction, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_online():
        raise HTTPException(409, _offline_reason(device))
    if payload.action == "set":
        if payload.value is None:
            raise HTTPException(400, "action=set 时必须给 value")
        return dev.set_volume(payload.value)
    return dev.volume_step(payload.action)


@router.post("/{device_id}/display/keep-awake")
def keep_awake(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """把屏幕设成常亮（息屏时投屏是全黑的）。"""
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_online():
        raise HTTPException(409, _offline_reason(device))
    return dev.prepare_display()


@router.post("/{device_id}/rotate")
def rotate(device_id: int, payload: RotateAction, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_online():
        raise HTTPException(409, _offline_reason(device))
    return dev.rotate(payload.orientation)


@router.get("/{device_id}/screenshot")
def screenshot(device_id: int, session: Session = Depends(get_session)) -> Response:
    device = _get(session, device_id)
    dev = get_device(device.adb_addr)
    if not dev.is_online():
        raise HTTPException(409, _offline_reason(device))
    tmp = settings.screenshots_dir / f"_live_{device_id}.png"
    try:
        dev.screenshot(tmp)
        data = tmp.read_bytes()
    except Exception as exc:
        raise HTTPException(400, f"截图失败: {exc}") from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{device_id}/ui", dependencies=[Depends(require_admin)])
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


@router.post("/{device_id}/screen-report")
def screen_report(device_id: int, payload: ScreenReport, session: Session = Depends(get_session)) -> Ok:
    """浏览器把投屏连接状态回报上来，写进事件流。

    「画面不稳定」这类问题只看服务端日志是断不了案的：分不清是容器侧断链、
    还是浏览器整页重载。这里记录状态与页面实例，事后一看就知道。
    """
    device = _get(session, device_id)
    level = {"connected": "info", "connecting": "debug", "resized": "info"}.get(payload.state, "warning")
    if payload.state in {"auth_failed", "auth_required", "error"}:
        level = "error"
    events.emit(
        f"投屏状态 {payload.state}"
        + (f"（{payload.detail}）" if payload.detail else "")
        + (f" 页面重载 {payload.reloads} 次" if payload.reloads else ""),
        level=level,
        source="screen",
        device_id=device.id,
    )
    return Ok()


@router.get("/{device_id}/vnc")
def vnc_info(device_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    device = _get(session, device_id)
    states = _container_states().get(str(device.id), {})
    vnc_state = states.get("vnc")
    ready = vnc_state == "running"
    problem = None
    if not ready:
        problem = (
            f"画面容器状态为 {vnc_state or '不存在'}，noVNC 端口上没有服务，"
            "浏览器会直接报连接被重置。先把设备启动起来。"
        )
    elif states.get("android") != "running":
        problem = (
            "画面容器在跑，但安卓容器没跑起来，noVNC 里只会看到一块黑屏（scrcpy 连不上设备）。\n"
            + _offline_reason(device)
        )
    return {
        "novnc_port": device.novnc_port,
        "password": device.vnc_password,
        "path": f"/vnc.html?autoconnect=1&resize=scale&password={device.vnc_password or ''}",
        "ready": ready and states.get("android") == "running",
        "container_states": states,
        "problem": problem,
        "hint": "用浏览器打开 http://<部署机IP>:<novnc_port>/vnc.html",
    }
