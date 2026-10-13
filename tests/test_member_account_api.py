"""会员中心只读接口；断言守住权限与资料保护中的会员会话要求。"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app


def test_member_account_endpoints_require_a_member_session() -> None:
    """守住 DESIGN 1.11「会员仅能访问自己的资料」：没有会员会话一律为 401。"""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys = ON"))
    Base.metadata.create_all(engine)
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))

    def session() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session
    with TestClient(app, base_url="https://testserver") as client:
        for path in ("/api/member/points", "/api/member/coupons"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.json() == {"detail": "member_session_required"}
            assert response.headers["Cache-Control"] == "no-store"
    engine.dispose()
