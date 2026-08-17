from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, select

from ..config import settings
from ..db import session_scope
from ..models import (
    Device,
    Entitlement,
    EntitlementStatus,
    Order,
    OrderStatus,
    PayChannel,
    Plan,
    utcnow,
)
from . import events, payments

log = logging.getLogger(__name__)


class BillingError(RuntimeError):
    pass


# ── 套餐 ──────────────────────────────────────────────────────────────────
DEFAULT_PLANS: list[dict[str, Any]] = [
    {
        "code": "starter",
        "name": "入门版",
        "description": "单实例，适合盯一个直播间",
        "width": 720, "height": 1280, "dpi": 320,
        "memory_mb": 3072, "cpu_limit": 2,
        "max_devices": 1, "max_tasks": 3,
        "allow_proxy": False, "allow_recording": True, "allow_audio": True,
        "duration_days": 30, "price_cents": 9900, "original_price_cents": 14900,
        "sort_order": 10,
    },
    {
        "code": "pro",
        "name": "专业版",
        "description": "高清实例 + 独立出口 IP，适合多账号并行",
        "width": 1080, "height": 1920, "dpi": 420,
        "memory_mb": 6144, "cpu_limit": 4,
        "max_devices": 3, "max_tasks": 15,
        "allow_proxy": True, "allow_recording": True, "allow_audio": True,
        "duration_days": 30, "price_cents": 29900, "original_price_cents": 39900,
        "badge": "推荐", "sort_order": 20,
    },
    {
        "code": "team",
        "name": "团队版",
        "description": "多实例矩阵，含全部功能与更高任务配额",
        "width": 1080, "height": 1920, "dpi": 420,
        "memory_mb": 8192, "cpu_limit": 6,
        "max_devices": 10, "max_tasks": 60,
        "allow_proxy": True, "allow_recording": True, "allow_audio": True,
        "duration_days": 30, "price_cents": 79900,
        "sort_order": 30,
    },
]


def seed_plans() -> int:
    """首次启动时灌入示例套餐，之后管理员在后台自行改价改配置。"""
    created = 0
    with session_scope() as session:
        if session.exec(select(Plan)).first() is not None:
            return 0
        for item in DEFAULT_PLANS:
            session.add(Plan(**item))
            created += 1
    if created:
        log.info("已初始化 %s 个示例套餐（可在后台修改定价与规格）", created)
    return created


def plan_out(plan: Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "code": plan.code,
        "name": plan.name,
        "description": plan.description,
        "badge": plan.badge,
        "spec": plan.spec(),
        "duration_days": plan.duration_days,
        "price_cents": plan.price_cents,
        "price_yuan": round(plan.price_cents / 100, 2),
        "original_price_cents": plan.original_price_cents,
        "original_price_yuan": round(plan.original_price_cents / 100, 2) if plan.original_price_cents else None,
        "currency": plan.currency,
        "sort_order": plan.sort_order,
        "enabled": plan.enabled,
        "created_at": plan.created_at,
    }


# ── 订单 ──────────────────────────────────────────────────────────────────
def new_order_no() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(4).upper()


def notify_url(channel: str) -> str:
    return f"{settings.site_base_url.rstrip('/')}/api/billing/notify/{channel}"


def create_order(session: Session, *, plan_id: int, channel: str, remark: Optional[str] = None) -> Order:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise BillingError(f"套餐不存在: {plan_id}")
    if not plan.enabled:
        raise BillingError(f"套餐已下架: {plan.name}")
    if channel not in {c.value for c in PayChannel}:
        raise BillingError(f"不支持的支付渠道: {channel}")

    provider = payments.get_provider(channel)  # 通道不可用会直接抛错，避免生成废订单

    order = Order(
        order_no=new_order_no(),
        plan_id=plan.id,
        plan_code=plan.code,
        plan_name=plan.name,
        plan_snapshot=json.dumps(plan.spec(), ensure_ascii=False),
        amount_cents=plan.price_cents,
        currency=plan.currency,
        channel=PayChannel(channel),
        status=OrderStatus.pending,
        expires_at=utcnow() + timedelta(minutes=settings.order_ttl_minutes),
        remark=remark,
    )
    session.add(order)
    session.commit()
    session.refresh(order)

    try:
        result = provider.create(
            order_no=order.order_no,
            amount_cents=order.amount_cents,
            subject=f"{plan.name} · {plan.duration_days}天",
            notify_url=notify_url(channel),
        )
    except Exception as exc:
        order.status = OrderStatus.failed
        order.error = str(exc)[:500]
        session.add(order)
        session.commit()
        raise BillingError(f"创建支付失败: {exc}") from exc

    order.qr_code = result.qr_code
    order.pay_url = result.pay_url
    order.trade_no = result.trade_no
    session.add(order)
    session.commit()
    session.refresh(order)

    events.emit(
        f"新订单 {order.order_no}：{plan.name} ¥{order.amount_cents / 100:.2f}（{channel}）",
        source="billing",
    )
    return order


def order_out(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "plan_id": order.plan_id,
        "plan_code": order.plan_code,
        "plan_name": order.plan_name,
        "spec": json.loads(order.plan_snapshot) if order.plan_snapshot else {},
        "amount_cents": order.amount_cents,
        "amount_yuan": round(order.amount_cents / 100, 2),
        "currency": order.currency,
        "channel": order.channel,
        "status": order.status,
        "qr_code": order.qr_code,
        "pay_url": order.pay_url,
        "trade_no": order.trade_no,
        "buyer": order.buyer,
        "created_at": order.created_at,
        "expires_at": order.expires_at,
        "paid_at": order.paid_at,
        "error": order.error,
        "remark": order.remark,
    }


def mark_paid(
    session: Session,
    order: Order,
    *,
    trade_no: Optional[str] = None,
    buyer: Optional[str] = None,
    amount_cents: Optional[int] = None,
    raw: Optional[dict[str, Any]] = None,
) -> Entitlement:
    """幂等：重复回调不会重复发权益。"""
    if order.status == OrderStatus.paid:
        existing = session.exec(select(Entitlement).where(Entitlement.order_id == order.id)).first()
        if existing:
            return existing

    if amount_cents is not None and amount_cents != order.amount_cents:
        # 金额不符一律拒绝，避免被改价
        raise BillingError(
            f"订单 {order.order_no} 金额不符：期望 {order.amount_cents}，回调 {amount_cents}"
        )

    order.status = OrderStatus.paid
    order.paid_at = utcnow()
    order.trade_no = trade_no or order.trade_no
    order.buyer = buyer or order.buyer
    if raw:
        order.notify_raw = json.dumps(raw, ensure_ascii=False)[:4000]
    session.add(order)

    spec = json.loads(order.plan_snapshot) if order.plan_snapshot else {}
    days = int(spec.get("duration_days") or 30)
    ent = Entitlement(
        order_id=order.id,
        order_no=order.order_no,
        plan_id=order.plan_id,
        plan_code=order.plan_code,
        plan_name=order.plan_name,
        spec_snapshot=order.plan_snapshot,
        max_devices=int(spec.get("max_devices") or 1),
        max_tasks=int(spec.get("max_tasks") or 5),
        started_at=utcnow(),
        expires_at=utcnow() + timedelta(days=days),
        status=EntitlementStatus.active,
    )
    session.add(ent)
    session.commit()
    session.refresh(ent)

    events.emit(
        f"订单 {order.order_no} 支付成功，已发放权益（{ent.plan_name}，{days} 天，{ent.max_devices} 台设备）",
        source="billing",
    )
    return ent


def _paid_at_channel(order: Order) -> bool:
    """向渠道确认一次是否已支付。查不通时保守返回 False（允许关单）。"""
    try:
        channel = order.channel.value if hasattr(order.channel, "value") else str(order.channel)
        return bool(payments.get_provider(channel).query(order.order_no).paid)
    except Exception as exc:
        log.debug("关单前确认支付状态失败 %s: %s", order.order_no, exc)
        return False


def sync_order(session: Session, order: Order) -> Order:
    """主动查一次渠道状态。回调丢失或本地联调时靠它推进状态机。"""
    if order.status != OrderStatus.pending:
        return order
    if order.expires_at and utcnow() > order.expires_at:
        # 关单前必须再问渠道一次：用户完全可能在最后一秒付款，
        # 直接按本地时间关掉会造成「钱付了但没发权益」。
        if _paid_at_channel(order):
            log.warning("订单 %s 已超时但渠道显示已支付，按已支付处理", order.order_no)
        else:
            log.info("订单 %s 超时关闭（expires_at=%s now=%s）", order.order_no, order.expires_at, utcnow())
            order.status = OrderStatus.closed
            order.closed_at = utcnow()
            session.add(order)
            session.commit()
            session.refresh(order)
            return order
    try:
        provider = payments.get_provider(order.channel.value)
        result = provider.query(order.order_no)
    except Exception as exc:
        log.warning("查询订单 %s 失败: %s", order.order_no, exc)
        return order
    log.info("订单 %s 渠道查询结果 paid=%s trade_no=%s", order.order_no, result.paid, result.trade_no)
    if result.paid:
        mark_paid(
            session,
            order,
            trade_no=result.trade_no,
            buyer=result.buyer,
            amount_cents=result.amount_cents,
            raw=result.raw,
        )
        session.refresh(order)
    return order


# ── 权益 / 配额 ───────────────────────────────────────────────────────────
def entitlement_out(session: Session, ent: Entitlement) -> dict[str, Any]:
    used = len(session.exec(select(Device).where(Device.entitlement_id == ent.id)).all())
    return {
        "id": ent.id,
        "order_no": ent.order_no,
        "plan_name": ent.plan_name,
        "plan_code": ent.plan_code,
        "spec": json.loads(ent.spec_snapshot) if ent.spec_snapshot else {},
        "max_devices": ent.max_devices,
        "used_devices": used,
        "remaining_devices": max(0, ent.max_devices - used),
        "max_tasks": ent.max_tasks,
        "started_at": ent.started_at,
        "expires_at": ent.expires_at,
        "status": ent.status,
        "days_left": (
            max(0, (ent.expires_at - utcnow()).days) if ent.expires_at else None
        ),
    }


def active_entitlements(session: Session) -> list[Entitlement]:
    rows = session.exec(
        select(Entitlement).where(Entitlement.status == EntitlementStatus.active)
    ).all()
    return [e for e in rows if not e.expires_at or e.expires_at > utcnow()]


def quota_summary(session: Session) -> dict[str, Any]:
    ents = active_entitlements(session)
    total = sum(e.max_devices for e in ents)
    used = len(session.exec(select(Device).where(Device.entitlement_id.is_not(None))).all())  # type: ignore[union-attr]
    devices_total = len(session.exec(select(Device)).all())
    return {
        "billing_enabled": settings.billing_enabled,
        "enforce": settings.billing_enforce,
        "active_entitlements": [entitlement_out(session, e) for e in ents],
        "device_quota": total,
        "device_used": used,
        "device_remaining": max(0, total - used),
        "devices_total": devices_total,
        "max_tasks": sum(e.max_tasks for e in ents) if ents else None,
    }


def pick_entitlement(session: Session, plan_id: Optional[int] = None) -> Entitlement:
    """开设备时挑一份还有名额的权益。"""
    candidates = active_entitlements(session)
    if plan_id:
        candidates = [e for e in candidates if e.plan_id == plan_id]
    if not candidates:
        raise BillingError("没有可用的已购套餐，请先在「套餐」页购买" + (f"（plan_id={plan_id}）" if plan_id else ""))
    for ent in sorted(candidates, key=lambda e: e.expires_at or utcnow()):
        used = len(session.exec(select(Device).where(Device.entitlement_id == ent.id)).all())
        if used < ent.max_devices:
            return ent
    raise BillingError("已购套餐的设备名额已用满，请升级套餐或删除闲置设备")


def expire_stale(session: Session) -> dict[str, int]:
    """关闭超时订单、失效过期权益。由调度器周期调用。"""
    closed = 0
    for order in session.exec(select(Order).where(Order.status == OrderStatus.pending)).all():
        if order.expires_at and utcnow() > order.expires_at:
            if _paid_at_channel(order):
                # 渠道说已付就别关，交给 sync_order 走正常入账
                log.warning("订单 %s 超时但渠道已支付，跳过关闭", order.order_no)
                continue
            log.info("清理超时订单 %s（expires_at=%s）", order.order_no, order.expires_at)
            order.status = OrderStatus.closed
            order.closed_at = utcnow()
            session.add(order)
            closed += 1

    expired = 0
    for ent in session.exec(
        select(Entitlement).where(Entitlement.status == EntitlementStatus.active)
    ).all():
        if ent.expires_at and utcnow() > ent.expires_at:
            ent.status = EntitlementStatus.expired
            session.add(ent)
            expired += 1
    if closed or expired:
        session.commit()
    return {"orders_closed": closed, "entitlements_expired": expired}
