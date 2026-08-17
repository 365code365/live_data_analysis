from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
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
    # 0 表示不限制；由套餐规格决定，这是「不同配置不同价格」的落地点
    memory_mb: int = 0
    cpu_limit: float = 0
    # 安卓 /data 卷容量（GB）。0 = 不设上限。
    disk_gb: int = 0
    # 宿主文件系统支持配额时才是硬限制，否则只是记录（见 docker_manager._ensure_volume）
    disk_quota: bool = Field(default=False)
    # 创建时选的档位，用来在界面上显示「标准型 · 高清竖屏」而不是一堆数字
    perf_code: Optional[str] = None
    screen_code: Optional[str] = None
    android_image: Optional[str] = None

    proxy_id: Optional[int] = Field(default=None, foreign_key="proxy_profile.id", index=True)
    # 由哪份权益开出来的（计费开启时用于配额核算与到期回收）
    entitlement_id: Optional[int] = Field(default=None, index=True)

    adb_port: Optional[int] = None       # 宿主机映射端口（外部调试用）
    novnc_port: Optional[int] = None     # 宿主机映射端口（浏览器看屏）
    audio_port: Optional[int] = None     # 宿主机映射端口（浏览器听声）
    vnc_password: Optional[str] = None
    enable_audio: bool = Field(default=True)

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


class OrderStatus(str, Enum):
    pending = "pending"      # 待支付
    paid = "paid"            # 已支付
    closed = "closed"        # 已取消/超时关闭
    refunded = "refunded"
    failed = "failed"


class PayChannel(str, Enum):
    alipay = "alipay"
    wechat = "wechat"
    mock = "mock"            # 本地联调用：不接真实网关


class EntitlementStatus(str, Enum):
    active = "active"
    expired = "expired"


class Plan(SQLModel, table=True):
    """售卖的套餐。规格不同价格不同，全部在后台可改。"""

    __tablename__ = "plan"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, description="套餐标识，创建订单时用")
    name: str
    description: Optional[str] = None
    badge: Optional[str] = Field(default=None, description="卡片角标，如「推荐」")

    # ── 规格（决定价格的那些配置）──────────────────────────────────
    width: int = 720
    height: int = 1280
    dpi: int = 320
    cpu_limit: float = Field(default=0, description="0 表示不限制")
    memory_mb: int = Field(default=0, description="0 表示不限制")
    max_devices: int = 1
    max_tasks: int = 5
    allow_proxy: bool = True
    allow_recording: bool = True
    allow_audio: bool = True

    # ── 价格 ────────────────────────────────────────────────────────
    duration_days: int = 30
    price_cents: int = Field(default=0, description="单位：分")
    original_price_cents: Optional[int] = None
    currency: str = "CNY"

    sort_order: int = 0
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)

    def spec(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "cpu_limit": self.cpu_limit,
            "memory_mb": self.memory_mb,
            "max_devices": self.max_devices,
            "max_tasks": self.max_tasks,
            "allow_proxy": self.allow_proxy,
            "allow_recording": self.allow_recording,
            "allow_audio": self.allow_audio,
            "duration_days": self.duration_days,
        }


class Order(SQLModel, table=True):
    """一笔订单。二维码支付：创建即生成 qr_code，付款后由回调或轮询置为 paid。"""

    __tablename__ = "order"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_no: str = Field(index=True, description="商户订单号")
    plan_id: Optional[int] = Field(default=None, foreign_key="plan.id", index=True)
    plan_code: Optional[str] = None
    plan_name: Optional[str] = None
    plan_snapshot: Optional[str] = Field(default=None, description="下单时的套餐规格快照(JSON)")

    amount_cents: int = 0
    currency: str = "CNY"
    channel: PayChannel = Field(default=PayChannel.mock, index=True)
    status: OrderStatus = Field(default=OrderStatus.pending, index=True)

    qr_code: Optional[str] = Field(default=None, description="二维码内容（付款码链接）")
    pay_url: Optional[str] = None
    trade_no: Optional[str] = Field(default=None, description="支付渠道流水号")
    buyer: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    notify_raw: Optional[str] = None
    error: Optional[str] = None
    remark: Optional[str] = None


class Entitlement(SQLModel, table=True):
    """支付成功后发放的权益：在有效期内可以按套餐规格开设备。"""

    __tablename__ = "entitlement"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: Optional[int] = Field(default=None, foreign_key="order.id", index=True)
    order_no: Optional[str] = Field(default=None, index=True)
    plan_id: Optional[int] = Field(default=None, foreign_key="plan.id", index=True)
    plan_code: Optional[str] = None
    plan_name: Optional[str] = None
    spec_snapshot: Optional[str] = None

    max_devices: int = 1
    max_tasks: int = 5
    started_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    status: EntitlementStatus = Field(default=EntitlementStatus.active, index=True)
    created_at: datetime = Field(default_factory=utcnow)


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
