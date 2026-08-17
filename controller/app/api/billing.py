from __future__ import annotations

import io
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from ..config import settings
from ..core import billing, payments
from ..core.payments.base import PaymentError
from ..db import get_session
from ..models import Entitlement, Order, OrderStatus, Plan
from ..schemas import Ok, OrderCreate, PlanCreate, PlanUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    """后台定价类接口的简单保护：设了 ADMIN_TOKEN 就必须带对应请求头。"""
    if not settings.admin_token:
        return
    if x_admin_token != settings.admin_token:
        raise HTTPException(401, "需要正确的 X-Admin-Token")


# ── 套餐（前台只读，后台可写）──────────────────────────────────────────────
@router.get("/plans")
def list_plans(
    include_disabled: bool = Query(False),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Plan).order_by(Plan.sort_order, Plan.id)
    if not include_disabled:
        stmt = stmt.where(Plan.enabled == True)  # noqa: E712
    plans = session.exec(stmt).all()
    return {
        "items": [billing.plan_out(p) for p in plans],
        "channels": payments.available_channels(),
        "billing_enabled": settings.billing_enabled,
        "enforce": settings.billing_enforce,
    }


@router.post("/plans", status_code=201, dependencies=[Depends(require_admin)])
def create_plan(payload: PlanCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.exec(select(Plan).where(Plan.code == payload.code)).first():
        raise HTTPException(409, f"套餐 code 已存在: {payload.code}")
    plan = Plan(**payload.model_dump())
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return billing.plan_out(plan)


@router.patch("/plans/{plan_id}", dependencies=[Depends(require_admin)])
def update_plan(plan_id: int, payload: PlanUpdate, session: Session = Depends(get_session)) -> dict[str, Any]:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(404, "套餐不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(plan, k, v)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return billing.plan_out(plan)


@router.delete("/plans/{plan_id}", dependencies=[Depends(require_admin)])
def delete_plan(plan_id: int, session: Session = Depends(get_session)) -> Ok:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(404, "套餐不存在")
    if session.exec(select(Order).where(Order.plan_id == plan_id)).first():
        # 有历史订单就只下架，保留数据可追溯
        plan.enabled = False
        session.add(plan)
        session.commit()
        return Ok(message="该套餐已有订单，改为下架而非删除")
    session.delete(plan)
    session.commit()
    return Ok(message="已删除")


# ── 订单 ──────────────────────────────────────────────────────────────────
@router.post("/orders", status_code=201)
def create_order(payload: OrderCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        order = billing.create_order(
            session, plan_id=payload.plan_id, channel=payload.channel, remark=payload.remark
        )
    except (billing.BillingError, PaymentError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return billing.order_out(order)


@router.get("/orders")
def list_orders(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Order).order_by(Order.id.desc())  # type: ignore[attr-defined]
    if status:
        stmt = stmt.where(Order.status == status)
    return {"items": [billing.order_out(o) for o in session.exec(stmt.limit(limit)).all()]}


def _get_order(session: Session, order_no: str) -> Order:
    order = session.exec(select(Order).where(Order.order_no == order_no)).first()
    if order is None:
        raise HTTPException(404, "订单不存在")
    return order


@router.get("/orders/{order_no}")
def get_order(order_no: str, sync: bool = Query(True), session: Session = Depends(get_session)) -> dict[str, Any]:
    """前端轮询这个接口等支付结果；sync=true 会顺带向渠道查一次。"""
    order = _get_order(session, order_no)
    if sync:
        try:
            order = billing.sync_order(session, order)
        except billing.BillingError as exc:
            log.warning("同步订单失败: %s", exc)
    return billing.order_out(order)


@router.get("/orders/{order_no}/qr.png")
def order_qr(order_no: str, scale: int = Query(6, ge=2, le=16), session: Session = Depends(get_session)) -> Response:
    """把二维码内容渲染成 PNG，前端直接 <img> 用，不依赖任何前端二维码库。"""
    order = _get_order(session, order_no)
    if not order.qr_code:
        raise HTTPException(409, "该订单没有二维码内容")
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(500, "服务端缺少 qrcode 依赖") from exc

    qr = qrcode.QRCode(border=2, box_size=scale)
    qr.add_data(order.qr_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/orders/{order_no}/cancel")
def cancel_order(order_no: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    order = _get_order(session, order_no)
    if order.status == OrderStatus.paid:
        raise HTTPException(409, "订单已支付，不能取消")
    order.status = OrderStatus.closed
    order.closed_at = billing.utcnow()
    session.add(order)
    session.commit()
    session.refresh(order)
    return billing.order_out(order)


# ── 支付回调 ──────────────────────────────────────────────────────────────
@router.post("/notify/{channel}")
async def payment_notify(channel: str, request: Request, session: Session = Depends(get_session)) -> Response:
    """支付渠道异步回调。验签失败直接拒绝，绝不凭回调内容裸信。"""
    body = await request.body()
    headers = dict(request.headers)
    try:
        provider = payments.get_provider(channel)
        result = provider.parse_notify(headers, body)
    except PaymentError as exc:
        log.warning("支付回调被拒 channel=%s: %s", channel, exc)
        raise HTTPException(400, str(exc)) from exc

    order = session.exec(select(Order).where(Order.order_no == result.order_no)).first()
    if order is None:
        log.warning("支付回调找不到订单: %s", result.order_no)
        raise HTTPException(404, "订单不存在")

    if result.paid:
        try:
            billing.mark_paid(
                session,
                order,
                trade_no=result.trade_no,
                buyer=result.buyer,
                amount_cents=result.amount_cents,
                raw=result.raw,
            )
        except billing.BillingError as exc:
            log.error("回调处理失败: %s", exc)
            raise HTTPException(400, str(exc)) from exc

    return Response(
        content=result.ack_body or "success",
        media_type=result.ack_content_type,
    )


@router.get("/mock/pay", include_in_schema=False)
def mock_pay(order_no: str, session: Session = Depends(get_session)) -> HTMLResponse:
    """mock 通道的「付款页」：打开即视为付款成功，用于本地把流程跑通。"""
    order = _get_order(session, order_no)
    if order.channel.value != "mock":
        raise HTTPException(400, "该订单不是 mock 通道")
    provider = payments.get_provider("mock")
    provider.mark_paid(order.order_no, order.amount_cents)  # type: ignore[attr-defined]
    billing.sync_order(session, order)
    session.refresh(order)
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
        <title>模拟支付</title><style>
        body{{background:#0f1216;color:#e6eaf0;font:15px/1.7 -apple-system,"PingFang SC",sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
        .box{{background:#171b21;border:1px solid #2a323c;border-radius:12px;padding:28px 34px;text-align:center}}
        .ok{{color:#3ecf8e;font-size:20px;font-weight:600;margin-bottom:10px}}
        code{{color:#4ea1ff}}</style></head><body><div class="box">
        <div class="ok">模拟支付成功</div>
        <div>订单 <code>{order.order_no}</code></div>
        <div>金额 ¥{order.amount_cents / 100:.2f} · 状态 {order.status.value}</div>
        <p style="color:#8b98a8;font-size:13px">这是本地联调通道。回到控制台即可看到权益已发放。</p>
        </div></body></html>"""
    )


# ── 权益 / 概览 ───────────────────────────────────────────────────────────
@router.get("/entitlements")
def list_entitlements(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.exec(select(Entitlement).order_by(Entitlement.id.desc())).all()  # type: ignore[attr-defined]
    return {"items": [billing.entitlement_out(session, e) for e in rows]}


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    return billing.quota_summary(session)


@router.get("/config", dependencies=[Depends(require_admin)])
def billing_config() -> dict[str, Any]:
    """后台查看当前计费与支付配置（不返回任何密钥内容）。"""
    return {
        "billing_enabled": settings.billing_enabled,
        "enforce": settings.billing_enforce,
        "site_base_url": settings.site_base_url,
        "order_ttl_minutes": settings.order_ttl_minutes,
        "channels": payments.available_channels(),
        "admin_token_set": bool(settings.admin_token),
        "alipay_configured": bool(settings.alipay_app_id and settings.alipay_private_key),
        "wechat_configured": bool(settings.wechat_mch_id and settings.wechat_private_key),
        "notify_urls": {
            "alipay": billing.notify_url("alipay"),
            "wechat": billing.notify_url("wechat"),
        },
    }
