from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from urllib.parse import quote

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DeviceStatus(str, Enum):
    created = "created"      # 记录已建，容器未起
    starting = "starting"
    running = "running"
    stopped = "stopped"
    error = "error"


class Platform(str, Enum):
    douyin = "douyin"
    xiaohongshu = "xiaohongshu"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    partial = "partial"
    failed = "failed"


class RecordingStatus(str, Enum):
    recording = "recording"
    merging = "merging"
    done = "done"
    failed = "failed"


# ──────────────────────────────────────────────────────────────────────────
class ProxyProfile(SQLModel, table=True):
    """IP 代理配置。支持 socks5 / socks5h / http / https。"""

    __tablename__ = "proxy_profile"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    scheme: str = Field(default="socks5")
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    remark: Optional[str] = None
    enabled: bool = Field(default=True)

    last_checked_at: Optional[datetime] = None
    last_egress_ip: Optional[str] = None
    last_egress_region: Optional[str] = None
    last_status: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)

    def url(self, *, mask: bool = False) -> str:
        auth = ""
        if self.username:
            if mask:
                auth = f"{self.username}:***@"
            else:
                pwd = quote(self.password or "", safe="")
                auth = f"{quote(self.username, safe='')}:{pwd}@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


class Device(SQLModel, table=True):
    """一个安卓容器实例（含它的网关与画面容器）。"""

    __tablename__ = "device"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    status: DeviceStatus = Field(default=DeviceStatus.created, index=True)

    width: int = 720
    height: int = 1280
    dpi: int = 320
    android_image: Optional[str] = None

    proxy_id: Optional[int] = Field(default=None, foreign_key="proxy_profile.id", index=True)

    adb_port: Optional[int] = None       # 宿主机映射端口（外部调试用）
    novnc_port: Optional[int] = None     # 宿主机映射端口（浏览器看屏）
    vnc_password: Optional[str] = None

    gw_container: Optional[str] = None
    android_container: Optional[str] = None
    vnc_container: Optional[str] = None
    data_volume: Optional[str] = None

    egress_ip: Optional[str] = None
    egress_region: Optional[str] = None
    last_error: Optional[str] = None
    booted_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def adb_addr(self) -> str:
        """控制器访问 adb 的地址：直接走 docker 网络里的网关容器名。"""
        return f"{self.gw_container}:5555"


class MonitorTask(SQLModel, table=True):
    """一个直播间监控任务。"""

    __tablename__ = "monitor_task"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    platform: Platform = Field(index=True)
    target: str = Field(description="直播间标识：抖音 room_id/短链/live.douyin.com 链接；小红书 直播链接/用户 id")
    device_id: Optional[int] = Field(default=None, foreign_key="device.id", index=True)

    interval_seconds: int = 60
    enabled: bool = Field(default=True, index=True)

    collect_products: bool = True
    collect_comments: bool = False
    record_video: bool = False
    keep_screenshot: bool = True

    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_status: Optional[RunStatus] = None
    last_error: Optional[str] = None
    run_count: int = 0
    fail_count: int = 0

    created_at: datetime = Field(default_factory=utcnow)


class LiveSnapshot(SQLModel, table=True):
    """一次采集到的直播间状态。"""

    __tablename__ = "live_snapshot"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="monitor_task.id", index=True)
    device_id: Optional[int] = Field(default=None, foreign_key="device.id")
    captured_at: datetime = Field(default_factory=utcnow, index=True)

    is_live: bool = True
    room_id: Optional[str] = None
    room_title: Optional[str] = None
    anchor_name: Optional[str] = None
    viewer_count: Optional[int] = None
    like_count: Optional[int] = None
    follower_count: Optional[int] = None
    product_count: int = 0

    screenshot_path: Optional[str] = None
    dump_path: Optional[str] = None
    comments_json: Optional[str] = None
    raw_json: Optional[str] = None


class ProductRecord(SQLModel, table=True):
    """商品在某次采集时刻的状态（同一商品会有多行，用于画变动曲线）。"""

    __tablename__ = "product_record"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="monitor_task.id", index=True)
    snapshot_id: int = Field(foreign_key="live_snapshot.id", index=True)
    captured_at: datetime = Field(default_factory=utcnow, index=True)

    position: Optional[int] = None
    product_key: Optional[str] = Field(default=None, index=True, description="标题归一化后的稳定键")
    product_id: Optional[str] = None
    title: Optional[str] = None
    price: Optional[float] = None
    price_text: Optional[str] = None
    origin_price: Optional[float] = None
    sales_text: Optional[str] = None
    stock_text: Optional[str] = None
    coupon_text: Optional[str] = None
    raw_json: Optional[str] = None


class Recording(SQLModel, table=True):
    """一段直播录屏。"""

    __tablename__ = "recording"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: Optional[int] = Field(default=None, foreign_key="monitor_task.id", index=True)
    device_id: Optional[int] = Field(default=None, foreign_key="device.id", index=True)

    status: RecordingStatus = Field(default=RecordingStatus.recording, index=True)
    started_at: datetime = Field(default_factory=utcnow, index=True)
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    file_path: Optional[str] = None
    size_bytes: Optional[int] = None
    segment_count: int = 0
    error: Optional[str] = None


class EventLog(SQLModel, table=True):
    """给控制台看的运行事件流。"""

    __tablename__ = "event_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    level: str = Field(default="info", index=True)
    source: str = Field(default="system", index=True)
    message: str = ""
    device_id: Optional[int] = None
    task_id: Optional[int] = None
