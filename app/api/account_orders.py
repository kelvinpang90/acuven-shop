"""Member-only order list, detail, and receipt-confirmation endpoints."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.member_auth import MemberDep, require_member_csrf
from app.api.order_lookup import ConfirmIn, _NoStoreRoute
from app.api.orders import IDEMPOTENCY_HEADER, _error, _key_errors
from app.api.pay import _body_errors, _conflict, _json_body, _language, _utcnow
from app.api.refunds import PointsEstimateOut, RefundIn, _line_errors
from app.db.session import get_session
from app.models import Order, OrderItemUnit
from app.models.order import ORDER_NUMBER_LENGTH, PAID_STATUSES, STATUS_AWAITING_PAYMENT
from app.services.account_orders import (
    PAGE_SIZE,
    find_account_order_id,
    list_account_orders,
)
from app.services.catalog import Language
from app.services.order_lookup import (
    ConfirmOutcome,
    FulfilmentFrozen,
    IdempotencyConflict,
    LookupOrder,
    OrderNotConfirmable,
    confirm_receipt,
    lookup_orders,
)
from app.services.payment import expire_overdue_orders
from app.services.points_refund import estimate_refund_points
from app.services.refunds import (
    IdempotencyConflict as RefundIdempotencyConflict,
)
from app.services.refunds import (
    OrderNotRefundable,
    RefundDuplicate,
    RefundLineRequest,
    RefundNothingLeft,
    RefundOutcome,
    RefundUnit,
    RefundWindowClosed,
    UnknownOrderLine,
    in_refund_window,
    pick_units,
    refund_state,
    submit_refund,
)

router = APIRouter(prefix="/api/member/orders", tags=["member"], route_class=_NoStoreRoute)

_PAGE = re.compile(r"[1-9][0-9]{0,4}")


class AccountOrderOut(BaseModel):
    order_number: str
    created_at: datetime
    status: str
    total_sen: int


class AccountOrdersOut(BaseModel):
    total: int
    page: int
    page_size: int
    orders: list[AccountOrderOut]


class ViewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str = Field(min_length=1, max_length=ORDER_NUMBER_LENGTH)


@dataclass(frozen=True)
class ConfirmInput:
    idempotency_key: str = field(repr=False)
    body: ConfirmIn = field(repr=False)


@dataclass(frozen=True)
class RefundInput:
    idempotency_key: str = field(repr=False)
    body: RefundIn = field(repr=False)


async def _order_body(request: Request) -> bytes:
    """Read and type-check the body before member authentication, as specified."""
    return await _json_body(request)


def _page(page: str = "1") -> int:
    """Accept only pages 1 through 10000 without reflecting the supplied value."""
    if not _PAGE.fullmatch(page) or int(page) > 10000:
        raise RequestValidationError(
            [
                _error(
                    ("query", "page"),
                    "page_invalid",
                    "Page should be an integer from 1 to 10000",
                )
            ]
        )
    return int(page)


OrderBodyDep = Annotated[bytes, Depends(_order_body)]
PageDep = Annotated[int, Depends(_page)]
LanguageDep = Annotated[Language, Depends(_language)]
SessionDep = Annotated[Session, Depends(get_session)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="order_not_found")


def _order_id(session: Session, member_id: int, order_number: str) -> int:
    order_id = find_account_order_id(session, member_id, order_number)
    if order_id is None:
        raise _not_found()
    return order_id


def _parse_view(raw: bytes) -> ViewIn:
    try:
        return ViewIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


def _parse_confirmation(request: Request, raw: bytes) -> ConfirmInput:
    errors = _key_errors(request)
    body: ConfirmIn | None = None
    try:
        body = ConfirmIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors or body is None:
        raise RequestValidationError(errors)
    return ConfirmInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], body=body)


def _parse_refund(request: Request, raw: bytes) -> RefundInput:
    errors = _key_errors(request)
    body: RefundIn | None = None
    try:
        body = RefundIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if body is not None:
        errors.extend(_line_errors(body))
    if errors or body is None:
        raise RequestValidationError(errors)
    return RefundInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], body=body)


def _parse_refund_estimate(raw: bytes) -> RefundIn:
    errors: list[dict[str, Any]] = []
    body: RefundIn | None = None
    try:
        body = RefundIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if body is not None:
        errors.extend(_line_errors(body))
    if errors or body is None:
        raise RequestValidationError(errors)
    return body


@router.get("", response_model=AccountOrdersOut)
def list_orders(member: MemberDep, page: PageDep, session: SessionDep) -> AccountOrdersOut:
    """Return only this member's orders, newest first, in fixed-size pages."""
    result = list_account_orders(session, member.member.id, page)
    return AccountOrdersOut(
        total=result.total,
        page=page,
        page_size=PAGE_SIZE,
        orders=[
            AccountOrderOut(
                order_number=row.order_number,
                created_at=row.created_at.replace(tzinfo=UTC),
                status=row.status,
                total_sen=row.total_sen,
            )
            for row in result.orders
        ],
    )


@router.post("/view", response_model=LookupOrder)
def view_order(
    raw: OrderBodyDep,
    member: MemberDep,
    lang: LanguageDep,
    session: SessionDep,
) -> LookupOrder:
    """Read the complete lookup view for an order belonging to the current member."""
    body = _parse_view(raw)
    member_id = member.member.id
    order_id = _order_id(session, member_id, body.order_number)
    now = _utcnow()
    # This can commit and expire ORM instances, so retain only the scalar member ID above.
    if session.get(Order, order_id).status == STATUS_AWAITING_PAYMENT:
        expire_overdue_orders(session, now, order_ids=[order_id])
    (view,) = lookup_orders(session, [order_id], lang, now)
    return view


@router.post("/confirm-receipt", response_model=ConfirmOutcome)
def confirm(
    raw: OrderBodyDep,
    member: MemberDep,
    session: SessionDep,
    request: Request,
) -> ConfirmOutcome | JSONResponse:
    """Confirm this member's shipped order after CSRF, then validate request structure."""
    require_member_csrf(request, member)
    confirmation = _parse_confirmation(request, raw)
    order_id = _order_id(session, member.member.id, confirmation.body.order_number)
    try:
        outcome, _ = confirm_receipt(
            session, order_id, confirmation.idempotency_key, _utcnow(), actor="member"
        )
    except IdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderNotConfirmable as exc:
        return _conflict("order_not_confirmable", exc.status)
    except FulfilmentFrozen as exc:
        return _conflict("fulfilment_frozen", exc.status)
    return outcome


@router.post("/refunds", status_code=201, response_model=RefundOutcome)
def request_refund(
    raw: OrderBodyDep,
    member: MemberDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> RefundOutcome | JSONResponse:
    """Submit a member's refund request after its CSRF check."""
    require_member_csrf(request, member)
    refund = _parse_refund(request, raw)
    order_id = _order_id(session, member.member.id, refund.body.order_number)
    lines = [RefundLineRequest(line.line_index, line.quantity) for line in refund.body.lines]
    try:
        outcome, created = submit_refund(
            session,
            order_id,
            lines,
            refund.idempotency_key,
            _utcnow(),
            actor="member",
        )
    except RefundIdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderNotRefundable:
        return _conflict("order_not_refundable")
    except RefundWindowClosed:
        return _conflict("refund_window_closed")
    except RefundNothingLeft:
        return _conflict("refund_nothing_left")
    except RefundDuplicate:
        return _conflict("refund_duplicate")
    except UnknownOrderLine:
        msg = "line_index is not a line of this order"
        raise RequestValidationError([_error(("body", "lines"), "value_error", msg)]) from None
    if not created:
        response.status_code = 200
    return outcome


@router.post("/refunds/points-estimate", response_model=PointsEstimateOut)
def estimate_refund_points_for_member(
    raw: OrderBodyDep,
    member: MemberDep,
    session: SessionDep,
) -> PointsEstimateOut | JSONResponse:
    """Read the member-order points effect of the requested refundable units."""
    body = _parse_refund_estimate(raw)
    order_id = _order_id(session, member.member.id, body.order_number)
    now = _utcnow()
    order = session.execute(
        select(Order.status, Order.paid_at, Order.member_id).where(Order.id == order_id)
    ).one()
    if order.status not in PAID_STATUSES or order.paid_at is None:
        return _conflict("order_not_refundable")
    if not in_refund_window(order.paid_at, now):
        return _conflict("refund_window_closed")

    state = refund_state(session, order_id)
    picked: list[tuple[int, tuple[RefundUnit, ...]]] = []
    for line in body.lines:
        line_state = state.line(line.line_index)
        if line_state is None:
            msg = "line_index is not a line of this order"
            raise RequestValidationError([_error(("body", "lines"), "value_error", msg)])
        picked.append((line.quantity, line_state.refundable))
    if any(quantity > len(refundable) for quantity, refundable in picked):
        return _conflict("refund_nothing_left")
    unit_ids = [
        unit.unit_id for quantity, refundable in picked for unit in pick_units(refundable, quantity)
    ]
    if order.member_id is None:
        return PointsEstimateOut(member_order=False, points_back=0, points_reversed=0)
    rows = session.execute(
        select(OrderItemUnit.points_discount, OrderItemUnit.points_earned).where(
            OrderItemUnit.id.in_(unit_ids)
        )
    ).all()
    estimate = estimate_refund_points(
        session,
        order_id,
        sum(row.points_discount for row in rows),
        sum(row.points_earned for row in rows),
        now,
    )
    return PointsEstimateOut(
        member_order=True,
        points_back=estimate.returned,
        points_reversed=estimate.reversed_total,
    )
