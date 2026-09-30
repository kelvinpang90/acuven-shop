"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.catalog import router as catalog_router
from app.api.health import router as health_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # 用工厂而不是只有模块级单例：测试可以传入自己的 Settings，互不干扰。
    settings = settings or get_settings()
    app = FastAPI(title="Acuven Shop")
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(catalog_router)
    return app


app = create_app()
