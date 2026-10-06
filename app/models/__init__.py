"""全部模型；import 本包即把它们注册到 app.db.base.Base.metadata。"""

from app.models.admin import AdminAccount, AdminSession, AuditEvent
from app.models.catalog import (
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)
from app.models.member import Member, MemberSession, SmsDailyUsage, VerificationAttempt
from app.models.order import (
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    PaymentAttempt,
    ReceiptConfirmation,
)
from app.models.order_access import OrderAccessGrant, OrderAccessSession
from app.models.refund import RefundLine, RefundLineUnit, RefundRequest
from app.models.shipping import DemoFxRate, ShippingRate
from app.models.site import SiteSetting
from app.models.stock_reset import StockReset, StockResetLine

__all__ = [
    "AdminAccount",
    "AdminSession",
    "AuditEvent",
    "Category",
    "DemoFxRate",
    "Member",
    "MemberSession",
    "Order",
    "OrderAccessGrant",
    "OrderAccessSession",
    "OrderEvent",
    "OrderItem",
    "OrderItemUnit",
    "OrderRecipient",
    "PaymentAttempt",
    "Product",
    "ProductImage",
    "ProductOption",
    "ProductOptionValue",
    "ProductVariant",
    "ReceiptConfirmation",
    "RefundLine",
    "RefundLineUnit",
    "RefundRequest",
    "ShippingRate",
    "SiteSetting",
    "SmsDailyUsage",
    "StockReset",
    "StockResetLine",
    "VariantOptionValue",
    "VerificationAttempt",
]
