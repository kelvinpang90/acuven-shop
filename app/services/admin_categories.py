"""后台维护分类：读取全部分类、新建、逐个保存三语名称与启用状态。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 14 条（下称「第 14 条」）
与第 11 条「代码」一段，及 docs/HANDOFF.md 0.76 的落实写法。接口由 SHOP-TASK-181 接上，
本模块只提供规则函数，不写日志。

范围：全站至多 30 个分类，不删除，不再用的停用。slug 在新建时按英文名称生成
（app/services/admin_codes.py，上限 100 个字符、全站唯一、结果为空时为 category），之后改名称
也不变。名称每种语言 0–200 个字符，首尾空白去掉，空串存为 None；英文名称在新建与启用时必填；
全部分类的英文名称去掉首尾空白并转小写后在应用层比较不得重复（为空的不参与）。

新建与保存都先不碰数据库检查请求本身（不通过抛 CategoryInvalid），再回滚此前的事务，以同一条
锁定查询锁住现有全部分类行（按 id 排序）后核对：只锁该分类行时，两个会话同时把两个分类改成同一
英文名称都能通过（HANDOFF 0.76）。新建先计数再插入，两个会话同时新建也不会超过 30 个。
保存核对版本号，不同即 CategoryChanged；有改动才加版本、每改一项写一条审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, Product
from app.services.admin_auth import record_audit
from app.services.admin_codes import code_base, unique_code
from app.services.catalog import published

# 审计操作名与对象类别（对象 ID 为分类 ID）。
ADMIN_CATEGORY_CREATED = "admin_category_created"
ADMIN_CATEGORY_CHANGED = "admin_category_changed"
AUDIT_TARGET_CATEGORY = "category"

MAX_CATEGORIES = 30
NAME_MAX_LENGTH = 200
SLUG_MAX_LENGTH = 100
SLUG_FALLBACK = "category"
NEW_CATEGORY_VERSION = 1
# 第 14 条「文案的字段带语言，值取前 100 个字符」。
AUDIT_TEXT_LENGTH = 100


class CategoryInvalid(Exception):
    """取值不合格：名称、启用状态或版本号的格式，缺英文名称，英文名称重复，或分类已满。"""


class CategoryChanged(Exception):
    """分类已被改动：分类不存在、版本号与当前不同，或新建时 slug 撞上唯一约束。"""


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminCategory:
    """一个分类：三语名称为空时为 None；visible_products 为该分类下前台可见的商品件数。"""

    id: int
    slug: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    is_active: bool
    version: int
    visible_products: int


def _load(db: Session, *conditions: ColumnElement[bool]) -> list[AdminCategory]:
    # 与前台同一发布规则（published() 里的分类条件关联外层的分类行）。
    visible = (
        select(func.count(Product.id))
        .where(Product.category_id == Category.id, published())
        .correlate(Category)
        .scalar_subquery()
    )
    # 只查列值：会话里的旧对象不会被当作当前值。
    stmt = (
        select(
            Category.id,
            Category.slug,
            Category.name_en,
            Category.name_zh,
            Category.name_ms,
            Category.is_active,
            Category.version,
            visible,
        )
        .where(*conditions)
        .order_by(Category.id)
    )
    return [
        AdminCategory(
            id=row[0],
            slug=row[1],
            name_en=row[2] or None,
            name_zh=row[3] or None,
            name_ms=row[4] or None,
            is_active=bool(row[5]),
            version=row[6],
            visible_products=row[7],
        )
        for row in db.execute(stmt)
    ]


def load_admin_categories(db: Session) -> list[AdminCategory]:
    """全部分类（含停用的），按 id 从小到大。只发 SELECT，不写库、不写审计。"""
    return _load(db)


# ---------------------------------------------------------------------------
# 第一步：不碰数据库检查请求本身
# ---------------------------------------------------------------------------


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str):
        raise CategoryInvalid("names must be strings")
    text = value.strip()
    if len(text) > NAME_MAX_LENGTH:
        raise CategoryInvalid(f"names must be at most {NAME_MAX_LENGTH} characters")
    return text or None


def _clean_names(
    name_en: object, name_zh: object, name_ms: object
) -> tuple[str | None, str | None, str | None]:
    return _clean_name(name_en), _clean_name(name_zh), _clean_name(name_ms)


def _check_active(is_active: object) -> None:
    if not isinstance(is_active, bool):
        raise CategoryInvalid("is_active must be a boolean")


# ---------------------------------------------------------------------------
# 第二步：锁行核对
# ---------------------------------------------------------------------------


def lock_categories_statement() -> Select[tuple[Category]]:
    """锁定现有全部分类行的查询（SELECT … FOR UPDATE，按 id 排序）；新建与保存共用。"""
    return (
        select(Category)
        .order_by(Category.id)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def _name_key(name: str | None) -> str | None:
    """英文名称的比较口径：去掉首尾空白并转小写；为空时为 None，不参与比较。"""
    if name is None:
        return None
    return name.strip().lower() or None


def _check_unique_name(
    categories: list[Category], name_en: str | None, exclude_id: int | None = None
) -> None:
    key = _name_key(name_en)
    if key is None:
        return
    for category in categories:
        if category.id != exclude_id and _name_key(category.name_en) == key:
            raise CategoryInvalid("another category has the same English name")


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def _audit_value(field: str, value: str | bool | None) -> str:
    """审计的「字段 值」：启用状态为 true 或 false，文案取前 100 个字符，为空时只写字段名。"""
    if isinstance(value, bool):
        return f"{field} {'true' if value else 'false'}"
    if not value:
        return field
    return f"{field} {value[:AUDIT_TEXT_LENGTH]}"


def create_category(
    db: Session,
    admin_account_id: int,
    name_en: str,
    name_zh: str,
    name_ms: str,
    is_active: bool,
    now: datetime,
) -> AdminCategory:
    """新建一个分类，返回它（同 load_admin_categories 的一项）。

    now 为不带时区的 UTC 时间，交给 record_audit；admin_account_id 须在调用前取出（这里先
    回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. 检查请求本身，不发任何 SQL：三个名称为 str，去掉首尾空白后不超过 200 个字符（空串存为
       None），英文名称必填；is_active 为 bool。不通过抛 CategoryInvalid。
    2. 结束此前的事务（此前只有读取，回滚即可），以 lock_categories_statement 锁住现有全部
       分类行，使锁定成为新事务的第一条语句。已有 30 个、或英文名称与任一现有分类相同，抛
       CategoryInvalid。
    3. slug 为 unique_code(code_base(英文名称, category), 100, 现有全部 slug)，插入版本号 1；
       flush 撞上唯一约束抛 CategoryChanged。写一条 admin_category_created 审计（新值为
       slug）后提交。
    任何异常都先回滚再抛出。
    """
    new_en, new_zh, new_ms = _clean_names(name_en, name_zh, name_ms)
    if new_en is None:
        raise CategoryInvalid("English name is required")
    _check_active(is_active)

    db.rollback()
    try:
        categories = list(db.scalars(lock_categories_statement()).all())
        if len(categories) >= MAX_CATEGORIES:
            raise CategoryInvalid(f"there are already {MAX_CATEGORIES} categories")
        _check_unique_name(categories, new_en)

        # 生成的 slug 全为小写；已有的按小写比较，MySQL 不区分大小写的排序规则下也不会撞上。
        taken = {category.slug.lower() for category in categories}
        slug = unique_code(code_base(new_en, SLUG_FALLBACK), SLUG_MAX_LENGTH, taken)
        category = Category(
            slug=slug,
            name_en=new_en,
            name_zh=new_zh,
            name_ms=new_ms,
            is_active=is_active,
            version=NEW_CATEGORY_VERSION,
        )
        db.add(category)
        try:
            db.flush()
        except IntegrityError:
            # 只在两个后台会话同时新建、生成同一 slug 时发生。
            raise CategoryChanged("the categories were changed by another session") from None
        category_id = category.id
        record_audit(
            db,
            ADMIN_CATEGORY_CREATED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_CATEGORY,
            target_id=category_id,
            new_value=slug,
        )
        saved = _load(db, Category.id == category_id)[0]
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


def save_category(
    db: Session,
    admin_account_id: int,
    category_id: int,
    version: int,
    name_en: str,
    name_zh: str,
    name_ms: str,
    is_active: bool,
    now: datetime,
) -> AdminCategory:
    """保存一个分类的三语名称与启用状态，返回保存后的分类（同 load_admin_categories 的一项）。

    now 与 admin_account_id 同 create_category。按以下顺序：
    1. 检查请求本身，不发任何 SQL：三个名称同新建；is_active 为 bool，为真时英文名称必填
       （停用时可为空）；version 为整数（布尔值不算）。不通过抛 CategoryInvalid。
    2. 回滚后执行与新建同一条锁定查询。该分类不在其中或版本号与当前不同，抛 CategoryChanged；
       英文名称与其他任一分类相同，抛 CategoryInvalid。
    3. 按 name_en、name_zh、name_ms、is_active 的顺序逐项与当前值比较，有改动的写新值，每项
       一条 admin_category_changed 审计（旧值与新值为「字段 值」），版本号加一后提交；都未变时
       回滚、不加版本、不写审计。slug 不变。
    任何异常都先回滚再抛出。
    """
    new_en, new_zh, new_ms = _clean_names(name_en, name_zh, name_ms)
    _check_active(is_active)
    if is_active and new_en is None:
        raise CategoryInvalid("an active category needs an English name")
    # bool 是 int 的子类，但 True 不是一个版本号。
    if type(version) is not int:
        raise CategoryInvalid("version must be an integer")

    db.rollback()
    try:
        categories = list(db.scalars(lock_categories_statement()).all())
        category = next((c for c in categories if c.id == category_id), None)
        if category is None or category.version != version:
            raise CategoryChanged("the category was changed since it was read")
        _check_unique_name(categories, new_en, exclude_id=category.id)

        changes: list[tuple[str, str | bool | None, str | bool | None]] = []
        for field, new_value in (
            ("name_en", new_en),
            ("name_zh", new_zh),
            ("name_ms", new_ms),
            ("is_active", is_active),
        ):
            old_value = getattr(category, field)
            if field == "is_active":
                old_value = bool(old_value)
            if old_value != new_value:
                changes.append((field, old_value, new_value))
                setattr(category, field, new_value)

        if not changes:
            saved = _load(db, Category.id == category.id)[0]
            db.rollback()
            return saved

        category.version += 1
        db.flush()
        for field, old_value, new_value in changes:
            record_audit(
                db,
                ADMIN_CATEGORY_CHANGED,
                admin_account_id,
                now,
                target_type=AUDIT_TARGET_CATEGORY,
                target_id=category.id,
                old_value=_audit_value(field, old_value),
                new_value=_audit_value(field, new_value),
            )
        saved = _load(db, Category.id == category.id)[0]
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved
