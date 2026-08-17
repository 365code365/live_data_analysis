from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ...config import settings
from .base import NotifyResult, PaymentError, PayResult

log = logging.getLogger(__name__)

API_BASE = "https://api.mch.weixin.qq.com"


def _read(path_or_pem: str) -> str:
    if not path_or_pem:
        return ""
    p = Path(path_or_pem)
    return p.read_text(encoding="utf-8") if p.exists() else path_or_pem


class WechatProvider:
    """微信支付 v3 Native（扫码）。

    需要：商户号、AppID、商户 API 证书私钥 + 证书序列号、APIv3 密钥。
    请求按 v3 规范用 RSA-SHA256 签名；回调用 APIv3 密钥做 AES-256-GCM 解密。
    配了微信支付平台公钥时会额外验签（强烈建议配上）。
    注意：本项目未做真实商户联调，上线前请先在沙箱/小额真实环境验证。
    """

    channel = "wechat"
    display_hint = "用微信扫码完成付款"

    def __init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("WECHAT_MCH_ID", settings.wechat_mch_id),
                ("WECHAT_APP_ID", settings.wechat_app_id),
                ("WECHAT_API_V3_KEY", settings.wechat_api_v3_key),
                ("WECHAT_CERT_SERIAL_NO", settings.wechat_cert_serial_no),
                ("WECHAT_PRIVATE_KEY", settings.wechat_private_key),
            )
            if not value
        ]
        if missing:
            raise PaymentError("未配置: " + ", ".join(missing))
        try:
            self._private_key = serialization.load_pem_private_key(
                _read(settings.wechat_private_key).encode(), password=None
            )
        except Exception as exc:
            raise PaymentError(f"微信商户私钥解析失败: {exc}") from exc

        self.mch_id = settings.wechat_mch_id
        self.app_id = settings.wechat_app_id
        self.serial_no = settings.wechat_cert_serial_no
        self._api_v3_key = settings.wechat_api_v3_key.encode()
        self._platform_key = None
        plat = _read(settings.wechat_platform_public_key)
        if plat:
            try:
                self._platform_key = serialization.load_pem_public_key(plat.encode())
            except Exception as exc:
                log.warning("微信平台公钥解析失败，回调将只解密不验签: %s", exc)

    # ── 签名 ──────────────────────────────────────────────────────────
    def _auth_header(self, method: str, url_path: str, body: str) -> str:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex.upper()
        message = f"{method}\n{url_path}\n{ts}\n{nonce}\n{body}\n"
        sig = self._private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        signature = base64.b64encode(sig).decode()
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.mch_id}",nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{ts}",serial_no="{self.serial_no}"'
        )

    def _request(self, method: str, url_path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload is not None else ""
        headers = {
            "Authorization": self._auth_header(method, url_path, body),
            "Accept": "application/json",
            "User-Agent": "ldm-controller/1.0",
        }
        if body:
            headers["Content-Type"] = "application/json"
        try:
            resp = httpx.request(method, API_BASE + url_path, content=body or None, headers=headers, timeout=20.0)
        except httpx.HTTPError as exc:
            raise PaymentError(f"调用微信支付失败: {exc}") from exc
        if resp.status_code >= 400:
            raise PaymentError(f"微信支付返回 {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    # ── 接口 ──────────────────────────────────────────────────────────
    def create(self, *, order_no: str, amount_cents: int, subject: str, notify_url: str) -> PayResult:
        data = self._request(
            "POST",
            "/v3/pay/transactions/native",
            {
                "appid": self.app_id,
                "mchid": self.mch_id,
                "description": subject[:127],
                "out_trade_no": order_no,
                "notify_url": notify_url,
                "amount": {"total": int(amount_cents), "currency": "CNY"},
            },
        )
        code_url = data.get("code_url")
        if not code_url:
            raise PaymentError(f"微信没有返回 code_url: {data}")
        return PayResult(qr_code=code_url, pay_url=code_url, raw=data)

    def parse_notify(self, headers: dict[str, str], body: bytes) -> NotifyResult:
        lower = {k.lower(): v for k, v in headers.items()}
        if self._platform_key is not None:
            ts = lower.get("wechatpay-timestamp", "")
            nonce = lower.get("wechatpay-nonce", "")
            signature = lower.get("wechatpay-signature", "")
            message = f"{ts}\n{nonce}\n{body.decode('utf-8')}\n"
            try:
                self._platform_key.verify(
                    base64.b64decode(signature), message.encode(), padding.PKCS1v15(), hashes.SHA256()
                )
            except Exception as exc:
                raise PaymentError(f"微信回调验签不通过: {exc}") from exc

        envelope = json.loads(body or b"{}")
        resource = envelope.get("resource") or {}
        ciphertext = resource.get("ciphertext")
        if not ciphertext:
            raise PaymentError("微信回调缺少 resource.ciphertext")
        try:
            aead = AESGCM(self._api_v3_key)
            plain = aead.decrypt(
                (resource.get("nonce") or "").encode(),
                base64.b64decode(ciphertext),
                (resource.get("associated_data") or "").encode() or None,
            )
        except Exception as exc:
            raise PaymentError(f"微信回调解密失败（APIv3 密钥是否正确）: {exc}") from exc

        data = json.loads(plain.decode("utf-8"))
        amount = (data.get("amount") or {}).get("total")
        payer = (data.get("payer") or {}).get("openid")
        return NotifyResult(
            order_no=data.get("out_trade_no", ""),
            paid=data.get("trade_state") == "SUCCESS",
            trade_no=data.get("transaction_id"),
            amount_cents=int(amount) if amount is not None else None,
            buyer=payer,
            raw=data,
            ack_body=json.dumps({"code": "SUCCESS", "message": "成功"}, ensure_ascii=False),
            ack_content_type="application/json",
        )

    def query(self, order_no: str) -> NotifyResult:
        path = f"/v3/pay/transactions/out-trade-no/{order_no}?mchid={self.mch_id}"
        try:
            data = self._request("GET", path)
        except PaymentError as exc:
            log.debug("微信查询 %s: %s", order_no, exc)
            return NotifyResult(order_no=order_no, paid=False, raw={"error": str(exc)})
        amount = (data.get("amount") or {}).get("total")
        return NotifyResult(
            order_no=order_no,
            paid=data.get("trade_state") == "SUCCESS",
            trade_no=data.get("transaction_id"),
            amount_cents=int(amount) if amount is not None else None,
            buyer=(data.get("payer") or {}).get("openid"),
            raw=data,
        )
