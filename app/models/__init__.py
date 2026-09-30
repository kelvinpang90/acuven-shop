"""全部模型；import 本包即把它们注册到 app.db.base.Base.metadata。"""

from app.models.catalog import (
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)
from app.models.shipping import DemoFxRate, ShippingRate

__all__ = [
    "Category",
    "DemoFxRate",
    "Product",
    "ProductImage",
    "ProductOption",
    "ProductOptionValue",
    "ProductVariant",
    "ShippingRate",
    "VariantOptionValue",
]
