from __future__ import annotations

import logging
from typing import Optional

from ...config import settings
from ...models import PayChannel
from .base import PaymentError, PaymentProvider, PayResult
from .mock import MockProvider

log = logging.getLogger(__name__)

_providers: dict[str, PaymentProvider] = {}


def _build(channel: str) -> Optional[PaymentProvider]:
    if channel == PayChannel.mock.value:
        return MockProvider()
    if channel == PayChannel.alipay.value:
        from .alipay import AlipayProvider

        try:
            return AlipayProvider()
        except PaymentError as exc:
            log.warning("支付宝通道未启用: %s", exc)
            return None
    if channel == PayChannel.wechat.value:
        from .wechat import WechatProvider

        try:
            return WechatProvider()
        except PaymentError as exc:
            log.warning("微信支付通道未启用: %s", exc)
            return None
    return None


def get_provider(channel: str) -> PaymentProvider:
    provider = _providers.get(channel)
    if provider is None:
        provider = _build(channel)
        if provider is None:
            raise PaymentError(
                f"支付通道 {channel} 不可用：缺少商户配置。"
                "本地联调可以用 channel=mock 走完整流程。"
            )
        _providers[channel] = provider
    return provider


def available_channels() -> list[dict[str, object]]:
    """列出通道及其是否可用，前端据此决定显示哪些付款方式。"""
    wanted = [c.strip() for c in settings.payment_channels.split(",") if c.strip()]
    out = []
    for ch in wanted:
        try:
            provider = get_provider(ch)
            ready, reason = True, None
        except PaymentError as exc:
            provider, ready, reason = None, False, str(exc)
        out.append(
            {
                "channel": ch,
                "label": {"alipay": "支付宝", "wechat": "微信支付", "mock": "本地联调(Mock)"}.get(ch, ch),
                "ready": ready,
                "reason": reason,
                "display": getattr(provider, "display_hint", "扫码支付") if provider else None,
            }
        )
    return out


def reset_cache() -> None:
    _providers.clear()


__all__ = [
    "PaymentError",
    "PaymentProvider",
    "PayResult",
    "get_provider",
    "available_channels",
    "reset_cache",
]
