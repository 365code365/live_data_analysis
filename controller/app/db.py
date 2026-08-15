from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

log = logging.getLogger(__name__)

_connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args,
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
    """SQLite 下开 WAL，采集线程与 API 线程并发读写不会互相锁死。"""
    if not settings.database_url.startswith("sqlite"):
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def init_db() -> None:
    settings.ensure_dirs()
    from . import models  # noqa: F401  确保模型已注册

    SQLModel.metadata.create_all(engine)
    log.info("数据库已就绪: %s", settings.database_url)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台线程用：出错回滚，正常提交。"""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
