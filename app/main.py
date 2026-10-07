"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.admin_auth import router as admin_auth_router
from app.api.admin_orders import router as admin_orders_router
from app.api.admin_refunds import router as admin_refunds_router
from app.api.admin_store_design import router as admin_store_design_router
from app.api.catalog import router as catalog_router
from app.api.checkout import router as checkout_router
from app.api.health import router as health_router
from app.api.order_lookup import router as order_lookup_router
from app.api.orders import router as orders_router
from app.api.pay import router as pay_router
from app.api.refunds import router as refunds_router
from app.api.regions import router as regions_router
from app.api.site_settings import router as site_settings_router
from app.api.store_design import router as store_design_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # 用工厂而不是只有模块级单例：测试可以传入自己的 Settings，互不干扰。
    settings = settings or get_settings()
    app = FastAPI(title="Acuven Shop")
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(catalog_router)
    app.include_router(checkout_router)
    app.include_router(regions_router)
    app.include_router(site_settings_router)
    app.include_router(store_design_router)
    app.include_router(orders_router)
    app.include_router(pay_router)
    app.include_router(order_lookup_router)
    app.include_router(refunds_router)
    app.include_router(admin_auth_router)
    app.include_router(admin_orders_router)
    app.include_router(admin_refunds_router)
    app.include_router(admin_store_design_router)
    return app


app = create_app()
