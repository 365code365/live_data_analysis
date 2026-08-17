from sqlmodel import Session, select

from app.db import engine
from app.models import Order, OrderStatus, utcnow

with Session(engine) as s:
    o = s.exec(select(Order).order_by(Order.id.desc())).first()
    print(" order_no:", o.order_no)
    print(" status:", repr(o.status), type(o.status))
    print(" status == pending ?", o.status == OrderStatus.pending)
    print(" status != pending ?", o.status != OrderStatus.pending)
    print(" expires_at:", repr(o.expires_at), type(o.expires_at))
    now = utcnow()
    print(" utcnow:", now, type(now))
    try:
        print(" now > expires_at ?", now > o.expires_at)
    except Exception as exc:
        print(" 比较异常:", exc)
    print(" channel:", repr(o.channel), type(o.channel))
    try:
        print(" channel.value:", o.channel.value)
    except Exception as exc:
        print(" channel.value 异常:", exc)
