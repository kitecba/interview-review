"""数据库引擎。

单机自用选 SQLite：一个文件、零配置、拷走即全部数据。
两个 PRAGMA 是必须的：
  - journal_mode=WAL   允许读写并发，避免流水线写进度时阻塞查询
  - foreign_keys=ON    SQLite 默认不校验外键，必须显式打开
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

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
    """建表，并给已有表补上新增的列。

    SQLModel.metadata 已由各模型模块 import 时填充。
    """
    from app.db import models  # noqa: F401  仅为触发模型注册

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine) -> list[str]:
    """给已存在的表补上模型里新增的列。

    为什么不用 Alembic：这是一个单机自用、schema 还在快速变动的项目，
    引入迁移框架的成本大于收益。而更现实的原因是 —— 每次重建数据库都要把
    已经花过钱的语音转写重新跑一遍，代价不小。

    只处理可以安全追加的列（可空、无 server_default）。其余的只告警，
    因为 SQLite 的 ALTER TABLE 能力有限，改类型或加非空列需要重建表。
    """
    from sqlalchemy import inspect, text

    added: list[str] = []
    inspector = inspect(engine)

    for table_name, table in SQLModel.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue

        existing = {col["name"] for col in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in existing:
                continue

            if not column.nullable and column.server_default is None:
                logger.warning(
                    "列 %s.%s 是非空且无默认值，SQLite 无法直接追加，需要手动迁移",
                    table_name,
                    column.name,
                )
                continue

            col_type = column.type.compile(dialect=engine.dialect)
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}')
                    )
                added.append(f"{table_name}.{column.name}")
            except Exception as exc:  # pragma: no cover
                logger.warning("追加列失败 %s.%s：%s", table_name, column.name, exc)

    if added:
        logger.info("已为已有表补上 %d 个新列：%s", len(added), "、".join(added))

    return added


def get_session() -> Iterator[Session]:
    """FastAPI 依赖注入用的会话工厂。"""
    with Session(get_engine()) as session:
        yield session
