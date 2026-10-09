"""短信验证后登录或注册与认领游客订单（app/services/member_sms_login.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 1 条（下称「第 1 条」）：“验证通过
  后，号码已注册且账号有效则登录该账号，未注册则创建无密码会员并登录，随后按下一条的规则
  自动认领该号码的游客订单”；第 2 条（下称「第 2 条」）：“注册、查单和订单认领都用相同的
  E.164 规范化结果”，“认领只查近 30 天且未认领的游客订单”；第 3 条（下称「第 3 条」）：
  “注册、短信登录与结账验证是同一个短信验证流程：验证通过后号码已注册则登录，未注册则
  创建会员并登录”，“游客订单被认领时写入不含手机号、不可撤销的「已认领」标记，会员下单的
  订单创建时即标记为不可认领，注销清空会员 ID 后这些标记保留，重新注册不能据此恢复旧订单
  的访问或积分”，“每个游客订单只能认领一次，注销后同号重注册不得再次认领或补发积分”，
  “短信验证开关关闭期间：注册、短信登录与密码重设暂停”。
- 「计价、优惠、积分与库存」第 3 条（下称「积分第 3 条」）：“认领过去游客订单不追发积分”。
- 「边界与原则」第 4 条（下称「原则第 4 条」）：“开关在流程途中被关闭后，提交的验证码不再
  核验”；「失败、并发与重试」第 4 条（下称「失败第 4 条」）：“不得把验证码、短信凭据或完整
  手机号写进日志”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 10-08」）：“会员会话 30 天”。
没有直接原句的，写明是 SHOP-TASK-075 验收标准里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_sms_verification.py 相同，由引擎发 BEGIN，
保存点与 MySQL 上一样）。服务商用 SHOP-TASK-067 的测试替身 FakeSmsProvider；验证记录、会员与
订单直接写库。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, func, insert, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Member,
    MemberSession,
    Order,
    OrderAccessGrant,
    OrderAccessSession,
    OrderEvent,
    OrderRecipient,
    SiteSetting,
    VerificationAttempt,
)
from app.models.order import (
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PAID,
)
from app.models.site import SITE_SETTING_ID
from app.services import member_sms_login
from app.services.member_auth import check_member_session
from app.services.member_sms_login import (
    MemberSmsLoginError,
    SmsLoginResult,
    sms_login_or_register,
)
from app.services.phone import InvalidPhoneNumber
from app.services.sms_provider import CheckStatus, FakeSmsProvider
from app.services.sms_verification import SmsVerificationError, VerifyStatus

NOW = datetime(2026, 10, 9, 3, 0, 0)
FIVE_MINUTES_AGO = NOW - timedelta(minutes=5)
THREE_DAYS_AGO = NOW - timedelta(days=3)
PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
CODE = "482915"
PASSWORD_HASH = "scrypt$existing-password-hash"
FINGERPRINT = "0" * 64

_REQUEST_IDS = itertools.count(1)
_ORDER_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 与 tests/test_sms_verification.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
    # 保存点与提交才和 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.rollback()
        yield session


@pytest.fixture
def enabled(db: Session) -> None:
    set_switch(db, True)


class RaisingProvider(FakeSmsProvider):
    """核验时抛出带号码与验证码的异常（模拟服务商调用出错）。"""

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        super().check_verification(request_id, code)
        raise RuntimeError(f"check failed for {PHONE_MY} with {CODE}")


def set_switch(db: Session, value: bool) -> None:
    row = db.get(SiteSetting, SITE_SETTING_ID)
    if row is None:
        db.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=value))
    else:
        row.sms_verification_enabled = value
    db.commit()


def add_attempt(
    db: Session,
    purpose: str = "login",
    phone: str = PHONE_MY,
    created_at: datetime = FIVE_MINUTES_AGO,
) -> int:
    """一条待核验（sent）的验证记录，请求 ID 逐次不同。"""
    result = db.execute(
        insert(VerificationAttempt).values(
            phone=phone,
            purpose=purpose,
            status="sent",
            provider_request_id=f"VE{next(_REQUEST_IDS):032x}",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def attempt_status(db: Session, attempt_id: int) -> str:
    return db.execute(
        select(VerificationAttempt.status).where(VerificationAttempt.id == attempt_id)
    ).scalar_one()


def add_member(
    db: Session,
    phone: str | None = PHONE_MY,
    password_hash: str | None = PASSWORD_HASH,
    status: str = "active",
) -> int:
    deleted = status == "deleted"
    result = db.execute(
        insert(Member).values(
            phone=None if deleted else phone,
            password_hash=None if deleted else password_hash,
            status=status,
            created_at=NOW - timedelta(days=100),
            deleted_at=NOW - timedelta(days=50) if deleted else None,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def add_order(
    db: Session,
    phone: str = PHONE_MY,
    created_at: datetime = THREE_DAYS_AGO,
    claim_status: str = CLAIM_OPEN,
    member_id: int | None = None,
    status: str = STATUS_AWAITING_PAYMENT,
) -> int:
    """一张订单及其收货资料；直接写库，不进会话的对象表。"""
    n = next(_ORDER_SEQUENCE)
    paid = status in PAID_STATUSES
    result = db.execute(
        insert(Order).values(
            order_number=f"{n:016d}",
            status=status,
            subtotal_sen=2500,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=800,
            total_sen=3300,
            points_earned=25 if paid else 0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"order-key-{n}",
            request_fingerprint=FINGERPRINT,
            created_at=created_at,
            payment_expires_at=created_at + timedelta(minutes=15),
            paid_at=created_at + timedelta(minutes=1) if paid else None,
            member_id=member_id,
            claim_status=claim_status,
        )
    )
    order_id = result.inserted_primary_key[0]
    db.execute(
        insert(OrderRecipient).values(
            order_id=order_id,
            name="Demo Recipient",
            phone=phone,
            country_code="MY",
            region="MY-10",
            address="1 Jalan Demo",
            postal_code="50000",
        )
    )
    db.commit()
    return order_id


def claim_of(db: Session, order_id: int) -> tuple[int | None, str]:
    """订单的（会员 ID, 认领状态）。"""
    stmt = select(Order.member_id, Order.claim_status).where(Order.id == order_id)
    member_id, claim_status = db.execute(stmt).one()
    return member_id, claim_status


def table_rows(db: Session, table) -> list[dict]:
    return [dict(row) for row in db.execute(select(table).order_by(table.c.id)).mappings()]


def count(db: Session, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def login(
    db: Session,
    provider: FakeSmsProvider | None = None,
    phone: str = PHONE_MY,
    purpose: str = "login",
    code: str = CODE,
    now: datetime = NOW,
) -> SmsLoginResult:
    return sms_login_or_register(db, provider or FakeSmsProvider(), phone, purpose, code, now)


def assert_sanitized(exc: BaseException) -> None:
    """异常消息不含号码与验证码，也不挂原异常（异常链里可能带着它们）。"""
    for text_value in (PHONE_MY, PHONE_MY[1:], CODE):
        assert text_value not in str(exc) and text_value not in repr(exc)
    assert exc.__cause__ is None and exc.__context__ is None


# ---------------------------------------------------------------------------
# 用途与入参
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ["checkout", "register", "login"])
def test_three_purposes_share_one_flow(db: Session, enabled: None, purpose: str) -> None:
    """第 3 条：“注册、短信登录与结账验证是同一个短信验证流程：验证通过后号码已注册则登录，
    未注册则创建会员并登录”；第 1 条：随后自动认领该号码的游客订单。三个用途结果相同。
    """
    attempt_id = add_attempt(db, purpose=purpose)
    order_id = add_order(db)
    result = login(db, purpose=purpose)
    assert result.verification.status is VerifyStatus.APPROVED
    assert result.verification.attempt_id == attempt_id
    assert result.created_member is True
    assert result.claimed_orders == 1
    assert result.member is not None and result.issued is not None
    assert claim_of(db, order_id) == (result.member.id, CLAIM_CLAIMED)
    assert attempt_status(db, attempt_id) == "approved"


@pytest.mark.parametrize("purpose", ["reset_password", "delete_account", "unknown", "LOGIN"])
def test_other_purposes_are_rejected_without_verifying(
    db: Session, enabled: None, purpose: str
) -> None:
    """验收：用途只接受 checkout、register、login，其余（含 reset_password、delete_account）
    抛 ValueError，不核验（不调用服务商、验证记录不变）。
    """
    attempt_id = add_attempt(db, purpose="reset_password")
    provider = FakeSmsProvider()
    with pytest.raises(ValueError) as excinfo:
        login(db, provider=provider, purpose=purpose)
    assert provider.check_calls == 0
    assert attempt_status(db, attempt_id) == "sent"
    assert count(db, Member) == 0
    assert PHONE_MY not in str(excinfo.value) and CODE not in str(excinfo.value)


@pytest.mark.parametrize(
    "phone", ["+60123", "60123456789", " +60123456789", "+60 12-345 6789", "+0123456789"]
)
def test_unnormalized_phone_raises_without_verifying(
    db: Session, enabled: None, phone: str
) -> None:
    """第 2 条：“注册、查单和订单认领都用相同的 E.164 规范化结果”。验收：号码不是规范化
    E.164 时抛 InvalidPhoneNumber，不核验、不写库，消息不含号码。
    """
    attempt_id = add_attempt(db)
    add_order(db)
    provider = FakeSmsProvider()
    with pytest.raises(InvalidPhoneNumber) as excinfo:
        login(db, provider=provider, phone=phone)
    assert provider.check_calls == 0
    db.rollback()
    assert attempt_status(db, attempt_id) == "sent"
    assert count(db, Member) == 0
    assert phone.strip() not in str(excinfo.value)


def test_aware_now_is_rejected(db: Session, enabled: None) -> None:
    """验收：当前时间是不带时区的 UTC（写法同 app/services/member_auth.py）。

    带时区即拒绝、不核验。
    """
    add_attempt(db)
    provider = FakeSmsProvider()
    with pytest.raises(ValueError):
        login(db, provider=provider, now=NOW.replace(tzinfo=UTC))
    assert provider.check_calls == 0


# ---------------------------------------------------------------------------
# 核验不通过
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("wrong_code", VerifyStatus.WRONG_CODE),
        ("expired", VerifyStatus.EXPIRED),
        ("unavailable", VerifyStatus.UNAVAILABLE),
        ("no_pending", VerifyStatus.NO_PENDING),
        ("sms_disabled", VerifyStatus.SMS_DISABLED),
    ],
)
def test_non_approved_results_do_nothing(
    db: Session, enabled: None, case: str, expected: VerifyStatus
) -> None:
    """第 1 条：只有“验证通过后”才登录或注册并认领；原则第 4 条与第 3 条：开关在流程途中被
    关闭后提交的验证码不再核验、注册与短信登录暂停。验收：结果不是 approved 时原样返回，
    不查会员、不建会员、不认领、不签发会话。
    """
    if case != "no_pending":
        add_attempt(db)
    order_id = add_order(db)
    if case == "sms_disabled":
        set_switch(db, False)
    check = {
        "wrong_code": CheckStatus.WRONG_CODE,
        "expired": CheckStatus.EXPIRED,
        "unavailable": CheckStatus.UNAVAILABLE,
    }.get(case, CheckStatus.APPROVED)
    provider = FakeSmsProvider(check_result=check)
    result = login(db, provider=provider)
    assert result.verification.status is expected
    assert result.member is None and result.issued is None
    assert result.claimed_orders == 0 and result.created_member is False
    assert count(db, Member) == 0
    assert count(db, MemberSession) == 0
    assert claim_of(db, order_id) == (None, CLAIM_OPEN)
    if case in ("no_pending", "sms_disabled"):
        assert provider.check_calls == 0


def test_non_approved_result_does_not_touch_existing_member(db: Session, enabled: None) -> None:
    """第 1 条：只有“验证通过后”才登录。验收：验证码错误时不登录已注册的会员、不签发会话、
    不认领。
    """
    member_id = add_member(db)
    add_attempt(db)
    order_id = add_order(db)
    result = login(db, provider=FakeSmsProvider(check_result=CheckStatus.WRONG_CODE))
    assert result.verification.status is VerifyStatus.WRONG_CODE
    assert result.member is None
    assert count(db, MemberSession) == 0
    assert claim_of(db, order_id) == (None, CLAIM_OPEN)
    assert db.get(Member, member_id).password_hash == PASSWORD_HASH


def test_provider_error_is_passed_through(db: Session, enabled: None) -> None:
    """验收：verify_code 的 SmsVerificationError 原样交给调用方（消息不含号码与验证码）；
    不建会员、不签发会话。
    """
    add_attempt(db)
    with pytest.raises(SmsVerificationError) as excinfo:
        login(db, provider=RaisingProvider())
    assert_sanitized(excinfo.value)
    db.rollback()
    assert count(db, Member) == 0
    assert count(db, MemberSession) == 0


# ---------------------------------------------------------------------------
# 登录或注册
# ---------------------------------------------------------------------------


def test_registered_phone_logs_in_existing_member(db: Session, enabled: None) -> None:
    """第 1 条：“号码已注册且账号有效则登录该账号”。验收：密码哈希不变，不新建会员。"""
    member_id = add_member(db)
    add_attempt(db)
    result = login(db)
    assert result.created_member is False
    assert result.member is not None and result.member.id == member_id
    db.commit()
    assert count(db, Member) == 1
    member = db.get(Member, member_id)
    assert member.phone == PHONE_MY
    assert member.password_hash == PASSWORD_HASH
    assert member.status == "active"


def test_registered_member_without_password_logs_in(db: Session, enabled: None) -> None:
    """第 3 条：“未设密码的账号只能短信登录”。没有密码哈希的会员照样以短信登录、哈希仍为空。"""
    member_id = add_member(db, password_hash=None)
    add_attempt(db)
    result = login(db)
    assert result.member is not None and result.member.id == member_id
    assert result.created_member is False
    assert db.get(Member, member_id).password_hash is None


def test_unregistered_phone_creates_passwordless_member(db: Session, enabled: None) -> None:
    """第 1 条：“未注册则创建无密码会员并登录”。验收：active、无密码哈希，创建时间为当前时间。
    另一号码的会员不被登录。
    """
    other_id = add_member(db, phone=PHONE_SG)
    add_attempt(db)
    result = login(db)
    assert result.created_member is True
    assert result.member is not None and result.member.id != other_id
    db.commit()
    member = db.get(Member, result.member.id)
    assert member.phone == PHONE_MY
    assert member.password_hash is None
    assert member.status == "active"
    assert member.created_at == NOW
    assert member.deleted_at is None


def test_unique_conflict_logs_in_member_registered_concurrently(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：创建放在保存点里，撞上 members 的号码唯一约束（并发注册同一号码）时回到保存点，
    以加锁读（SELECT … FOR UPDATE）改读已存在的 active 会员登录，不报错。另一个请求在本次
    查找之后、插入之前注册同一号码（包住查找函数，在第一次不加锁的查找没找到之后直接写库
    模拟）。保存点之前的核验记录仍为 approved；会员只有对方那一个；加锁读按 MySQL 方言
    编译带 FOR UPDATE（SQLite 不生成）。
    """
    attempt_id = add_attempt(db)
    order_id = add_order(db)
    original = member_sms_login._active_member
    reads: list[tuple[bool, bool]] = []
    racer: list[int] = []

    def _racing(session: Session, phone: str, *, lock: bool) -> Member | None:
        found = original(session, phone, lock=lock)
        reads.append((lock, found is not None))
        if not lock and found is None and not racer:
            inserted = session.execute(
                insert(Member).values(
                    phone=phone,
                    password_hash=None,
                    status="active",
                    created_at=NOW - timedelta(seconds=1),
                    deleted_at=None,
                )
            )
            racer.append(inserted.inserted_primary_key[0])
        return found

    member_selects: list[str] = []

    @event.listens_for(db, "do_orm_execute")
    def _record(state) -> None:
        if state.is_select:
            sql = str(state.statement.compile(dialect=mysql.dialect()))
            if "FROM members" in sql:
                member_selects.append(sql)

    monkeypatch.setattr(member_sms_login, "_active_member", _racing)
    result = login(db)
    assert reads == [(False, False), (True, True)]
    assert result.created_member is False
    assert result.member is not None and result.member.id == racer[0]
    assert result.issued is not None
    assert result.claimed_orders == 1
    assert len(member_selects) == 2
    assert "FOR UPDATE" not in member_selects[0]
    assert "FOR UPDATE" in member_selects[1]
    assert attempt_status(db, attempt_id) == "approved"
    assert count(db, Member) == 1
    assert claim_of(db, order_id) == (racer[0], CLAIM_CLAIMED)


def test_unique_conflict_without_active_member_is_a_store_error(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：撞上约束后加锁读仍找不到时按本函数自己的数据库错误处理，不建会员、不签发会话；
    异常不含号码（撞约束时 IntegrityError 的消息带 INSERT 参数里的号码）且不挂原异常。
    号码已有 active 会员、查找函数被换成总是找不到，模拟两次都读不到。
    """
    member_id = add_member(db)
    add_attempt(db)
    order_id = add_order(db)
    monkeypatch.setattr(member_sms_login, "_active_member", lambda *_args, **_kwargs: None)
    with pytest.raises(MemberSmsLoginError) as excinfo:
        login(db)
    assert_sanitized(excinfo.value)
    db.rollback()
    assert [row["id"] for row in table_rows(db, Member.__table__)] == [member_id]
    assert count(db, MemberSession) == 0
    assert claim_of(db, order_id) == (None, CLAIM_OPEN)


def test_deleted_member_reverification_creates_new_member(db: Session, enabled: None) -> None:
    """第 3 条：“注销清空会员 ID 后这些标记保留，重新注册不能据此恢复旧订单的访问或积分”，
    “注销后同号重注册不得再次认领或补发积分”。验收：已注销的会员（号码已清空）不会被找到，
    同号重新验证创建新会员，旧会员的 claimed 订单（注销后 member_id 已清空）不被再次认领。
    """
    old_id = add_member(db, status="deleted")
    old_claimed = add_order(db, claim_status=CLAIM_CLAIMED, member_id=None)
    add_attempt(db)
    result = login(db)
    assert result.created_member is True
    assert result.member is not None and result.member.id != old_id
    assert result.claimed_orders == 0
    assert claim_of(db, old_claimed) == (None, CLAIM_CLAIMED)
    old = db.get(Member, old_id)
    assert (old.phone, old.status) == (None, "deleted")


# ---------------------------------------------------------------------------
# 认领
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("created_at", "claimed"),
    [
        (NOW - timedelta(days=30), True),
        (NOW, True),
        (NOW - timedelta(days=30, seconds=1), False),
        (NOW + timedelta(seconds=1), False),
    ],
)
def test_claim_window_is_last_30_days(
    db: Session, enabled: None, created_at: datetime, claimed: bool
) -> None:
    """第 2 条：“认领只查近 30 天且未认领的游客订单”。验收：created_at 在当前时间减 30 天与
    当前时间之间，两端都含（恰满 30 天仍认领），早一秒不认领，创建时间晚于当前时间的不认领。
    """
    order_id = add_order(db, created_at=created_at)
    add_attempt(db)
    result = login(db)
    assert result.member is not None
    if claimed:
        assert result.claimed_orders == 1
        assert claim_of(db, order_id) == (result.member.id, CLAIM_CLAIMED)
    else:
        assert result.claimed_orders == 0
        assert claim_of(db, order_id) == (None, CLAIM_OPEN)


def test_claim_only_open_unowned_orders_of_same_phone(db: Session, enabled: None) -> None:
    """第 3 条：“每个游客订单只能认领一次”，“会员下单的订单创建时即标记为不可认领”；
    第 1 条：认领“该号码的”游客订单。验收：只认领收货电话相同、open 且 member_id 为空的订单，
    不论订单状态；其他号码、claimed 与 not_claimable 的订单（含 member_id 已清空的）不变。
    """
    other_member = add_member(db, phone=PHONE_SG)
    awaiting = add_order(db, status=STATUS_AWAITING_PAYMENT)
    paid = add_order(db, status=STATUS_PAID)
    completed = add_order(db, status=STATUS_COMPLETED)
    cancelled = add_order(db, status=STATUS_CANCELLED)
    other_phone = add_order(db, phone=PHONE_SG)
    claimed_by_other = add_order(db, claim_status=CLAIM_CLAIMED, member_id=other_member)
    claimed_cleared = add_order(db, claim_status=CLAIM_CLAIMED, member_id=None)
    member_order = add_order(db, claim_status=CLAIM_NOT_CLAIMABLE, member_id=other_member)
    member_order_cleared = add_order(db, claim_status=CLAIM_NOT_CLAIMABLE, member_id=None)
    add_attempt(db)
    result = login(db)
    assert result.member is not None
    me = result.member.id
    assert result.claimed_orders == 4
    for order_id in (awaiting, paid, completed, cancelled):
        assert claim_of(db, order_id) == (me, CLAIM_CLAIMED)
    assert claim_of(db, other_phone) == (None, CLAIM_OPEN)
    assert claim_of(db, claimed_by_other) == (other_member, CLAIM_CLAIMED)
    assert claim_of(db, claimed_cleared) == (None, CLAIM_CLAIMED)
    assert claim_of(db, member_order) == (other_member, CLAIM_NOT_CLAIMABLE)
    assert claim_of(db, member_order_cleared) == (None, CLAIM_NOT_CLAIMABLE)


def test_second_verification_does_not_claim_again(db: Session, enabled: None) -> None:
    """第 3 条：“每个游客订单只能认领一次”。验收：每次短信验证通过都执行一次认领（登录与新注册
    相同）；第二次（已注册、登录原会员）不重复认领已认领的订单，只认领之后新出现的游客订单。
    """
    first_order = add_order(db)
    add_attempt(db)
    first = login(db)
    assert first.member is not None
    member_id = first.member.id
    assert (first.created_member, first.claimed_orders) == (True, 1)
    db.commit()

    later = NOW + timedelta(hours=1)
    new_order = add_order(db, created_at=later - timedelta(minutes=30))
    add_attempt(db, created_at=later - timedelta(minutes=1))
    second = login(db, now=later)
    assert second.member is not None and second.member.id == member_id
    assert (second.created_member, second.claimed_orders) == (False, 1)
    assert claim_of(db, first_order) == (member_id, CLAIM_CLAIMED)
    assert claim_of(db, new_order) == (member_id, CLAIM_CLAIMED)

    db.commit()
    add_attempt(db, created_at=later)
    third = login(db, now=later + timedelta(minutes=1))
    assert third.claimed_orders == 0


def test_claim_awards_no_points_and_writes_no_events(db: Session, enabled: None) -> None:
    """积分第 3 条：“认领过去游客订单不追发积分”；第 3 条：认领只写“已认领”标记。验收：不发
    积分、不写订单事件、不改订单状态与收货资料、不影响游客短期凭据与查单授权——订单除会员
    ID 与认领状态之外每列不变，收货资料、订单事件、订单访问会话与授权、会员会话之外的表不变。
    """
    order_id = add_order(db, status=STATUS_PAID)
    db.execute(
        insert(OrderEvent).values(
            order_id=order_id,
            from_status=STATUS_AWAITING_PAYMENT,
            to_status=STATUS_PAID,
            actor_type="guest",
            created_at=NOW - timedelta(days=3),
        )
    )
    session_id = db.execute(
        insert(OrderAccessSession).values(
            token_hash="a" * 64,
            created_at=NOW - timedelta(minutes=5),
            expires_at=NOW + timedelta(minutes=25),
            revoked_at=None,
        )
    ).inserted_primary_key[0]
    db.execute(
        insert(OrderAccessGrant).values(
            session_id=session_id,
            order_id=order_id,
            scope="lookup",
            created_at=NOW - timedelta(minutes=5),
            expires_at=NOW + timedelta(minutes=25),
            revoked_at=None,
        )
    )
    db.commit()
    add_attempt(db)
    unchanged_tables = (
        OrderRecipient.__table__,
        OrderEvent.__table__,
        OrderAccessSession.__table__,
        OrderAccessGrant.__table__,
    )
    before = {table.name: table_rows(db, table) for table in unchanged_tables}
    order_before = table_rows(db, Order.__table__)[0]

    result = login(db)
    assert result.member is not None and result.claimed_orders == 1
    after = {table.name: table_rows(db, table) for table in unchanged_tables}
    assert after == before
    order_after = table_rows(db, Order.__table__)[0]
    assert order_after == order_before | {
        "member_id": result.member.id,
        "claim_status": CLAIM_CLAIMED,
    }
    assert order_after["points_earned"] == 25
    assert order_after["status"] == STATUS_PAID


# ---------------------------------------------------------------------------
# 签发与事务
# ---------------------------------------------------------------------------


def test_issued_session_passes_check(db: Session, enabled: None) -> None:
    """第 1 条“创建无密码会员并登录”；Kelvin 10-08“会员会话 30 天”。验收：用 SHOP-TASK-071 的
    issue_member_session 签发，签发的会话能被 check_member_session 校验通过、属于该会员。
    """
    add_attempt(db)
    result = login(db)
    assert result.issued is not None and result.member is not None
    checked = check_member_session(db, result.issued.token, NOW)
    assert checked is not None
    assert checked.member.id == result.member.id
    assert result.issued.expires_at == NOW + timedelta(days=30)


def test_function_does_not_commit(db: Session, enabled: None) -> None:
    """验收：全程只 flush、不提交，核验记录改为 approved、会员、认领与会话由调用方在同一个
    事务里提交；函数返回后回滚，会员、认领与会话都不存在，核验记录仍为 sent。
    """
    attempt_id = add_attempt(db)
    order_id = add_order(db)
    result = login(db)
    assert result.issued is not None
    token = result.issued.token
    db.rollback()
    assert count(db, Member) == 0
    assert count(db, MemberSession) == 0
    assert claim_of(db, order_id) == (None, CLAIM_OPEN)
    assert attempt_status(db, attempt_id) == "sent"
    assert check_member_session(db, token, NOW) is None


def test_store_error_is_sanitized(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败第 4 条“不得把……完整手机号写进日志”。验收：本函数自己的认领出现数据库错误
    （消息带 SQL 参数里的完整号码）时抛不含号码与 SQL 参数、不挂原异常的异常，不签发会话。
    """
    add_attempt(db)

    def _fail(*_args: object, **_kwargs: object) -> int:
        raise OperationalError("UPDATE orders", {"phone": PHONE_MY}, Exception("lost connection"))

    monkeypatch.setattr(member_sms_login, "_claim_guest_orders", _fail)
    with pytest.raises(MemberSmsLoginError) as excinfo:
        login(db)
    assert_sanitized(excinfo.value)
    assert "UPDATE" not in str(excinfo.value)
    db.rollback()
    assert count(db, Member) == 0
    assert count(db, MemberSession) == 0


# ---------------------------------------------------------------------------
# 不泄露
# ---------------------------------------------------------------------------


def test_repr_contains_no_phone_code_or_token(db: Session, enabled: None) -> None:
    """失败第 4 条“不得把验证码、短信凭据或完整手机号写进日志”。验收：返回结果的会员与会话
    repr=False，repr 不含号码、验证码、会话令牌与 CSRF 令牌。
    """
    add_member(db)
    add_attempt(db)
    result = login(db)
    assert result.issued is not None
    shown = repr(result)
    for text_value in (
        PHONE_MY,
        PHONE_MY[1:],
        CODE,
        PASSWORD_HASH,
        result.issued.token,
        result.issued.csrf_token,
    ):
        assert text_value not in shown
    assert "approved" in shown


def test_no_log_records(db: Session, enabled: None, caplog: pytest.LogCaptureFixture) -> None:
    """失败第 4 条“不得把验证码、短信凭据或完整手机号写进日志”；验收：不写日志。"""
    caplog.set_level(logging.DEBUG)
    add_attempt(db)
    add_order(db)
    login(db)
    login(db, provider=FakeSmsProvider(check_result=CheckStatus.WRONG_CODE))
    assert caplog.records == []
