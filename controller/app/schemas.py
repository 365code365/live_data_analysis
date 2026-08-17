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
    enable_audio: bool = True
    plan_id: Optional[int] = Field(default=None, description="按套餐规格创建（会校验权益）")
    autostart: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    proxy_id: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    dpi: Optional[int] = None
    memory_mb: Optional[int] = Field(default=None, ge=0, le=65536)
    cpu_limit: Optional[float] = Field(default=None, ge=0, le=64)
    android_image: Optional[str] = None
    vnc_password: Optional[str] = None
    enable_audio: Optional[bool] = None


class AppInstall(BaseModel):
    """安装来源三选一：应用目录 / 已上传的本地包 / 任意直链。"""

    source: str = Field(default="local", pattern="^(catalog|local|url)$")
    key: Optional[str] = Field(default=None, description="source=catalog 时的应用 key")
    filename: Optional[str] = Field(default=None, description="source=local 时 apks/ 下的文件名")
    url: Optional[str] = Field(default=None, description="source=url 时的 apk 直链")
    keep_file: bool = Field(default=True, description="安装完是否保留 apks/ 里的文件")


class PasteText(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    submit: bool = Field(default=False, description="粘贴后是否回车")


class VolumeAction(BaseModel):
    action: str = Field(default="set", pattern="^(set|up|down|mute)$")
    value: Optional[int] = Field(default=None, ge=0, le=25)


class RotateAction(BaseModel):
    orientation: Optional[int] = Field(default=None, ge=0, le=3, description="0/1/2/3；留空则竖横切换")


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


# ── 计费 ──────────────────────────────────────────────────────────────────
class PlanCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    description: Optional[str] = None
    badge: Optional[str] = Field(default=None, max_length=10)

    width: int = Field(default=720, ge=320, le=2160)
    height: int = Field(default=1280, ge=480, le=3840)
    dpi: int = Field(default=320, ge=120, le=640)
    cpu_limit: float = Field(default=0, ge=0, le=64)
    memory_mb: int = Field(default=0, ge=0, le=65536)
    max_devices: int = Field(default=1, ge=1, le=200)
    max_tasks: int = Field(default=5, ge=1, le=2000)
    allow_proxy: bool = True
    allow_recording: bool = True
    allow_audio: bool = True

    duration_days: int = Field(default=30, ge=1, le=3650)
    price_cents: int = Field(default=0, ge=0, le=100_000_000)
    original_price_cents: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    currency: str = "CNY"
    sort_order: int = 0
    enabled: bool = True


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    badge: Optional[str] = None
    width: Optional[int] = Field(default=None, ge=320, le=2160)
    height: Optional[int] = Field(default=None, ge=480, le=3840)
    dpi: Optional[int] = Field(default=None, ge=120, le=640)
    cpu_limit: Optional[float] = Field(default=None, ge=0, le=64)
    memory_mb: Optional[int] = Field(default=None, ge=0, le=65536)
    max_devices: Optional[int] = Field(default=None, ge=1, le=200)
    max_tasks: Optional[int] = Field(default=None, ge=1, le=2000)
    allow_proxy: Optional[bool] = None
    allow_recording: Optional[bool] = None
    allow_audio: Optional[bool] = None
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    price_cents: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    original_price_cents: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    sort_order: Optional[int] = None
    enabled: Optional[bool] = None


class OrderCreate(BaseModel):
    plan_id: int
    channel: str = Field(default="mock", pattern="^(alipay|wechat|mock)$")
    remark: Optional[str] = Field(default=None, max_length=200)
