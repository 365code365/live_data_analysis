from __future__ import annotations

import json
import logging
import threading
from typing import Any

from ...config import settings
from .base import NotifyResult, PayResult

log = logging.getLogger(__name__)


class MockProvider:
    """本地联调通道：不接真实网关，但把整套流程跑通。

    二维码内容是控制台自己的一个链接，用手机或浏览器打开即视为「付款成功」，
    于是订单状态机、权益发放、前端轮询都能在没有商户号的情况下完整验证。
    生产环境把 PAYMENT_CHANNELS 改成 alipay,wechat 即可，不要保留 mock。
    """

    channel = "mock"
    display_hint = "扫码或直接点开链接即视为支付成功（仅本地联调）"

    def __init__(self) -> None:
        self._paid: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _pay_link(self, order_no: str) -> str:
        base = settings.site_base_url.rstrip("/")
        return f"{base}/api/billing/mock/pay?order_no={order_no}"

    def create(self, *, order_no: str, amount_cents: int, subject: str, notify_url: str) -> PayResult:
        link = self._pay_link(order_no)
        return PayResult(
            qr_code=link,
            pay_url=link,
            trade_no=f"MOCK{order_no}",
            raw={"subject": subject, "amount_cents": amount_cents, "notify_url": notify_url},
        )

    # mock 没有真实回调，由 /api/billing/mock/pay 直接标记
    def mark_paid(self, order_no: str, amount_cents: int) -> None:
        with self._lock:
            self._paid[order_no] = {"amount_cents": amount_cents}
        log.info("mock 通道标记订单已付: %s", order_no)

    def parse_notify(self, headers: dict[str, str], body: bytes) -> NotifyResult:
        data = json.loads(body or b"{}")
        order_no = str(data.get("order_no") or "")
        if not order_no:
            from .base import PaymentError

            raise PaymentError("mock 回调缺少 order_no")
        return NotifyResult(
            order_no=order_no,
            paid=True,
            trade_no=f"MOCK{order_no}",
            amount_cents=data.get("amount_cents"),
            raw=data,
            ack_body="success",
        )

    def query(self, order_no: str) -> NotifyResult:
        with self._lock:
            hit = self._paid.get(order_no)
        return NotifyResult(
            order_no=order_no,
            paid=bool(hit),
            trade_no=f"MOCK{order_no}" if hit else None,
            amount_cents=(hit or {}).get("amount_cents"),
            raw={"source": "mock-query"},
        )
