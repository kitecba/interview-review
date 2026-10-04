"""数据库引擎。

单机自用选 SQLite：一个文件、零配置、拷走即全部数据。
两个 PRAGMA 是必须的：
  - journal_mode=WAL   允许读写并发，避免流水线写进度时阻塞查询
  - foreign_keys=ON    SQLite 默认不校验外键，必须显式打开
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

_engine: Engine | None = None


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            echo=False,
            # SQLite 默认禁止跨线程复用连接；FastAPI 的线程池需要放开
            connect_args={"check_same_thread": False},
        )
        event.listen(_engine, "connect", _configure_sqlite)
    return _engine


def init_db() -> None:
    """建表。SQLModel.metadata 已由各模型模块 import 时填充。"""
    from app.db import models  # noqa: F401  仅为触发模型注册

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI 依赖注入用的会话工厂。"""
    with Session(get_engine()) as session:
        yield session
