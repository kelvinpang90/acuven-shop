"""数据库引擎与会话，按 SHOP_DATABASE_URL 惰性创建。

import 本模块与启动应用都不连库：引擎在第一次有请求要用会话时才建，同一连接串只建一次。
未配置数据库时应用照常启动、健康检查照常回答；只有要用会话的接口返回 503。
测试用 app.dependency_overrides[get_session] 换成自己的会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache
def _session_factory(database_url: str) -> sessionmaker[Session]:
    # pool_pre_ping：共享 MySQL 会断开空闲连接，取用前先探一下，避免请求撞上死连接。
    return sessionmaker(create_engine(database_url, pool_pre_ping=True))


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，请求结束关闭；不提交，未提交的改动随关闭回滚。"""
    database_url = request.app.state.settings.database_url
    if not database_url:
        raise HTTPException(status_code=503, detail="database is not configured")
    with _session_factory(database_url)() as session:
        yield session
