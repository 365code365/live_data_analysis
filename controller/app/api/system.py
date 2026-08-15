from __future__ import annotations

import logging
import platform
import shutil
from typing import Any

from fastapi import APIRouter

from .. import __version__
from ..config import settings
from ..core import scheduler
from ..core.docker_manager import DockerError, get_docker
from ..platforms import list_adapters, reload_selectors
from ..schemas import Ok

log = logging.getLogger(__name__)
router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@router.get("/info")
def info() -> dict[str, Any]:
    docker_info: dict[str, Any]
    try:
        docker = get_docker()
        images = docker.ensure_images()
        containers = docker.list_managed()
        version = docker.client.version()
        docker_info = {
            "ok": True,
            "server": f"{version.get('Os')}/{version.get('Arch')} {version.get('Version')}",
            "images": images,
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


@router.get("/platforms")
def platforms() -> dict[str, Any]:
    return {"platforms": list_adapters()}


@router.post("/selectors/reload")
def reload() -> Ok:
    keys = reload_selectors()
    return Ok(message=f"已重载选择器配置: {', '.join(keys)}")


@router.get("/containers")
def containers() -> dict[str, Any]:
    try:
        return {"items": get_docker().list_managed()}
    except DockerError as exc:
        return {"items": [], "error": str(exc)}
