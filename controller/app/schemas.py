from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .models import Platform

ALLOWED_SCHEMES = {"socks5", "socks5h", "socks4", "http", "https"}


# ── 代理 ──────────────────────────────────────────────────────────────────
class ProxyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scheme: str = "socks5"
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    remark: Optional[str] = None
    enabled: bool = True

    @field_validator("scheme")
    @classmethod
    def _check_scheme(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ALLOWED_SCHEMES:
            raise ValueError(f"scheme 需为 {sorted(ALLOWED_SCHEMES)} 之一")
        return v


class ProxyUpdate(BaseModel):
    name: Optional[str] = None
    scheme: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    remark: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("scheme")
    @classmethod
    def _check_scheme(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.lower().strip()
        if v not in ALLOWED_SCHEMES:
            raise ValueError(f"scheme 需为 {sorted(ALLOWED_SCHEMES)} 之一")
        return v


class ProxyProbe(BaseModel):
    """临时验证一条代理，不必先入库。"""

    url: Optional[str] = None
    scheme: str = "socks5"
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


# ── 设备 ──────────────────────────────────────────────────────────────────
class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    width: Optional[int] = Field(default=None, ge=320, le=2160)
    height: Optional[int] = Field(default=None, ge=480, le=3840)
    dpi: Optional[int] = Field(default=None, ge=120, le=640)
    proxy_id: Optional[int] = None
    android_image: Optional[str] = None
    vnc_password: Optional[str] = None
    autostart: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    proxy_id: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    dpi: Optional[int] = None
    android_image: Optional[str] = None
    vnc_password: Optional[str] = None


class ApkInstall(BaseModel):
    filename: str = Field(description="apks/ 目录下的文件名")


class ShellCommand(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    timeout: float = 30.0


class TapAction(BaseModel):
    x: int
    y: int


class SwipeAction(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int = 400


class DeeplinkAction(BaseModel):
    uri: str
    package: Optional[str] = None


class RecordStart(BaseModel):
    task_id: Optional[int] = None
    bitrate: Optional[int] = None
    segment_seconds: Optional[int] = None
    size: Optional[str] = Field(default=None, description="如 720x1280，留空用设备分辨率")
    max_duration_seconds: Optional[int] = None


# ── 任务 ──────────────────────────────────────────────────────────────────
class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    platform: Platform
    target: str = Field(min_length=1, max_length=500)
    device_id: Optional[int] = None
    interval_seconds: int = Field(default=60, ge=10, le=86400)
    enabled: bool = True
    collect_products: bool = True
    collect_comments: bool = False
    record_video: bool = False
    keep_screenshot: bool = True


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    target: Optional[str] = None
    device_id: Optional[int] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10, le=86400)
    enabled: Optional[bool] = None
    collect_products: Optional[bool] = None
    collect_comments: Optional[bool] = None
    record_video: Optional[bool] = None
    keep_screenshot: Optional[bool] = None


# ── 通用 ──────────────────────────────────────────────────────────────────
class Ok(BaseModel):
    ok: bool = True
    message: str = ""
    data: Optional[Any] = None
