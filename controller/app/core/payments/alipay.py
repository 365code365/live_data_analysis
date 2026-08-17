from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ...config import settings
from .base import NotifyResult, PaymentError, PayResult

log = logging.getLogger(__name__)


def _load_private_key(pem: str) -> rsa.RSAPrivateKey:
    body = pem.strip()
    if "BEGIN" not in body:
        # 支付宝控制台复制出来的是裸 base64，补上头尾
        body = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(
            body[i : i + 64] for i in range(0, len(body), 64)
        ) + "\n-----END PRIVATE KEY-----\n"
    return serialization.load_pem_private_key(body.encode(), password=None)


def _load_public_key(pem: str):  # noqa: ANN201
    body = pem.strip()
    if "BEGIN" not in body:
        body = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(
            body[i : i + 64] for i in range(0, len(body), 64)
        ) + "\n-----END PUBLIC KEY-----\n"
    return serialization.load_pem_public_key(body.encode())


def _read(path_or_pem: str) -> str:
    if not path_or_pem:
        return ""
    p = Path(path_or_pem)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return path_or_pem


class AlipayProvider:
    """支付宝当面付（alipay.trade.precreate）扫码支付。

    需要在 .env 里配好 app_id、应用私钥、支付宝公钥。
    签名用 RSA2(SHA256WithRSA)，与官方文档一致；回调按官方规则验签。
    注意：本项目未做真实商户联调，上线前请用沙箱环境走一遍。
    """

    channel = "alipay"
    display_hint = "用支付宝扫码完成付款"

    def __init__(self) -> None:
        if not settings.alipay_app_id:
            raise PaymentError("未配置 ALIPAY_APP_ID")
        priv = _read(settings.alipay_private_key)
        if not priv:
            raise PaymentError("未配置 ALIPAY_PRIVATE_KEY（可填 PEM 内容或文件路径）")
        pub = _read(settings.alipay_public_key)
        if not pub:
            raise PaymentError("未配置 ALIPAY_PUBLIC_KEY（支付宝公钥，用于回调验签）")
        try:
            self._private_key = _load_private_key(priv)
            self._public_key = _load_public_key(pub)
        except Exception as exc:
            raise PaymentError(f"支付宝密钥解析失败: {exc}") from exc
        self.app_id = settings.alipay_app_id
        self.gateway = settings.alipay_gateway

    # ── 签名 ──────────────────────────────────────────────────────────
    def _sign(self, params: dict[str, str]) -> str:
        content = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
        sig = self._private_key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    def _verify(self, params: dict[str, str], sign: str) -> bool:
        content = "&".join(
            f"{k}={params[k]}"
            for k in sorted(params)
            if k not in ("sign", "sign_type") and params[k] not in (None, "")
        )
        try:
            self._public_key.verify(
                base64.b64decode(sign), content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
            )
            return True
        except Exception as exc:
            log.warning("支付宝回调验签失败: %s", exc)
            return False

    def _call(self, method: str, biz_content: dict[str, Any], notify_url: str = "") -> dict[str, Any]:
        import datetime as _dt

        params: dict[str, str] = {
            "app_id": self.app_id,
            "method": method,
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
        }
        if notify_url:
            params["notify_url"] = notify_url
        params["sign"] = self._sign(params)

        try:
            resp = httpx.post(self.gateway, data=params, timeout=20.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PaymentError(f"调用支付宝失败: {exc}") from exc

        payload = resp.json()
        key = method.replace(".", "_") + "_response"
        body = payload.get(key) or {}
        if body.get("code") != "10000":
            raise PaymentError(
                f"支付宝返回错误 {body.get('code')}: {body.get('msg')} / {body.get('sub_msg')}"
            )
        return body

    # ── 接口 ──────────────────────────────────────────────────────────
    def create(self, *, order_no: str, amount_cents: int, subject: str, notify_url: str) -> PayResult:
        body = self._call(
            "alipay.trade.precreate",
            {
                "out_trade_no": order_no,
                "total_amount": f"{amount_cents / 100:.2f}",
                "subject": subject[:256],
            },
            notify_url=notify_url,
        )
        qr = body.get("qr_code")
        if not qr:
            raise PaymentError("支付宝没有返回 qr_code")
        return PayResult(qr_code=qr, pay_url=qr, raw=body)

    def parse_notify(self, headers: dict[str, str], body: bytes) -> NotifyResult:
        data = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
        sign = data.get("sign", "")
        if not sign or not self._verify(data, sign):
            raise PaymentError("支付宝回调验签不通过")
        status = data.get("trade_status", "")
        amount = data.get("total_amount")
        return NotifyResult(
            order_no=data.get("out_trade_no", ""),
            paid=status in ("TRADE_SUCCESS", "TRADE_FINISHED"),
            trade_no=data.get("trade_no"),
            amount_cents=int(round(float(amount) * 100)) if amount else None,
            buyer=data.get("buyer_logon_id") or data.get("buyer_id"),
            raw=data,
            ack_body="success",
        )

    def query(self, order_no: str) -> NotifyResult:
        try:
            body = self._call("alipay.trade.query", {"out_trade_no": order_no})
        except PaymentError as exc:
            # 订单不存在时支付宝也会返回错误码，这里当作未支付
            log.debug("支付宝查询 %s: %s", order_no, exc)
            return NotifyResult(order_no=order_no, paid=False, raw={"error": str(exc)})
        status = body.get("trade_status", "")
        amount = body.get("total_amount")
        return NotifyResult(
            order_no=order_no,
            paid=status in ("TRADE_SUCCESS", "TRADE_FINISHED"),
            trade_no=body.get("trade_no"),
            amount_cents=int(round(float(amount) * 100)) if amount else None,
            buyer=body.get("buyer_logon_id"),
            raw=body,
        )
