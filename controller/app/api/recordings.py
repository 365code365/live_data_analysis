from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
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


def _recording_file(recording_id: int, session: Session) -> Path:
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
    return path


@router.get("/{recording_id}/download")
def download(recording_id: int, session: Session = Depends(get_session)) -> FileResponse:
    path = _recording_file(recording_id, session)
    return FileResponse(path, media_type="video/mp4", filename=f"recording_{recording_id}.mp4")


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 512 * 1024


@router.get("/{recording_id}/stream")
def stream(
    recording_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """支持 Range 的视频流，浏览器 <video> 可以直接播放并拖动进度。

    FileResponse 不处理 Range，拖进度条会失败，所以这里自己实现 206。
    """
    path = _recording_file(recording_id, session)
    size = path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    common = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="recording_{recording_id}.mp4"',
    }

    if not range_header:
        def full():  # noqa: ANN202
            with path.open("rb") as fp:
                while chunk := fp.read(CHUNK):
                    yield chunk

        return StreamingResponse(
            full(),
            media_type="video/mp4",
            headers={**common, "Content-Length": str(size)},
        )

    m = RANGE_RE.search(range_header)
    if not m:
        raise HTTPException(416, "Range 格式不支持")
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        raise HTTPException(416, "Range 格式不支持")
    if start_s == "":                      # bytes=-N 取末尾 N 字节
        length = min(int(end_s), size)
        start, end = size - length, size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    end = min(end, size - 1)
    length = end - start + 1

    def partial():  # noqa: ANN202
        with path.open("rb") as fp:
            fp.seek(start)
            remain = length
            while remain > 0:
                chunk = fp.read(min(CHUNK, remain))
                if not chunk:
                    break
                remain -= len(chunk)
                yield chunk

    return StreamingResponse(
        partial(),
        status_code=206,
        media_type="video/mp4",
        headers={
            **common,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


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
