from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile

from ..config import settings
from ..core import apps
from ..schemas import Ok

log = logging.getLogger(__name__)
router = APIRouter(prefix="/apps", tags=["apps"])

MAX_APK_MB = 600


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    """应用目录：可一键下载安装的应用清单（可外部覆写）。"""
    items = apps.load_catalog()
    return {
        "items": items,
        "catalog_file": str(apps.catalog_path()),
        "categories": sorted({i["category"] for i in items}),
    }


@router.get("/local")
def local() -> dict[str, Any]:
    """apks/ 目录里已有的安装包。"""
    return {"items": apps.local_apks(), "dir": str(settings.apk_dir)}


@router.post("/upload")
async def upload(file: UploadFile) -> dict[str, Any]:
    """上传 apk 到 apks/ 目录，不立即安装（装到哪台设备由调用方决定）。"""
    name = apps.safe_filename(file.filename or "upload.apk")
    settings.apk_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.apk_dir / name
    size = 0
    try:
        with dest.open("wb") as fp:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_APK_MB * 1048576:
                    raise HTTPException(413, f"文件超过 {MAX_APK_MB}MB 上限")
                fp.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"写入失败（apks/ 是否只读？）: {exc}") from exc

    if size < 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "文件太小，不像是 apk")
    return {"filename": name, "size_mb": round(size / 1048576, 1)}


@router.delete("/local/{filename}")
def delete_local(filename: str) -> Ok:
    try:
        apps.delete_local(filename)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return Ok(message=f"已删除 {filename}")
