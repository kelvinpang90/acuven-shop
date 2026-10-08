"""短信每日总量与费用预算的原子预占、结算与释放（MySQL 兜底）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：
短信每日总量及费用预算另在本项目 MySQL 库以按日原子计数和预算记录兜底：
发送前按目的地预占保守的单次最高费用，未知费用时停发；结算后更新记录，
防 Redis 重置后超过日上限。Redis 或 MySQL 不可用时短信停发。
以及 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定：全站每天 200 条、
费用上限 20 美元；单次预占按目的地（马来西亚、新加坡）配置的最高单价，
未配置的目的地停发；Twilio Verify 不即时返回实际价格，按预占价结算；
各项都做成配置项（app/core/config.py）。

记录是 SHOP-TASK-012 的 sms_daily_usage 表，每个马来西亚日期一行。
日期按固定的 UTC+8 偏移算出（马来西亚不实行夏令时）：
UTC 16:00:00 起即马来西亚的下一天。金额一律是整数微美元。

- reserve_sms：先用普通查询找当日行，没有时在保存点里插入零值行，
  撞上日期唯一约束即回滚保存点；然后以 SELECT … FOR UPDATE 锁定该行再判断：
  目的地按 E.164 的国家呼叫码（60 马来西亚、65 新加坡）取单次最高费用，
  未配置（0）或其他目的地为 unknown_cost；条数加 1 超过每日条数上限，
  或已预占加已结算加本次费用超过每日费用上限为 over_budget；
  两种拒绝都不改数。否则条数加 1、已预占加本次费用，
  返回预占凭据（日期与金额）。
- settle_sms：把凭据的金额从已预占移到已结算（按预占价），条数不变。
- release_sms：短信未发出时用，把凭据的金额从已预占减去、条数减 1。

结算与释放按凭据里的日期锁定同一行，不按当前时间、不跨日；
该行不存在、凭据金额不是正数，或会使已预占或条数成为负数时
抛 SmsBudgetError（库里另有非负约束）。三个函数只 flush、不提交，
由调用方提交或回滚；MySQL 出错（含锁等待超时与死锁）时
异常原样交给调用方，调用方据此停发。

MySQL 上并发的预占在同一行的行锁上排队，后到者读到前者提交后的数，
所以合计不会超过上限。先找行用不加锁的普通查询：REPEATABLE READ 下
对不存在的行加锁读取会取间隙锁，两个会话同时取间隙锁再插入会死锁。
普通查询读的是快照，漏看别人刚提交的行时插入撞上唯一约束，
回滚保存点后加锁读取（读最新已提交的数据）能找到它。

本模块不写日志。手机号只用来取国家呼叫码，不进返回值与异常消息。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import SmsDailyUsage

# 马来西亚时间（MYT）固定为 UTC+8，不实行夏令时。
MALAYSIA_UTC_OFFSET = timedelta(hours=8)

# 预占结果。
RESERVED = "reserved"
UNKNOWN_COST = "unknown_cost"
OVER_BUDGET = "over_budget"

# 目的地的国家呼叫码。国家呼叫码是前缀码，
# 按 E.164 的开头即可判定，不会把别的国家认成这两国。
COUNTRY_CODE_MY = "60"
COUNTRY_CODE_SG = "65"

# 规范化的 E.164：加号、首位非 0，共 2 到 15 位数字。
_E164_PATTERN = re.compile(r"\+[1-9][0-9]{1,14}")


class SmsBudgetError(RuntimeError):
    """结算或释放会使记录不成立。

    行不存在、凭据金额不是正数、金额或条数会成为负数时抛出。
    消息固定，不含手机号或金额。
    """


@dataclass(frozen=True)
class Reservation:
    """预占凭据：马来西亚日期与预占的金额（微美元）。

    结算与释放都按它，不按当前时间。
    """

    usage_date: date
    cost_micro_usd: int


@dataclass(frozen=True)
class ReserveResult:
    """预占结果。

    status 为 reserved 时带凭据；拒绝（unknown_cost、over_budget）时为空。
    """

    status: str
    reservation: Reservation | None = None


def malaysia_date(now: datetime) -> date:
    """不带时区的 UTC 时间对应的马来西亚日期。"""
    return (now + MALAYSIA_UTC_OFFSET).date()


def max_cost_micro_usd(settings: Settings, phone_e164: str) -> int | None:
    """目的地的单次最高费用。

    未配置（0 或负数）或不是马来西亚、新加坡时为空。号码须是规范化的
    E.164，否则抛 ValueError（编程错误，消息不含号码）。
    """
    if not isinstance(phone_e164, str) or not _E164_PATTERN.fullmatch(phone_e164):
        raise ValueError("phone number must be E.164")
    if phone_e164.startswith("+" + COUNTRY_CODE_MY):
        cost = settings.sms_max_cost_micro_usd_my
    elif phone_e164.startswith("+" + COUNTRY_CODE_SG):
        cost = settings.sms_max_cost_micro_usd_sg
    else:
        return None
    return cost if cost > 0 else None


def reserve_sms(db: Session, settings: Settings, phone_e164: str, now: datetime) -> ReserveResult:
    """发送前预占 now 所在马来西亚日期的一条短信与目的地的单次最高费用。

    步骤见模块说明。号码格式不对时在访问数据库之前抛 ValueError。
    """
    cost = max_cost_micro_usd(settings, phone_e164)
    day = malaysia_date(now)
    if _find(db, day) is None:
        _insert_zero_row(db, day)
    row = _lock(db, day)
    if row is None:
        raise SmsBudgetError("sms daily usage row missing")

    if cost is None:
        return ReserveResult(status=UNKNOWN_COST)
    if row.sent_count + 1 > settings.sms_daily_count_limit:
        return ReserveResult(status=OVER_BUDGET)
    spent = row.reserved_micro_usd + row.settled_micro_usd
    if spent + cost > settings.sms_daily_cost_limit_micro_usd:
        return ReserveResult(status=OVER_BUDGET)

    row.sent_count += 1
    row.reserved_micro_usd += cost
    db.flush()
    return ReserveResult(status=RESERVED, reservation=Reservation(day, cost))


def settle_sms(db: Session, reservation: Reservation) -> None:
    """按预占价结算：凭据金额从已预占移到已结算，条数不变。只 flush。"""
    row = _lock_for(db, reservation)
    if row.reserved_micro_usd < reservation.cost_micro_usd:
        raise SmsBudgetError("sms budget settle would go negative")
    row.reserved_micro_usd -= reservation.cost_micro_usd
    row.settled_micro_usd += reservation.cost_micro_usd
    db.flush()


def release_sms(db: Session, reservation: Reservation) -> None:
    """短信未发出时释放：凭据金额从已预占减去、条数减 1。只 flush。"""
    row = _lock_for(db, reservation)
    if row.reserved_micro_usd < reservation.cost_micro_usd or row.sent_count < 1:
        raise SmsBudgetError("sms budget release would go negative")
    row.reserved_micro_usd -= reservation.cost_micro_usd
    row.sent_count -= 1
    db.flush()


def _find(db: Session, day: date) -> int | None:
    """不加锁地找当日行，返回行 ID。"""
    stmt = select(SmsDailyUsage.id).where(SmsDailyUsage.usage_date == day)
    return db.scalars(stmt).one_or_none()


def _insert_zero_row(db: Session, day: date) -> None:
    """在保存点里插入当日的零值行。

    撞上日期唯一约束（别人已插入）时回滚保存点、不报错。
    """
    try:
        with db.begin_nested():
            db.add(
                SmsDailyUsage(
                    usage_date=day,
                    sent_count=0,
                    reserved_micro_usd=0,
                    settled_micro_usd=0,
                )
            )
    except IntegrityError:
        pass


def _lock(db: Session, day: date) -> SmsDailyUsage | None:
    """以 SELECT … FOR UPDATE 锁定当日行。

    会话里已有的旧值以读到的最新值覆盖（populate_existing）。
    """
    stmt = (
        select(SmsDailyUsage)
        .where(SmsDailyUsage.usage_date == day)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one_or_none()


def _lock_for(db: Session, reservation: Reservation) -> SmsDailyUsage:
    """结算与释放：校验凭据金额，锁定凭据日期的那一行。

    金额不是正整数或该行不存在时抛 SmsBudgetError。
    """
    cost = reservation.cost_micro_usd
    if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
        raise SmsBudgetError("sms budget reservation amount must be positive")
    row = _lock(db, reservation.usage_date)
    if row is None:
        raise SmsBudgetError("sms daily usage row missing")
    return row
