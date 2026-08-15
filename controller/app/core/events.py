from __future__ import annotations

import logging
from typing import Optional

from ..db import session_scope
from ..models import EventLog

log = logging.getLogger("ldm.event")


def emit(
    message: str,
    *,
    level: str = "info",
    source: str = "system",
    device_id: Optional[int] = None,
    task_id: Optional[int] = None,
) -> None:
    """写一条事件日志：既进 python logger，也落库给控制台展示。"""
    getattr(log, level if level in {"debug", "info", "warning", "error"} else "info")(
        "[%s] %s", source, message
    )
    try:
        with session_scope() as session:
            session.add(
                EventLog(
                    level=level,
                    source=source,
                    message=message[:2000],
                    device_id=device_id,
                    task_id=task_id,
                )
            )
    except Exception:  # 事件日志不应该影响主流程
        log.exception("事件写库失败")
