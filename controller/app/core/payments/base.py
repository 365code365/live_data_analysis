from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


class PaymentError(RuntimeError):
    """支付通道不可用或调用失败。"""


@dataclass
class PayResult:
    """创建支付后返回给前端的信息。"""

    qr_code: Optional[str] = None      # 二维码内容（付款链接）
    pay_url: Optional[str] = None      # 可直接跳转的支付页（H5/PC）
    trade_no: Optional[str] = None     # 渠道流水号
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotifyResult:
    """回调/查询归一化后的结果。"""

    order_no: str
    paid: bool
    trade_no: Optional[str] = None
    amount_cents: Optional[int] = None
    buyer: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)
    ack_body: Optional[str] = None       # 需要回给渠道的响应体
    ack_content_type: str = "text/plain"


class PaymentProvider(Protocol):
    channel: str
    display_hint: str

    def create(self, *, order_no: str, amount_cents: int, subject: str, notify_url: str) -> PayResult:
        """下单并拿到二维码 / 支付链接。"""

    def parse_notify(self, headers: dict[str, str], body: bytes) -> NotifyResult:
        """校验并解析异步回调。验签不通过必须抛 PaymentError。"""

    def query(self, order_no: str) -> NotifyResult:
        """主动查询订单状态（回调丢失时的兜底）。"""
