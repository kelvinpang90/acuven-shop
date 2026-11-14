"""AI 装饰提案的每日计数表、调用记录表与迁移 0029 的常量（SHOP-TASK-246）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条「限制」
一段（下称「第 13 条」），以及 SHOP-TASK-246 写定的表名、列名、类型与约束
（下称「落实写法」）。每条测试的文档字符串引用它守住的那一句。

用 SQLite 内存库按模型建表，写法照 tests/test_promotion_models.py。
检查约束的反例每次只让目标约束不成立，断言报出的是该约束的名称。
迁移 0029 只在 MySQL 上执行，这里不在 SQLite 上执行它，只经 importlib
载入、核对 revision 与约束常量；往返由 CI 的 backend 检查在 MySQL 8 上做。
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import date, datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, Table, create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import PromotionAiCall, PromotionAiDailyUsage
from app.models.promotion_ai import (
    AI_RESULT_ERROR,
    AI_RESULT_INVALID,
    AI_RESULT_OK,
    AI_RESULT_TIMEOUT,
    AI_RESULTS,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20261111_0029_promotion_ai.py"

MODELS = (PromotionAiDailyUsage, PromotionAiCall)

DAY = date(2026, 11, 11)
CALLED_AT = datetime(2026, 11, 11, 4, 30)


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("promotion_ai_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _add(db: Session, row: object) -> object:
    db.add(row)
    db.flush()
    return row


def _check_failed(name: str) -> AbstractContextManager[object]:
    # 名称后紧跟换行：断言报出的正是这条约束，不是以它为前缀的别的约束。
    return pytest.raises(IntegrityError, match=rf"CHECK constraint failed: {re.escape(name)}\n")


def _usage(db: Session, **fields: object) -> PromotionAiDailyUsage:
    values = {"usage_date": DAY} | fields
    return _add(db, PromotionAiDailyUsage(**values))


def _call(db: Session, **fields: object) -> PromotionAiCall:
    values = {"result": AI_RESULT_OK, "input_tokens": 1200, "output_tokens": 300} | fields
    return _add(db, PromotionAiCall(**values))


def _model_checks(table: Table) -> dict[str, str]:
    """表上检查约束的完整名称（按 app/db/base.py 的命名约定）→ 文本。"""
    prefix = f"ck_{table.name}_"
    checks = {}
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            name = str(constraint.name)
            checks[name if name.startswith(prefix) else prefix + name] = str(constraint.sqltext)
    return checks


# ---- promotion_ai_daily_usage ----


def test_daily_usage_is_keyed_by_date_alone() -> None:
    """第 13 条「计数表以马来西亚日期为主键」与落实写法「不另设 id 列」：
    主键只有 usage_date 一列（Date），表上只有日期与次数两列。
    """
    table = PromotionAiDailyUsage.__table__

    assert [column.name for column in table.primary_key.columns] == ["usage_date"]
    assert table.c.usage_date.type.python_type is date
    assert set(table.c.keys()) == {"usage_date", "call_count"}
    assert not table.c.call_count.nullable


def test_daily_usage_new_day_starts_at_zero(db: Session) -> None:
    """第 13 条「调用前先以『不存在则插入…』确保当天的行存在，再…未达上限
    才加一」：只填日期插入的当天行次数为 0，次数 0 满足 call_count_non_negative。
    """
    usage = _usage(db)

    assert usage.call_count == 0
    assert db.get(PromotionAiDailyUsage, DAY) is usage


def test_second_row_for_the_same_day_is_rejected(db: Session) -> None:
    """第 13 条「计数表以马来西亚日期为主键（每天至多一行）」：同一日期的
    第二行被主键拒绝；另一天的行不受影响。
    """
    _usage(db, call_count=3)
    _usage(db, usage_date=date(2026, 11, 12))

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: promotion_ai_daily_usage.usage_date"
    ):
        db.execute(insert(PromotionAiDailyUsage).values(usage_date=DAY, call_count=0))


def test_negative_call_count_is_rejected(db: Session) -> None:
    """第 13 条「未达上限才加一…不退回」（次数只增不减，从 0 起）：
    次数 -1 被 call_count_non_negative 拒绝。
    """
    with _check_failed("ck_promotion_ai_daily_usage_call_count_non_negative"):
        _usage(db, call_count=-1)


# ---- promotion_ai_calls ----


def test_call_records_only_time_result_and_usage() -> None:
    """第 13 条「每次调用记时间、结果类别与用量（输入与输出 token 数），
    不记描述原文与模型回答原文」与落实写法「不建外键」：记录表只有这五列，
    没有外键。
    """
    table = PromotionAiCall.__table__

    assert set(table.c.keys()) == {
        "id",
        "called_at",
        "result",
        "input_tokens",
        "output_tokens",
    }
    assert not table.foreign_keys
    assert not PromotionAiDailyUsage.__table__.foreign_keys
    assert table.c.called_at.type.timezone is False
    assert (table.c.called_at.nullable, table.c.result.nullable) == (False, False)
    assert table.c.result.type.length == 16


def test_result_constants() -> None:
    """第 13 条「无论成功、不合格、超时或出错都占用当天的一次」与落实写法
    「result…取 ok、invalid、timeout、error」：四个常量与元组 AI_RESULTS。
    """
    assert (AI_RESULT_OK, AI_RESULT_INVALID, AI_RESULT_TIMEOUT, AI_RESULT_ERROR) == (
        "ok",
        "invalid",
        "timeout",
        "error",
    )
    assert AI_RESULTS == ("ok", "invalid", "timeout", "error")


@pytest.mark.parametrize("result", AI_RESULTS)
def test_each_result_is_accepted(db: Session, result: str) -> None:
    """第 13 条「无论成功、不合格、超时或出错都占用当天的一次」：四种结果
    类别都能写入（满足 result_valid）并读回；不填时间时取写入时刻。
    """
    call = _call(db, result=result)

    assert db.get(PromotionAiCall, call.id).result == result
    assert call.called_at is not None


def test_unknown_result_is_rejected(db: Session) -> None:
    """第 13 条「每次调用记…结果类别」（只有四类）：其他类别被 result_valid 拒绝。"""
    with _check_failed("ck_promotion_ai_calls_result_valid"):
        _call(db, result="refused")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"input_tokens": None, "output_tokens": None}, id="both-missing"),
        pytest.param({"output_tokens": None}, id="output-missing"),
        pytest.param({"input_tokens": 0, "output_tokens": 0}, id="both-zero"),
    ],
)
def test_token_counts_may_be_empty_or_zero(db: Session, fields: dict[str, object]) -> None:
    """第 13 条「每次调用记…用量（输入与输出 token 数）」与落实写法「可空
    Integer（>= 0，没拿到用量时为空）」：两个 token 数可空、可为 0，
    满足两条 _non_negative 约束并按原样读回。
    """
    call = _call(db, result=AI_RESULT_TIMEOUT, called_at=CALLED_AT, **fields)

    assert {name: getattr(call, name) for name in fields} == fields
    assert call.called_at == CALLED_AT


@pytest.mark.parametrize("column", ["input_tokens", "output_tokens"])
def test_negative_token_count_is_rejected(db: Session, column: str) -> None:
    """第 13 条「每次调用记…用量（输入与输出 token 数）」与落实写法「>= 0」：
    负数被 <列>_non_negative 拒绝。
    """
    with _check_failed(f"ck_promotion_ai_calls_{column}_non_negative"):
        _call(db, **{column: -1})


# ---- 迁移 0029 ----


def test_migration_revision_follows_0028() -> None:
    """第 13 条「按日在 MySQL 计数：计数表以马来西亚日期为主键」与
    「每次调用记时间、结果类别与用量」：两张表由排在 0028 之后的迁移建出
    （落实写法「revision 0029、down_revision 0028」）。
    """
    assert (MIGRATION.revision, MIGRATION.down_revision) == ("0029", "0028")


def test_migration_tables_match_the_models() -> None:
    """第 13 条「计数表…」与「每次调用记…」两张表：迁移建的表与两个模型的表相同。"""
    assert set(MIGRATION.TABLES) == {model.__tablename__ for model in MODELS}


def test_migration_check_constraints_match_the_models() -> None:
    """第 13 条「计数表以马来西亚日期为主键（每天至多一行）」的计数与
    「每次调用记时间、结果类别与用量（输入与输出 token 数）」的结果类别与
    用量，在库里由同样的检查约束守住：落实写法「约束表达式作为常量自带并与
    模型逐字相同，约束名按 app/db/base.py 的命名约定逐个写出」（alembic
    check 不比较检查约束），迁移每张表的检查约束与模型的名称与文本逐字相同。
    """
    assert set(MIGRATION.CHECK_CONSTRAINTS) == set(MIGRATION.TABLES)
    for model in MODELS:
        table = model.__tablename__
        assert dict(MIGRATION.CHECK_CONSTRAINTS[table]) == _model_checks(model.__table__), table
