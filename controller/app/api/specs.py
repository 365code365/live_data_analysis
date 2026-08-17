from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import catalogs
from ..config import settings
from ..core import billing
from ..core.docker_manager import DockerError, get_docker
from ..db import get_session
from ..models import Device, Plan, ProxyProfile

log = logging.getLogger(__name__)
router = APIRouter(tags=["specs"])


def _regions(session: Session) -> list[dict[str, Any]]:
    """可选的出口 IP（按代理列出），只给用户看得着的信息。

    这是前台接口，绝不能带出 host/port/账号/密码 —— 那些是后台内容
    （/api/proxies 有 X-Admin-Token 守卫）。这里只给 id、名字、地区、可用性。
    """
    proxies = session.exec(
        select(ProxyProfile).where(ProxyProfile.enabled == True).order_by(ProxyProfile.id)  # noqa: E712
    ).all()
    used: dict[int, int] = {}
    for dev in session.exec(select(Device)).all():
        if dev.proxy_id:
            used[dev.proxy_id] = used.get(dev.proxy_id, 0) + 1
    out = []
    for p in proxies:
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "region": p.last_egress_region or None,
                "ip_masked": _mask_ip(p.last_egress_ip),
                "verified": p.last_status == "ok",
                "in_use": used.get(int(p.id), 0),
            }
        )
    return out


def _mask_ip(ip: str | None) -> str | None:
    """出口 IP 只给前两段，够用户区分线路，又不至于把资源清单直接抄走。"""
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return ip[: max(4, len(ip) // 2)] + "…"


@router.get("/specs")
def specs(session: Session = Depends(get_session)) -> dict[str, Any]:
    """新建云手机对话框需要的全部选项，一次取完。"""
    try:
        disk_quota = get_docker().disk_quota_supported()
    except DockerError:
        disk_quota = False

    plans = session.exec(
        select(Plan).where(Plan.enabled == True).order_by(Plan.sort_order, Plan.id)  # noqa: E712
    ).all()

    return {
        "performance": catalogs.PERFORMANCE_TIERS,
        "screens": catalogs.SCREEN_PRESETS,
        "disks": catalogs.DISK_OPTIONS,
        "defaults": {
            "perf": catalogs.DEFAULT_PERF,
            "screen": catalogs.DEFAULT_SCREEN,
            "width": settings.device_width,
            "height": settings.device_height,
            "dpi": settings.device_dpi,
        },
        # 宿主文件系统没开 project quota 时磁盘只是登记值，界面要如实标注
        "disk_quota_supported": disk_quota,
        "regions": _regions(session),
        "plans": [billing.plan_out(p) for p in plans],
        "quota": billing.quota_summary(session),
    }
