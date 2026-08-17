from __future__ import annotations

import logging
import platform
import shutil
from typing import Any

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import __version__
from ..config import settings
from ..core import host, scheduler
from ..core.docker_manager import DockerError, get_docker
from ..core.images import images_overview, puller
from ..platforms import list_adapters, reload_selectors
from ..schemas import Ok
from . import deps
from .deps import require_admin

log = logging.getLogger(__name__)
router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@router.get("/auth")
def auth_state(x_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """前端据此决定是否显示后台入口、要不要弹登录框。"""
    return {
        "admin_required": deps.admin_required(),
        "admin_ok": deps.check_admin_token(x_admin_token),
        "version": __version__,
    }


@router.get("/info", dependencies=[Depends(require_admin)])
def info() -> dict[str, Any]:
    docker_info: dict[str, Any]
    try:
        docker = get_docker()
        containers = docker.list_managed()
        version = docker.client.version()
        overview = images_overview()
        docker_info = {
            "ok": True,
            "server": f"{version.get('Os')}/{version.get('Arch')} {version.get('Version')}",
            "images": {i["target"]: i["ready"] for i in overview},
            "image_details": overview,
            "managed_containers": containers,
        }
    except DockerError as exc:
        docker_info = {"ok": False, "error": str(exc)}

    return {
        "version": __version__,
        "python": platform.python_version(),
        "tools": {
            "adb": bool(shutil.which("adb")),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe")),
        },
        "settings": {
            "redroid_image": settings.redroid_image,
            "gateway_image": settings.gateway_image,
            "vnc_image": settings.vnc_image,
            "docker_network": settings.docker_network,
            "device_port_range": [settings.device_port_base, settings.device_port_max],
            "default_interval_seconds": settings.default_interval_seconds,
            "max_concurrent_tasks": settings.max_concurrent_tasks,
            "record_segment_seconds": settings.record_segment_seconds,
            "record_bitrate": settings.record_bitrate,
            "selectors_dir": settings.selectors_dir or None,
            "data_dir": str(settings.data_dir),
        },
        "docker": docker_info,
        "scheduler": {"running": scheduler.get_scheduler().running, "jobs": scheduler.jobs()},
    }


@router.get("/host-check", dependencies=[Depends(require_admin)])
def host_check() -> dict[str, Any]:
    """宿主内核能力：binder（安卓容器必需）、tun（代理网关必需）。"""
    return host.capabilities()


@router.get("/platforms", dependencies=[Depends(require_admin)])
def platforms() -> dict[str, Any]:
    return {"platforms": list_adapters()}


@router.post("/selectors/reload", dependencies=[Depends(require_admin)])
def reload() -> Ok:
    keys = reload_selectors()
    return Ok(message=f"已重载选择器配置: {', '.join(keys)}")


@router.get("/images", dependencies=[Depends(require_admin)])
def images() -> dict[str, Any]:
    """镜像就绪情况 + 正在进行的拉取任务。"""
    try:
        return {"items": images_overview()}
    except DockerError as exc:
        return {"items": [], "error": str(exc)}


@router.post("/images/{target}/pull", dependencies=[Depends(require_admin)])
def pull_image(target: str) -> dict[str, Any]:
    """在控制台里直接拉镜像（目前只有安卓镜像可拉，网关/VNC 是本地构建）。"""
    try:
        return puller.start(target)
    except DockerError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, f"未知镜像目标: {target}") from exc


@router.get("/images/pulls", dependencies=[Depends(require_admin)])
def pull_status() -> dict[str, Any]:
    return {"jobs": puller.snapshot()}


@router.get("/containers", dependencies=[Depends(require_admin)])
def containers() -> dict[str, Any]:
    try:
        return {"items": get_docker().list_managed()}
    except DockerError as exc:
        return {"items": [], "error": str(exc)}
