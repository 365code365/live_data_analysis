from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from ..config import settings
from ..core.recorder import recorder
from ..db import get_session
from ..models import Recording
from ..schemas import Ok

log = logging.getLogger(__name__)
router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.get("")
def list_recordings(
    task_id: Optional[int] = None,
    device_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Recording).order_by(Recording.id.desc())  # type: ignore[attr-defined]
    if task_id:
        stmt = stmt.where(Recording.task_id == task_id)
    if device_id:
        stmt = stmt.where(Recording.device_id == device_id)
    rows = session.exec(stmt.limit(limit)).all()
    active = recorder.active()
    return {
        "active": active,
        "items": [
            {
                "id": r.id,
                "task_id": r.task_id,
                "device_id": r.device_id,
                "status": r.status,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "duration_seconds": r.duration_seconds,
                "size_mb": round((r.size_bytes or 0) / 1048576, 1) if r.size_bytes else None,
                "segment_count": r.segment_count,
                "error": r.error,
                "downloadable": bool(r.file_path and Path(r.file_path).exists()),
            }
            for r in rows
        ],
    }


@router.get("/{recording_id}/download")
def download(recording_id: int, session: Session = Depends(get_session)) -> FileResponse:
    rec = session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(404, "录像不存在")
    if not rec.file_path:
        raise HTTPException(409, f"录像尚未生成（状态: {rec.status}）")
    path = Path(rec.file_path).resolve()
    if not str(path).startswith(str(settings.data_dir.resolve())):
        raise HTTPException(400, "路径越界")
    if not path.exists():
        raise HTTPException(404, "文件已被删除")
    return FileResponse(path, media_type="video/mp4", filename=f"recording_{recording_id}.mp4")


@router.delete("/{recording_id}")
def delete_recording(
    recording_id: int,
    delete_file: bool = Query(True),
    session: Session = Depends(get_session),
) -> Ok:
    rec = session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(404, "录像不存在")
    if delete_file and rec.file_path:
        path = Path(rec.file_path)
        if str(path.resolve()).startswith(str(settings.data_dir.resolve())):
            try:
                path.unlink(missing_ok=True)
                # 目录里只剩空壳时一并清掉
                parent = path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError as exc:
                log.warning("删除录像文件失败: %s", exc)
    session.delete(rec)
    session.commit()
    return Ok(message="已删除")


@router.get("/active")
def active_recordings() -> dict[str, Any]:
    return {"active": recorder.active()}
