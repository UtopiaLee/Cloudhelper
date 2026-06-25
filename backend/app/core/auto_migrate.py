"""轻量 schema 同步：对比模型与 SQLite 现有表，缺失列就 ALTER TABLE ADD COLUMN 加上。

只处理"加列"这一种最常见演进，避免重启时丢账户数据。
不处理：改类型、删列、改约束、加索引——那些请走 alembic。
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.types import (
    BigInteger, Boolean, Date, DateTime, Float, Integer, JSON, String, Text,
)

from app.core.db import Base

log = logging.getLogger(__name__)


def _sql_type(col) -> str:
    t = col.type
    if isinstance(t, Boolean):
        return "BOOLEAN"
    if isinstance(t, (Integer, BigInteger)):
        return "INTEGER"
    if isinstance(t, Float):
        return "REAL"
    if isinstance(t, (String, Text)):
        return "TEXT"
    if isinstance(t, JSON):
        return "JSON"
    if isinstance(t, DateTime):
        return "DATETIME"
    if isinstance(t, Date):
        return "DATE"
    return "TEXT"


def _sql_default(col) -> str:
    if col.default is None:
        return ""
    arg = col.default.arg
    # callable default (like list/dict factory) — 不写默认，让 ORM 在写入时填
    if callable(arg):
        return ""
    if arg is None:
        return ""
    if isinstance(arg, bool):
        return f"DEFAULT {1 if arg else 0}"
    if isinstance(arg, (int, float)):
        return f"DEFAULT {arg}"
    if isinstance(arg, str):
        return f"DEFAULT '{arg}'"
    return ""


def _notnull_fallback_default(col, sql_type: str) -> str:
    """SQLite ALTER TABLE ADD COLUMN 加 NOT NULL 列必须带默认值。
    按列的 SQL 类型选一个安全的空值，避免给 JSON/数值列塞入 '' 导致后续解析崩溃。
    """
    if sql_type in ("INTEGER", "BOOLEAN", "REAL"):
        return "DEFAULT 0"
    if sql_type == "JSON":
        # 区分 dict / list 工厂，给出合法的空 JSON
        factory = getattr(col.default, "arg", None) if col.default is not None else None
        return "DEFAULT '[]'" if factory is list else "DEFAULT '{}'"
    if sql_type in ("DATETIME", "DATE"):
        return "DEFAULT CURRENT_TIMESTAMP"
    return "DEFAULT ''"


def auto_migrate(engine: Engine) -> None:
    """对所有 Base.metadata.tables 同步缺失列。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all 会建
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                sql_type = _sql_type(col)
                default = _sql_default(col)
                nullable = "" if col.nullable else " NOT NULL"
                # SQLite ALTER TABLE ADD COLUMN 不允许 NOT NULL 无默认值
                if not col.nullable and not default:
                    default = _notnull_fallback_default(col, sql_type)
                stmt = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {sql_type}{nullable} {default}'.strip()
                log.info("auto-migrate: %s", stmt)
                conn.execute(text(stmt))
