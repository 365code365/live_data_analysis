from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, func, select

from ..config import settings
from ..db import get_session
from ..models import Device, EventLog, LiveSnapshot, MonitorTask, ProductRecord, Recording

log = logging.getLogger(__name__)
router = APIRouter(tags=["data"])


# ── 直播间快照 ────────────────────────────────────────────────────────────
@router.get("/snapshots")
def list_snapshots(
    task_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(LiveSnapshot).order_by(LiveSnapshot.captured_at.desc())  # type: ignore[attr-defined]
    if task_id:
        stmt = stmt.where(LiveSnapshot.task_id == task_id)
    rows = session.exec(stmt.offset(offset).limit(limit)).all()
    total_stmt = select(func.count(LiveSnapshot.id))
    if task_id:
        total_stmt = total_stmt.where(LiveSnapshot.task_id == task_id)
    return {
        "total": session.exec(total_stmt).one(),
        "items": [
            {
                "id": s.id,
                "task_id": s.task_id,
                "device_id": s.device_id,
                "captured_at": s.captured_at,
                "is_live": s.is_live,
                "room_id": s.room_id,
                "room_title": s.room_title,
                "anchor_name": s.anchor_name,
                "viewer_count": s.viewer_count,
                "like_count": s.like_count,
                "follower_count": s.follower_count,
                "product_count": s.product_count,
                "screenshot_path": s.screenshot_path,
                "has_dump": bool(s.dump_path),
            }
            for s in rows
        ],
    }


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    snap = session.get(LiveSnapshot, snapshot_id)
    if snap is None:
        raise HTTPException(404, "快照不存在")
    products = session.exec(
        select(ProductRecord).where(ProductRecord.snapshot_id == snapshot_id).order_by(ProductRecord.position)
    ).all()
    return {
        "snapshot": snap,
        "comments": json.loads(snap.comments_json) if snap.comments_json else [],
        "raw": json.loads(snap.raw_json) if snap.raw_json else {},
        "products": products,
    }


# ── 商品 ──────────────────────────────────────────────────────────────────
@router.get("/products")
def list_products(
    task_id: Optional[int] = None,
    snapshot_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=2000),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(ProductRecord).order_by(ProductRecord.captured_at.desc(), ProductRecord.position)  # type: ignore[attr-defined]
    if task_id:
        stmt = stmt.where(ProductRecord.task_id == task_id)
    if snapshot_id:
        stmt = stmt.where(ProductRecord.snapshot_id == snapshot_id)
    rows = session.exec(stmt.limit(limit)).all()
    return {"items": rows}


@router.get("/products/latest")
def latest_products(task_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """某任务最近一次采集到的商品列表。"""
    snap = session.exec(
        select(LiveSnapshot)
        .where(LiveSnapshot.task_id == task_id)
        .order_by(LiveSnapshot.captured_at.desc())  # type: ignore[attr-defined]
        .limit(1)
    ).first()
    if snap is None:
        return {"snapshot": None, "items": []}
    rows = session.exec(
        select(ProductRecord).where(ProductRecord.snapshot_id == snap.id).order_by(ProductRecord.position)
    ).all()
    return {"snapshot": snap, "items": rows}


@router.get("/products/series")
def product_series(
    task_id: int,
    product_key: str,
    limit: int = Query(500, ge=1, le=5000),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """单个商品的价格 / 销量随时间变化，用于画曲线。"""
    rows = session.exec(
        select(ProductRecord)
        .where(ProductRecord.task_id == task_id, ProductRecord.product_key == product_key)
        .order_by(ProductRecord.captured_at)
        .limit(limit)
    ).all()
    return {
        "product_key": product_key,
        "title": rows[-1].title if rows else None,
        "points": [
            {
                "captured_at": r.captured_at,
                "price": r.price,
                "origin_price": r.origin_price,
                "position": r.position,
                "sales_text": r.sales_text,
                "stock_text": r.stock_text,
            }
            for r in rows
        ],
    }


@router.get("/products/keys")
def product_keys(task_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """某任务出现过的所有商品（去重），带出现次数与最近价格。"""
    rows = session.exec(
        select(
            ProductRecord.product_key,
            func.max(ProductRecord.title),
            func.count(ProductRecord.id),
            func.min(ProductRecord.price),
            func.max(ProductRecord.price),
            func.max(ProductRecord.captured_at),
        )
        .where(ProductRecord.task_id == task_id, ProductRecord.product_key.is_not(None))  # type: ignore[union-attr]
        .group_by(ProductRecord.product_key)
        .order_by(func.max(ProductRecord.captured_at).desc())
    ).all()
    return {
        "items": [
            {
                "product_key": r[0],
                "title": r[1],
                "samples": r[2],
                "min_price": r[3],
                "max_price": r[4],
                "last_seen": r[5],
            }
            for r in rows
        ]
    }


# ── 事件 / 概览 ───────────────────────────────────────────────────────────
@router.get("/events")
def list_events(
    limit: int = Query(100, ge=1, le=500),
    level: Optional[str] = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(EventLog).order_by(EventLog.id.desc())  # type: ignore[attr-defined]
    if level:
        stmt = stmt.where(EventLog.level == level)
    return {"items": session.exec(stmt.limit(limit)).all()}


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    def count(model) -> int:  # noqa: ANN001
        return session.exec(select(func.count(model.id))).one()

    running = session.exec(select(func.count(Device.id)).where(Device.status == "running")).one()
    enabled = session.exec(select(func.count(MonitorTask.id)).where(MonitorTask.enabled == True)).one()  # noqa: E712
    return {
        "devices": count(Device),
        "devices_running": running,
        "tasks": count(MonitorTask),
        "tasks_enabled": enabled,
        "snapshots": count(LiveSnapshot),
        "products": count(ProductRecord),
        "recordings": count(Recording),
    }


# ── 媒体文件 ──────────────────────────────────────────────────────────────
@router.get("/media")
def media(path: str = Query(..., description="data/ 目录内的文件路径")) -> FileResponse:
    """安全地读取 data/ 下的截图或 dump（防路径穿越）。"""
    root = settings.data_dir.resolve()
    target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(400, "路径越界")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "文件不存在")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".mp4": "video/mp4",
        ".xml": "application/xml",
    }.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(target, media_type=media_type)
