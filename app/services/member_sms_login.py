"""短信验证通过后登录或注册会员，并认领该号码近 30 天的游客订单。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 1、2、3 条：注册、短信登录与
结账验证是同一个短信验证流程，验证通过后号码已注册则登录，未注册则创建无密码会员并登录，
随后自动认领该号码的游客订单；认领只查近 30 天且未认领的游客订单，写入不可撤销的「已认领」
标记；每个游客订单只能认领一次，注销后同号重注册不得再次认领或补发积分。以及「计价、优惠、
积分与库存」第 3 条：认领过去游客订单不追发积分。docs/HANDOFF.md 0.41（会员注册登录与个人
资料）。

组合 SHOP-TASK-069 的 verify_code 与 SHOP-TASK-071 的 issue_member_session。本模块只提供规则
函数，接口与会话 cookie 留给之后的任务。

全程只 flush、不提交：核验记录改为 approved、新建的会员、认领与会话由调用方在同一个事务里
提交；出错时会话须由调用方回滚。时间一律是不带时区的 UTC。
本模块不写日志；手机号与验证码不出现在异常消息与 repr 里。verify_code 的 SmsVerificationError
原样交给调用方；本模块自己的数据库异常（如撞上号码唯一约束时的 IntegrityError，消息里带
INSERT 参数中的完整号码）换成消息固定的 MemberSmsLoginError，且不挂原异常（__cause__ 与
__context__ 都为空）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Member, Order, OrderRecipient
from app.models.member import (
    MEMBER_ACTIVE,
    PURPOSE_CHECKOUT,
    PURPOSE_LOGIN,
    PURPOSE_REGISTER,
)
from app.models.order import CLAIM_CLAIMED, CLAIM_OPEN
from app.services.member_auth import IssuedMemberSession, issue_member_session
from app.services.phone import is_sms_whitelisted
from app.services.sms_provider import SmsProvider
from app.services.sms_verification import VerifyResult, VerifyStatus, verify_code

# 三者是同一个短信验证流程（设计「权限与资料保护」第 3 条）。密码重设与注销确认不在这里。
SMS_LOGIN_PURPOSES = (PURPOSE_CHECKOUT, PURPOSE_REGISTER, PURPOSE_LOGIN)

# 认领只查近 30 天的游客订单：created_at 在 [now - 30 天, now] 内，两端都含。
CLAIM_WINDOW = timedelta(days=30)

_STORE_FAILED = "member sms login storage failed"


class MemberSmsLoginError(RuntimeError):
    """登录或注册、认领与签发时数据库出错。消息固定，不含号码与 SQL 参数，不挂原异常。"""


@dataclass(frozen=True)
class SmsLoginResult:
    """核验结果；通过时另有会员、签发的会话、本次认领的订单数与是否新建会员。

    核验不是 approved 时 member 与 issued 为空、claimed_orders 为 0、created_member 为假。
    会员对象带号码列、签发结果带令牌，两者都不进 repr。
    """

    verification: VerifyResult
    member: Member | None = field(default=None, repr=False)
    issued: IssuedMemberSession | None = field(default=None, repr=False)
    claimed_orders: int = 0
    created_member: bool = False


def sms_login_or_register(
    db: Session,
    provider: SmsProvider,
    phone_e164: str,
    purpose: str,
    code: str,
    now: datetime,
) -> SmsLoginResult:
    """核验短信验证码；通过时登录或注册会员、认领游客订单并签发会话。只 flush、不提交。

    1. 用途不是 checkout、register、login 抛 ValueError；now 带时区抛 ValueError；号码不是
       规范化 E.164 抛 InvalidPhoneNumber。三者都不核验、不写库。
    2. verify_code；结果不是 approved 时原样返回，不查会员、不建会员、不认领、不签发会话
       （开关在流程途中被关闭由 verify_code 的 sms_disabled 保证）。
    3. 按号码找 active 会员，找到即登录（密码哈希不变）；找不到即在保存点里创建 active、
       无密码哈希的会员。撞上号码唯一约束（并发注册同一号码）时回到保存点，以加锁读改读
       已存在的 active 会员登录；仍找不到时抛 MemberSmsLoginError。已注销的会员号码已清空，
       不会被找到，同号重新验证即创建新会员。
    4. 以一条条件更新认领（见 _claim_guest_orders）；登录与新注册都执行。
    5. issue_member_session 签发会话。

    verify_code 的 SmsVerificationError 原样交给调用方；3 到 5 步出现数据库错误时抛
    MemberSmsLoginError。两种情况都须由调用方回滚。
    """
    if purpose not in SMS_LOGIN_PURPOSES:
        raise ValueError("purpose must be checkout, register or login")
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    # 只用它校验规范化 E.164（不合即抛 InvalidPhoneNumber）；白名单外的号码不会有待核验记录。
    is_sms_whitelisted(phone_e164)

    verification = verify_code(db, provider, phone_e164, purpose, code, now)
    if verification.status is not VerifyStatus.APPROVED:
        return SmsLoginResult(verification)

    try:
        result = _sign_in(db, verification, phone_e164, now)
    except SQLAlchemyError:
        result = None
    if result is None:
        # 在 except 块之外抛出，不挂带着 SQL 参数（含完整号码）的原异常。
        raise MemberSmsLoginError(_STORE_FAILED)
    return result


def _sign_in(
    db: Session, verification: VerifyResult, phone_e164: str, now: datetime
) -> SmsLoginResult | None:
    """核验通过之后的登录或注册、认领与签发；加锁读仍找不到会员时为空。"""
    found = _login_or_register(db, phone_e164, now)
    if found is None:
        return None
    member, created = found
    claimed = _claim_guest_orders(db, member, phone_e164, now)
    issued = issue_member_session(db, member, now)
    return SmsLoginResult(
        verification,
        member=member,
        issued=issued,
        claimed_orders=claimed,
        created_member=created,
    )


def _active_member(db: Session, phone_e164: str, *, lock: bool) -> Member | None:
    """按号码找 active 会员。lock 为真时加锁读（SELECT … FOR UPDATE）：MySQL 可重复读下
    普通读沿用事务的旧快照，看不到并发注册刚提交的会员，加锁读读到最新提交的行。
    SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE。
    """
    stmt = select(Member).where(Member.phone == phone_e164, Member.status == MEMBER_ACTIVE)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).one_or_none()


def _login_or_register(db: Session, phone_e164: str, now: datetime) -> tuple[Member, bool] | None:
    """返回（会员, 是否新建）；撞上约束后加锁读仍找不到 active 会员时为空。"""
    member = _active_member(db, phone_e164, lock=False)
    if member is not None:
        return member, False

    new_member = Member(
        phone=phone_e164,
        password_hash=None,
        status=MEMBER_ACTIVE,
        created_at=now,
        deleted_at=None,
    )
    created = False
    try:
        with db.begin_nested():
            db.add(new_member)
            db.flush()
        created = True
    except IntegrityError:
        # 并发注册同一号码：保存点已回滚（之前 flush 的核验记录保留），改读对方已提交的会员。
        pass
    if created:
        return new_member, True
    member = _active_member(db, phone_e164, lock=True)
    if member is None:
        return None
    return member, False


def _claim_guest_orders(db: Session, member: Member, phone_e164: str, now: datetime) -> int:
    """一条条件更新认领该号码近 30 天、未认领的游客订单，返回本次认领的订单数。

    条件：收货电话等于该号码、claim_status 为 open、member_id 为空、created_at 在
    [now - 30 天, now] 内（两端都含，创建时间晚于 now 的不认领），不论订单状态。
    claim_status 为 open 与 member_id 为空保证同一订单只被认领一次：已认领（含注销后
    member_id 已清空的）与会员下单即标为 not_claimable 的订单都不会再被改到。只改会员 ID
    与认领状态：不发积分、不写订单事件、不改订单状态与收货资料、不碰订单访问会话与授权。
    已在会话里的 Order 对象不随之刷新（与 verify_code 的条件更新相同）。
    """
    recipient_orders = select(OrderRecipient.order_id).where(OrderRecipient.phone == phone_e164)
    result = db.execute(
        update(Order)
        .where(
            Order.id.in_(recipient_orders),
            Order.claim_status == CLAIM_OPEN,
            Order.member_id.is_(None),
            Order.created_at >= now - CLAIM_WINDOW,
            Order.created_at <= now,
        )
        .values(member_id=member.id, claim_status=CLAIM_CLAIMED)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return int(result.rowcount or 0)
