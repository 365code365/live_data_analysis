from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..core.docker_manager import DockerError, get_docker
from ..db import get_session
from ..models import Device, ProxyProfile, utcnow
from ..schemas import Ok, ProxyCreate, ProxyProbe, ProxyUpdate
from .deps import require_admin

log = logging.getLogger(__name__)

# 代理配置里带账号密码，属于后台内容，前台一律不可见
router = APIRouter(prefix="/proxies", tags=["proxies"], dependencies=[Depends(require_admin)])


def _out(proxy: ProxyProfile) -> dict[str, Any]:
    return {
        "id": proxy.id,
        "name": proxy.name,
        "scheme": proxy.scheme,
        "host": proxy.host,
        "port": proxy.port,
        "username": proxy.username,
        "has_password": bool(proxy.password),
        "remark": proxy.remark,
        "enabled": proxy.enabled,
        "url_masked": proxy.url(mask=True),
        "last_checked_at": proxy.last_checked_at,
        "last_egress_ip": proxy.last_egress_ip,
        "last_egress_region": proxy.last_egress_region,
        "last_status": proxy.last_status,
        "created_at": proxy.created_at,
    }


@router.get("")
def list_proxies(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    proxies = session.exec(select(ProxyProfile).order_by(ProxyProfile.id)).all()
    used = {d.proxy_id for d in session.exec(select(Device)).all() if d.proxy_id}
    return [{**_out(p), "in_use": p.id in used} for p in proxies]


@router.post("", status_code=201)
def create_proxy(payload: ProxyCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    proxy = ProxyProfile(**payload.model_dump())
    session.add(proxy)
    session.commit()
    session.refresh(proxy)
    return _out(proxy)


@router.patch("/{proxy_id}")
def update_proxy(proxy_id: int, payload: ProxyUpdate, session: Session = Depends(get_session)) -> dict[str, Any]:
    proxy = session.get(ProxyProfile, proxy_id)
    if proxy is None:
        raise HTTPException(404, "代理不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(proxy, key, value)
    session.add(proxy)
    session.commit()
    session.refresh(proxy)
    return _out(proxy)


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, session: Session = Depends(get_session)) -> Ok:
    proxy = session.get(ProxyProfile, proxy_id)
    if proxy is None:
        raise HTTPException(404, "代理不存在")
    bound = session.exec(select(Device).where(Device.proxy_id == proxy_id)).all()
    if bound:
        raise HTTPException(409, f"仍有设备在用该代理: {', '.join(d.name for d in bound)}")
    session.delete(proxy)
    session.commit()
    return Ok(message="已删除")


@router.post("/{proxy_id}/test")
def test_proxy(proxy_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """起一个一次性网关容器验证代理，并把出口 IP 记回代理配置。"""
    proxy = session.get(ProxyProfile, proxy_id)
    if proxy is None:
        raise HTTPException(404, "代理不存在")
    try:
        result = get_docker().probe_proxy(proxy.url())
    except DockerError as exc:
        raise HTTPException(500, str(exc)) from exc

    proxy.last_checked_at = utcnow()
    if result.get("ok"):
        proxy.last_egress_ip = result.get("ip")
        proxy.last_egress_region = " / ".join(x for x in (result.get("country"), result.get("city")) if x) or None
        proxy.last_status = "ok"
    else:
        proxy.last_status = f"failed: {str(result.get('error'))[:180]}"
    session.add(proxy)
    session.commit()
    return result


@router.post("/probe")
def probe_proxy(payload: ProxyProbe) -> dict[str, Any]:
    url = payload.url
    if not url:
        if not payload.host or not payload.port:
            raise HTTPException(400, "需要提供 url，或 host + port")
        auth = ""
        if payload.username:
            auth = f"{quote(payload.username, safe='')}:{quote(payload.password or '', safe='')}@"
        url = f"{payload.scheme}://{auth}{payload.host}:{payload.port}"
    try:
        return get_docker().probe_proxy(url)
    except DockerError as exc:
        raise HTTPException(500, str(exc)) from exc
