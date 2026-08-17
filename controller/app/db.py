from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, inspect, text
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


def _sqlite_add_missing_columns() -> None:
    """给已存在的表补上模型里新增的列。

    项目没引入 alembic，但升级时表里已经有用户数据，不能靠 create_all
    （它只建新表、不改旧表）。SQLite 的 ALTER TABLE ADD COLUMN 足够应付
    「加字段」这种唯一会发生的演进，其它结构变更请自行处理。
    """
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, table in SQLModel.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name in have:
                continue
            col_type = col.type.compile(engine.dialect)
            # 新加的列对已有行没有历史值，应该取模型里的默认值，
            # 而不是一律填 0/''（否则 bool 默认 True 的字段会变成 False）
            literal = _default_literal(col)
            ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}'
            if not col.nullable:
                ddl += f" NOT NULL DEFAULT {literal if literal is not None else 0}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                    if literal is not None:
                        conn.execute(
                            text(f'UPDATE "{table_name}" SET "{col.name}" = {literal}')
                        )
                log.info("已补充字段 %s.%s（默认值 %s）", table_name, col.name, literal)
            except Exception as exc:
                log.error("补充字段失败 %s.%s: %s", table_name, col.name, exc)


def _default_literal(col) -> object:  # noqa: ANN001
    """把模型默认值翻译成 SQL 字面量；拿不到默认值返回 None。"""
    default = getattr(col, "default", None)
    value = getattr(default, "arg", None) if default is not None else None
    if callable(value) or value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def init_db() -> None:
    settings.ensure_dirs()
    from . import models  # noqa: F401  确保模型已注册

    SQLModel.metadata.create_all(engine)
    _sqlite_add_missing_columns()
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
