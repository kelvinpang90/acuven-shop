"""Alembic environment.

连接串只从 app.core.config 读（SHOP_DATABASE_URL），不写进 alembic.ini：配置只有一个入口，且不落盘。
还没有任何模型，target_metadata 为 None；第一个建表的编号任务再接上模型的 metadata。
"""

from __future__ import annotations

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings

config = context.config
target_metadata = None


def _database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("SHOP_DATABASE_URL is not set; alembic cannot run without a target.")
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (`alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
