"""会员注销与注销的一次性批准（app/services/member_deletion.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」）：“注销时撤销
  会话、删除手机号与密码，并把其订单上的会员 ID 清空以解除关联”，“游客订单被认领时写入不含
  手机号、不可撤销的「已认领」标记，会员下单的订单创建时即标记为不可认领，注销清空会员 ID 后
  这些标记保留，重新注册不能据此恢复旧订单的访问或积分”，“注销后同号重注册不得再次认领或
  补发积分”。
- 「资料保留」（下称「资料保留」）：“会员注销后收货资料也保留”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（3）（下称「Kelvin 10-08」）：“重设密码与
  注销时撤销该会员的全部会话”。
- docs/HANDOFF.md 0.45 记录的 Kelvin 2026-10-10 决定（1）（下称「Kelvin 10-10」）：“注销验证码
  核验通过后，服务端在共享 Redis 本项目的库编号里记一条绑定当前会员会话的一次性批准，10 分钟
  有效、使用一次即删除，确认注销时取用”。
没有直接原句的，写明是 SHOP-TASK-080 验收标准里的约定（下称「验收」）。

数据库用 SQLite 内存库按模型建表（引擎设置与 tests/test_member_sms_login.py 相同，由引擎发
BEGIN，保存点与提交和 MySQL 上一样），每个连接打开外键检查并断言已打开。Redis 不连真的：
FakeRedis 照 tests/test_member_auth.py 的写法另写，只实现用到的 SET（带 EX）与事务管道
（GET、DEL），带可推进的替身时钟、命令记录与可注入的错误。期望的键在这里另算，不取实现里的
函数。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import Engine, create_engine, event, insert, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Member,
    MemberSession,
    Order,
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
    STATUS_SHIPPED,
)
from app.models.site import SITE_SETTING_ID
from app.services.member_auth import check_member_session, issue_member_session
from app.services.member_deletion import (
    consume_delete_approval,
    delete_member,
    issue_delete_approval,
)
from app.services.member_sms_login import sms_login_or_register
from app.services.rate_limit import RateLimitUnavailable
from app.services.sms_provider import FakeSmsProvider

NOW = datetime(2026, 10, 11, 3, 0, 0)
LATER = NOW + timedelta(hours=1)
PHONE = "+60123456789"
OTHER_PHONE = "+6581234567"
PASSWORD_HASH = "scrypt$existing-password-hash"
FINGERPRINT = "0" * 64
CODE = "482915"
MINUTES_10 = 600

_REQUEST_IDS = itertools.count(1)
_ORDER_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 与 tests/test_member_sms_login.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
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


def add_member(db: Session, phone: str = PHONE, password_hash: str | None = PASSWORD_HASH) -> int:
    result = db.execute(
        insert(Member).values(
            phone=phone,
            password_hash=password_hash,
            status="active",
            created_at=NOW - timedelta(days=100),
            deleted_at=None,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def add_session(db: Session, member_id: int) -> tuple[int, str]:
    """给会员签发一个会话并提交，返回（会话 ID, cookie 令牌）。"""
    member = db.get(Member, member_id)
    assert member is not None
    issued = issue_member_session(db, member, NOW - timedelta(days=1))
    db.commit()
    return issued.session.id, issued.token


def add_order(
    db: Session,
    phone: str = PHONE,
    created_at: datetime = NOW - timedelta(days=3),
    claim_status: str = CLAIM_OPEN,
    member_id: int | None = None,
    status: str = STATUS_AWAITING_PAYMENT,
) -> int:
    """一张订单、其收货资料与下单事件；直接写库，不进会话的对象表。"""
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
    db.execute(
        insert(OrderEvent).values(
            order_id=order_id,
            from_status=None,
            to_status=STATUS_AWAITING_PAYMENT,
            actor_type="member" if member_id is not None else "guest",
            created_at=created_at,
        )
    )
    db.commit()
    return order_id


def add_attempt(db: Session, phone: str = PHONE, created_at: datetime = NOW) -> int:
    """一条待核验（sent）的短信验证记录，请求 ID 逐次不同。"""
    result = db.execute(
        insert(VerificationAttempt).values(
            phone=phone,
            purpose="login",
            status="sent",
            provider_request_id=f"VE{next(_REQUEST_IDS):032x}",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def enable_sms(db: Session) -> None:
    db.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=True))
    db.commit()


def table_rows(db: Session, model) -> list[dict]:
    table = model.__table__
    return [dict(row) for row in db.execute(select(table).order_by(table.c.id)).mappings()]


def member_row(db: Session, member_id: int) -> dict:
    table = Member.__table__
    return dict(db.execute(select(table).where(table.c.id == member_id)).mappings().one())


def claim_of(db: Session, order_id: int) -> tuple[int | None, str]:
    """订单的（会员 ID, 认领状态）。"""
    stmt = select(Order.member_id, Order.claim_status).where(Order.id == order_id)
    member_id, claim_status = db.execute(stmt).one()
    return member_id, claim_status


def snapshot(db: Session) -> dict[str, list[dict]]:
    """所有相关表的全部行，用来断言「没有任何改动」或「回滚后全部复原」。"""
    models = (Member, MemberSession, Order, OrderRecipient, OrderEvent, VerificationAttempt)
    return {model.__tablename__: table_rows(db, model) for model in models}


class FakeRedis:
    """内存替身：值（按 Redis 存成字节）、按替身时钟计的过期时刻、命令记录与可注入的错误。

    fail_on 为 "set" 或 "execute" 时在该处抛 error（默认连接错误）；del_error 为真时事务里的
    DEL 得到错误，GET 照常读出值，execute 像 redis-py 默认那样抛出第一个错误；before_execute
    在事务执行前调用一次，用来插入另一个取用。
    """

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expires_at: dict[str, int] = {}
        self.now = 0
        self.commands: list[tuple] = []
        self.fail_on: str | None = None
        self.error: Exception = redis.exceptions.ConnectionError(
            "Error connecting to redis-host:6379"
        )
        self.del_error = False
        self.before_execute: Callable[[], None] | None = None

    def advance(self, seconds: int) -> None:
        self.now += seconds

    def ttl(self, key: str) -> int | None:
        self._purge(key)
        if key not in self.expires_at:
            return None
        return self.expires_at[key] - self.now

    def _purge(self, key: str) -> None:
        if key in self.expires_at and self.expires_at[key] <= self.now:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)

    def _maybe_fail(self, where: str) -> None:
        if self.fail_on == where:
            raise self.error

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.commands.append(("set", key, value, ex))
        self._maybe_fail("set")
        self.values[key] = str(value).encode()
        if ex is None:
            self.expires_at.pop(key, None)
        else:
            self.expires_at[key] = self.now + ex
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        self.commands.append(("pipeline",))
        return FakePipeline(self)

    def _get(self, key: str) -> bytes | None:
        self._purge(key)
        return self.values.get(key)

    def _delete(self, key: str) -> int | Exception:
        if self.del_error:
            return redis.exceptions.ResponseError("WRONGTYPE Operation against a key")
        self._purge(key)
        if key not in self.values:
            return 0
        del self.values[key]
        self.expires_at.pop(key, None)
        return 1


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行（中间不插入其他命令，同 MULTI / EXEC）。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def get(self, key: str) -> FakePipeline:
        self.queued.append(("get", key))
        return self

    def delete(self, key: str) -> FakePipeline:
        self.queued.append(("delete", key))
        return self

    def execute(self, raise_on_error: bool = True) -> list:
        hook, self.store.before_execute = self.store.before_execute, None
        if hook is not None:
            hook()
        self.store.commands.append(("exec", tuple(self.queued)))
        self.store._maybe_fail("execute")
        batch, self.queued = self.queued, []
        results = [
            self.store._get(c[1]) if c[0] == "get" else self.store._delete(c[1]) for c in batch
        ]
        if raise_on_error:
            for result in results:
                if isinstance(result, Exception):
                    raise result
        return results


def _key(session_id: int) -> str:
    return f"acuven_shop:member_delete_approval:{session_id}"


# ---------------------------------------------------------------------------
# 注销
# ---------------------------------------------------------------------------


def test_deletion_keeps_row_and_clears_phone_and_password(db: Session) -> None:
    """第 3 条：“注销时……删除手机号与密码”。验收：会员行保留，状态为 deleted，手机号与密码
    哈希为空，注销时间为当前时间，创建时间不变；返回 True。"""
    member_id = add_member(db)
    before = member_row(db, member_id)

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    after = member_row(db, member_id)
    assert after == before | {
        "status": "deleted",
        "phone": None,
        "password_hash": None,
        "deleted_at": NOW,
    }


def test_deletion_revokes_all_sessions_of_member_only(db: Session) -> None:
    """第 3 条：“注销时撤销会话”；Kelvin 10-08：“注销时撤销该会员的全部会话”。该会员的全部
    会话撤销于当前时间、不再通过校验，行保留；已撤销的保留原撤销时间；其他会员的会话不受影响。"""
    member_id = add_member(db)
    other_id = add_member(db, phone=OTHER_PHONE)
    sessions = [add_session(db, member_id) for _ in range(3)]
    other_session_id, other_token = add_session(db, other_id)
    earlier = NOW - timedelta(hours=2)
    db.get(MemberSession, sessions[0][0]).revoked_at = earlier
    db.commit()

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    for _, token in sessions:
        assert check_member_session(db, token, LATER) is None
    revoked = {
        row["id"]: row["revoked_at"]
        for row in table_rows(db, MemberSession)
        if row["member_id"] == member_id
    }
    assert revoked == {sessions[0][0]: earlier, sessions[1][0]: NOW, sessions[2][0]: NOW}
    checked = check_member_session(db, other_token, LATER)
    assert checked is not None and checked.session.id == other_session_id
    assert len(table_rows(db, MemberSession)) == 4


def test_deletion_clears_member_id_on_all_orders_and_keeps_claim_marks(db: Session) -> None:
    """第 3 条：“把其订单上的会员 ID 清空以解除关联”，“注销清空会员 ID 后这些标记保留”。
    验收：不论订单状态，该会员的订单（含 claimed 与 not_claimable）member_id 为空、
    claim_status 不变；订单其余各列、其他会员的订单与游客订单都不变。"""
    member_id = add_member(db)
    other_id = add_member(db, phone=OTHER_PHONE)
    cases = [
        (CLAIM_CLAIMED, STATUS_AWAITING_PAYMENT),
        (CLAIM_CLAIMED, STATUS_COMPLETED),
        (CLAIM_NOT_CLAIMABLE, STATUS_PAID),
        (CLAIM_NOT_CLAIMABLE, STATUS_SHIPPED),
        (CLAIM_NOT_CLAIMABLE, STATUS_CANCELLED),
    ]
    own = {
        add_order(db, claim_status=claim_status, member_id=member_id, status=status): claim_status
        for claim_status, status in cases
    }
    others = [
        add_order(db, phone=OTHER_PHONE, claim_status=CLAIM_NOT_CLAIMABLE, member_id=other_id),
        add_order(db, phone=OTHER_PHONE, claim_status=CLAIM_CLAIMED, member_id=other_id),
        add_order(db),
    ]
    orders_before = {row["id"]: row for row in table_rows(db, Order)}

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    orders_after = {row["id"]: row for row in table_rows(db, Order)}
    for order_id, claim_status in own.items():
        assert claim_of(db, order_id) == (None, claim_status)
        assert orders_after[order_id] == orders_before[order_id] | {"member_id": None}
    for order_id in others:
        assert orders_after[order_id] == orders_before[order_id]


def test_deletion_keeps_recipients_events_and_verification_records(db: Session) -> None:
    """资料保留：“会员注销后收货资料也保留”。验收：不删除、不改订单收货资料、订单状态、订单
    事件与短信验证记录。"""
    member_id = add_member(db)
    add_order(db, claim_status=CLAIM_CLAIMED, member_id=member_id, status=STATUS_PAID)
    add_order(db, claim_status=CLAIM_NOT_CLAIMABLE, member_id=member_id)
    add_attempt(db, created_at=NOW - timedelta(minutes=3))
    unchanged = (OrderRecipient, OrderEvent, VerificationAttempt)
    before = {model.__tablename__: table_rows(db, model) for model in unchanged}
    statuses_before = [row["status"] for row in table_rows(db, Order)]

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert {model.__tablename__: table_rows(db, model) for model in unchanged} == before
    assert [row["status"] for row in table_rows(db, Order)] == statuses_before
    assert all(row["phone"] == PHONE for row in before["order_recipients"])


def test_already_deleted_member_returns_false_without_changes(db: Session) -> None:
    """验收：条件更新仅当该会员仍为 active；已注销的会员返回 False 且没有任何改动——注销时间
    不被改写，会员、会话、订单、收货资料、订单事件与短信验证记录各表都不变。"""
    member_id = add_member(db)
    other_id = add_member(db, phone=OTHER_PHONE)
    add_session(db, other_id)
    add_order(db, phone=OTHER_PHONE, claim_status=CLAIM_NOT_CLAIMABLE, member_id=other_id)
    assert delete_member(db, member_id, NOW) is True
    db.commit()
    before = snapshot(db)

    assert delete_member(db, member_id, LATER) is False
    db.commit()

    assert snapshot(db) == before
    assert member_row(db, member_id)["deleted_at"] == NOW


def test_unknown_member_returns_false_without_changes(db: Session) -> None:
    """验收：更新不到（会员不存在）返回 False 且不做其他改动。"""
    member_id = add_member(db)
    add_session(db, member_id)
    add_order(db, claim_status=CLAIM_NOT_CLAIMABLE, member_id=member_id)
    before = snapshot(db)

    assert delete_member(db, member_id + 100, NOW) is False
    db.commit()

    assert snapshot(db) == before


def test_same_phone_reregisters_as_new_member_without_reclaiming(db: Session) -> None:
    """第 3 条：“重新注册不能据此恢复旧订单的访问或积分”，“注销后同号重注册不得再次认领”。
    验收：注销后同号经 SHOP-TASK-075 的 sms_login_or_register 重新注册为新会员，旧会员的
    claimed 与 not_claimable 订单（收货电话即该号码、仍在 30 天内）不被再次认领，仍无会员 ID；
    旧会话仍不通过。"""
    enable_sms(db)
    old_id = add_member(db)
    _, old_token = add_session(db, old_id)
    claimed = add_order(db, claim_status=CLAIM_CLAIMED, member_id=old_id)
    member_order = add_order(db, claim_status=CLAIM_NOT_CLAIMABLE, member_id=old_id)
    assert delete_member(db, old_id, NOW - timedelta(minutes=30)) is True
    db.commit()

    add_attempt(db, created_at=NOW - timedelta(minutes=5))
    result = sms_login_or_register(db, FakeSmsProvider(), PHONE, "login", CODE, NOW)
    db.commit()

    assert result.created_member is True
    assert result.member is not None and result.member.id != old_id
    assert result.claimed_orders == 0
    assert claim_of(db, claimed) == (None, CLAIM_CLAIMED)
    assert claim_of(db, member_order) == (None, CLAIM_NOT_CLAIMABLE)
    assert check_member_session(db, old_token, NOW) is None
    old = member_row(db, old_id)
    assert (old["phone"], old["status"]) == (None, "deleted")


def test_deletion_only_flushes(db: Session) -> None:
    """验收：只 flush、不提交——注销函数返回后回滚，会员、会话与订单全部复原；Redis 里的批准
    不随数据库回滚，已取用的批准仍不能再取用（Kelvin 10-10“使用一次即删除”）。"""
    member_id = add_member(db)
    session_id, token = add_session(db, member_id)
    add_order(db, claim_status=CLAIM_CLAIMED, member_id=member_id)
    add_order(db, claim_status=CLAIM_NOT_CLAIMABLE, member_id=member_id, status=STATUS_PAID)
    fake = FakeRedis()
    issue_delete_approval(fake, session_id, member_id)
    before = snapshot(db)

    assert consume_delete_approval(fake, session_id, member_id) is True
    assert delete_member(db, member_id, NOW) is True
    db.rollback()

    assert snapshot(db) == before
    assert check_member_session(db, token, NOW) is not None
    assert consume_delete_approval(fake, session_id, member_id) is False


def test_now_must_be_naive_utc(db: Session) -> None:
    """验收：入参为不带时区的当前 UTC 时间（沿用 SHOP-TASK-071）。带时区即抛 ValueError，
    不写库。"""
    member_id = add_member(db)
    before = snapshot(db)

    with pytest.raises(ValueError):
        delete_member(db, member_id, NOW.replace(tzinfo=UTC))
    db.commit()

    assert snapshot(db) == before


# ---------------------------------------------------------------------------
# 注销批准
# ---------------------------------------------------------------------------


def test_issue_writes_member_id_and_600s_in_one_set() -> None:
    """Kelvin 10-10：“绑定当前会员会话的一次性批准，10 分钟有效”。验收：键为前缀加会话 ID、
    值为会员 ID，值与 600 秒过期由同一条 SET 写入，没有别的命令。"""
    fake = FakeRedis()

    issue_delete_approval(fake, 7, 42)

    assert fake.values == {_key(7): b"42"}
    assert fake.ttl(_key(7)) == MINUTES_10
    assert fake.commands == [("set", _key(7), "42", MINUTES_10)]


def test_reissue_overwrites_and_restarts_timer() -> None:
    """Kelvin 10-10“10 分钟有效”。验收：同一会话再次签发即覆盖并重新计时——重签后满原定的
    10 分钟仍可取用。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7, 42)
    fake.advance(MINUTES_10 // 2)

    issue_delete_approval(fake, 7, 42)

    assert fake.ttl(_key(7)) == MINUTES_10
    fake.advance(MINUTES_10 // 2)
    assert consume_delete_approval(fake, 7, 42) is True


def test_consume_once_then_false() -> None:
    """Kelvin 10-10：“使用一次即删除”。第一次取用为 True 并删除键，再取用为 False；取用在一个
    事务里 GET 与 DEL。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7, 42)

    assert consume_delete_approval(fake, 7, 42) is True
    assert fake.values == {}
    assert consume_delete_approval(fake, 7, 42) is False
    assert ("exec", (("get", _key(7)), ("delete", _key(7)))) in fake.commands


def test_consume_after_expiry_is_false() -> None:
    """Kelvin 10-10：“10 分钟有效”。差 1 秒到 10 分钟仍可取用，满 10 分钟起为 False。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7, 42)
    issue_delete_approval(fake, 8, 42)

    fake.advance(MINUTES_10 - 1)
    assert consume_delete_approval(fake, 7, 42) is True
    fake.advance(1)
    assert consume_delete_approval(fake, 8, 42) is False


def test_approval_is_bound_to_session_and_member() -> None:
    """Kelvin 10-10：“绑定当前会员会话的一次性批准”。另一会话取用为 False 且不影响本会话的
    批准；会员 ID 不符为 False（该批准随之删除）。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7, 42)
    issue_delete_approval(fake, 9, 42)

    assert consume_delete_approval(fake, 8, 42) is False
    assert consume_delete_approval(fake, 7, 43) is False
    assert _key(7) not in fake.values
    assert consume_delete_approval(fake, 9, 42) is True


def test_interleaved_consumes_only_one_is_true() -> None:
    """Kelvin 10-10“使用一次即删除”。验收：并发取用同一批准时至多一次得到 True——第一个取用
    已排好 GET 与 DEL、尚未执行时插入第二个取用，只有一个为 True。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7, 42)
    inner: list[bool] = []
    fake.before_execute = lambda: inner.append(consume_delete_approval(fake, 7, 42))

    outer = consume_delete_approval(fake, 7, 42)

    assert sorted([outer, inner[0]]) == [False, True]
    assert fake.values == {}


@pytest.mark.parametrize(
    ("session_id", "member_id"),
    [(0, 42), (-1, 42), (True, 42), ("7", 42), (None, 42), (7, 0), (7, True), (7, "42")],
    ids=["s-zero", "s-negative", "s-bool", "s-str", "s-none", "m-zero", "m-bool", "m-str"],
)
def test_invalid_ids_raise_without_touching_redis(session_id: object, member_id: object) -> None:
    """验收：键为前缀加会话 ID，值为会员 ID。ID 不是正整数是编程错误，签发与取用都抛
    ValueError，不访问 Redis。"""
    fake = FakeRedis()

    with pytest.raises(ValueError):
        issue_delete_approval(fake, session_id, member_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        consume_delete_approval(fake, session_id, member_id)  # type: ignore[arg-type]

    assert fake.commands == []


# ---------------------------------------------------------------------------
# Redis 不可用
# ---------------------------------------------------------------------------


def _assert_sanitized(exc: BaseException, session_id: int, member_id: int) -> None:
    message = str(exc)
    for secret in (_key(session_id), str(session_id), str(member_id)):
        assert secret not in message
    assert exc.__cause__ is None


@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError("Error connecting to redis-host:6379"),
        redis.exceptions.TimeoutError("Timeout reading from redis-host:6379"),
        redis.exceptions.ResponseError("READONLY You can't write against a read only replica"),
    ],
    ids=["connection", "timeout", "response"],
)
def test_issue_raises_when_redis_unavailable(error: Exception) -> None:
    """验收：Redis 连不上、超时或返回错误时签发抛 RateLimitUnavailable（调用方必须拒绝，不得
    放行），消息与异常链不含键、会话 ID 与会员 ID。"""
    fake = FakeRedis()
    fake.fail_on = "set"
    fake.error = error

    with pytest.raises(RateLimitUnavailable) as excinfo:
        issue_delete_approval(fake, 7777, 4242)

    _assert_sanitized(excinfo.value, 7777, 4242)
    assert excinfo.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError("Error connecting to redis-host:6379"),
        redis.exceptions.TimeoutError("Timeout reading from redis-host:6379"),
        redis.exceptions.ExecAbortError("EXECABORT Transaction discarded"),
    ],
    ids=["connection", "timeout", "exec-abort"],
)
def test_consume_raises_when_redis_unavailable(error: Exception) -> None:
    """验收：取用时连不上、超时与事务执行报错都抛 RateLimitUnavailable，消息不含键、会话 ID
    与会员 ID。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7777, 4242)
    fake.fail_on = "execute"
    fake.error = error

    with pytest.raises(RateLimitUnavailable) as excinfo:
        consume_delete_approval(fake, 7777, 4242)

    _assert_sanitized(excinfo.value, 7777, 4242)


def test_consume_with_failed_delete_is_not_success() -> None:
    """Kelvin 10-10“使用一次即删除”。验收：事务执行报错时不把读到的值当作取用成功——GET 读出
    会员 ID 而 DEL 出错时抛 RateLimitUnavailable，消息不含会员 ID。"""
    fake = FakeRedis()
    issue_delete_approval(fake, 7777, 4242)
    fake.del_error = True

    with pytest.raises(RateLimitUnavailable) as excinfo:
        consume_delete_approval(fake, 7777, 4242)

    _assert_sanitized(excinfo.value, 7777, 4242)
    assert fake.values[_key(7777)] == b"4242"


def test_redis_not_configured_raises() -> None:
    """验收：Redis 未配置（客户端为 None）时签发与取用都抛 RateLimitUnavailable。"""
    with pytest.raises(RateLimitUnavailable):
        issue_delete_approval(None, 7, 42)
    with pytest.raises(RateLimitUnavailable):
        consume_delete_approval(None, 7, 42)


# ---------------------------------------------------------------------------
# 不写日志
# ---------------------------------------------------------------------------


def test_module_does_not_log(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    """验收：不写日志。签发、取用与注销都不产生 app. 下的日志记录。"""
    member_id = add_member(db)
    session_id, _ = add_session(db, member_id)
    fake = FakeRedis()
    with caplog.at_level(logging.DEBUG):
        issue_delete_approval(fake, session_id, member_id)
        assert consume_delete_approval(fake, session_id, member_id) is True
        assert delete_member(db, member_id, NOW) is True
        assert delete_member(db, member_id, NOW) is False

    assert [r for r in caplog.records if r.name.startswith("app.")] == []
