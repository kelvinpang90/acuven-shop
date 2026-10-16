"""Read-only order queries scoped to one member account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.order_lookup import normalize_order_number
from app.services.order_rules import is_valid_order_number

PAGE_SIZE = 20


@dataclass(frozen=True)
class AccountOrder:
    """The small order summary used by the member's order list."""

    order_number: str
    created_at: datetime
    status: str
    total_sen: int


@dataclass(frozen=True)
class AccountOrders:
    """One page of a member's orders and the count across all pages."""

    total: int
    orders: list[AccountOrder]


def list_account_orders(db: Session, member_id: int, page: int) -> AccountOrders:
    """Return a 20-order page belonging to member_id, newest first, without writing."""
    if page < 1:
        raise ValueError("page must be positive")
    statement = select(Order).where(Order.member_id == member_id)
    with db.no_autoflush:
        total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        rows = db.scalars(
            statement.order_by(Order.created_at.desc(), Order.id.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).all()
    return AccountOrders(
        total=total,
        orders=[
            AccountOrder(
                order_number=order.order_number,
                created_at=order.created_at,
                status=order.status,
                total_sen=order.total_sen,
            )
            for order in rows
        ],
    )


def find_account_order_id(db: Session, member_id: int, order_number: str) -> int | None:
    """Return an order ID only when the normalized, valid number belongs to member_id."""
    number = normalize_order_number(order_number)
    if not is_valid_order_number(number):
        return None
    with db.no_autoflush:
        return db.scalar(
            select(Order.id).where(Order.member_id == member_id, Order.order_number == number)
        )
