"""读取与保存店铺装修设置：主题与主色、首页四个区块的顺序与显隐、精选商品。

依据 docs/REQUIREMENTS.md「店铺装修」（首页由主视觉、演示怎么玩、按分类浏览、精选商品四个区块
组成，管理员调整顺序与显隐；精选商品由管理员挑选最多 4 件已上架商品并排定顺序，前台按该顺序只
显示仍上架的商品；修改记入后台审计记录）、docs/UX.md 0.10 的 A08 与 P01，以及 docs/HANDOFF.md
0.34 记录的 Kelvin 2026-10-06 决定（存储与公开读取首版不含标志图；精选商品只显示前台目录可见的
商品，复用目录的 published() 判定）。表与默认值常量见 app/models/store_design.py（SHOP-TASK-044）。

公开读取（read_store_design，SHOP-TASK-045）只发 SELECT，不写库。后台读取与保存
（read_admin_store_design、product_choices、save_store_design）由 SHOP-TASK-051 实现，接口见
app/api/admin_store_design.py：主题须在 THEMES、主色为空或属于所选主题（THEME_ACCENTS）、精选
商品此刻须满足 published()；保存锁定设置单例行后在同一事务里整体替换四个区块与精选各行并写审计。
请求结构（恰好四个区块且为一个排列、精选 0 到 4 件不重复）由接口校验。本模块不写日志。
未挑选或挑选的商品都不可见时显示最新 4 件的规则由前台按设置显示的任务实现，这里不补。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, Product, StoreDesignSetting, StoreFeaturedProduct, StoreHomeBlock
from app.models.admin import AUDIT_VALUE_MAX_LENGTH
from app.models.store_design import (
    BLOCK_CATEGORIES,
    BLOCK_FEATURED,
    BLOCK_HERO,
    BLOCK_HOW,
    DEFAULT_ACCENT,
    DEFAULT_THEME,
    HOME_BLOCKS,
    SINGLETON_SLOT,
    THEME_ACCENTS,
    THEMES,
)
from app.services.admin_auth import record_audit
from app.services.catalog import Language, published

# 审计操作名与对象类别（对象 ID 为设置单例行的 ID）。
ADMIN_STORE_DESIGN_SAVED = "admin_store_design_saved"
AUDIT_TARGET_STORE_DESIGN = "store_design"

# 审计值里的区块代号：主视觉、演示怎么玩、按分类浏览、精选商品。
_BLOCK_CODES = {BLOCK_HERO: "H", BLOCK_HOW: "W", BLOCK_CATEGORIES: "C", BLOCK_FEATURED: "F"}


@dataclass(frozen=True)
class HomeBlock:
    block: str
    visible: bool


@dataclass(frozen=True)
class StoreDesign:
    theme: str
    # 为空即该主题的默认主色（themes.json 里该主题 accentOptions 的第一项）。
    accent: str | None
    # 总是四个区块，按位置排序。
    home_blocks: list[HomeBlock]
    # 按位置排序、只含前台目录可见的精选商品；可能为空。
    featured_slugs: list[str]


@dataclass(frozen=True)
class FeaturedProduct:
    """后台看到的一件精选商品；published 为假即此刻前台不显示它（之后下架的仍留在列表中）。"""

    product_id: int
    slug: str
    # 按请求语言取，缺少时回退英文；英文也缺少（只会是不满足 published() 的商品）时为空。
    name: str | None
    published: bool


@dataclass(frozen=True)
class ProductChoice:
    """可挑选为精选的一件商品（满足 published()）。"""

    product_id: int
    slug: str
    name: str


@dataclass(frozen=True)
class AdminStoreDesign:
    theme: str
    accent: str | None
    # 总是四个区块，按位置排序。
    home_blocks: list[HomeBlock]
    # 按位置排序，含已不满足 published() 的精选商品。
    featured: list[FeaturedProduct]


class StoreDesignInvalid(Exception):
    """保存的业务校验不通过；code 为 theme_invalid、accent_invalid 或 featured_unavailable。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_store_design(db: Session) -> StoreDesign:
    """当前的店铺装修设置。

    设置行不存在时主题与主色取 DEFAULT_THEME、DEFAULT_ACCENT。区块按位置排序；区块表缺少某些
    区块时，缺少的按 HOME_BLOCKS 的默认顺序补在已有区块之后并显示，结果总是四个区块（四行齐全
    由迁移初始化、由写入方保持，这里只为缺行兜底，不写回库里）。精选商品按位置排序，只留满足
    app/services/catalog.py 的 published() 的商品（商品启用、分类启用、英文资料齐全、至少一件
    启用规格）；之后下架的商品仍在表里，只是不出现在结果中，其余商品的顺序不变。

    只查列值而不加载 ORM 对象；查询期间关闭 autoflush，调用方会话里未提交的改动不会因读取被
    flush 进库（读到的是库里已有的数据）。写入与校验见 save_store_design；未挑选或挑选的
    都不可见时显示最新 4 件，由前台按设置显示的任务实现。
    """
    with db.no_autoflush:
        return _read(db)


def _read(db: Session) -> StoreDesign:
    theme, accent = _theme_and_accent(db)
    home_blocks = _filled_blocks(_stored_blocks(db))

    # published() 要求已 join Category；外层不选 ProductVariant，其中的子查询照常关联商品。
    featured_stmt = (
        select(Product.slug)
        .join(StoreFeaturedProduct, StoreFeaturedProduct.product_id == Product.id)
        .join(Category, Category.id == Product.category_id)
        .where(published())
        .order_by(StoreFeaturedProduct.position)
    )
    featured_slugs = list(db.scalars(featured_stmt))

    return StoreDesign(
        theme=theme,
        accent=accent,
        home_blocks=home_blocks,
        featured_slugs=featured_slugs,
    )


def _theme_and_accent(db: Session) -> tuple[str, str | None]:
    setting_stmt = select(StoreDesignSetting.theme, StoreDesignSetting.accent)
    setting = db.execute(
        setting_stmt.where(StoreDesignSetting.singleton_slot == SINGLETON_SLOT)
    ).first()
    if setting is None:
        return DEFAULT_THEME, DEFAULT_ACCENT
    theme, accent = setting
    return theme, accent


def _stored_blocks(db: Session) -> list[HomeBlock]:
    """区块表里的各行，按位置排序，不补缺行。"""
    block_stmt = select(StoreHomeBlock.block, StoreHomeBlock.is_visible)
    rows = db.execute(block_stmt.order_by(StoreHomeBlock.position)).all()
    return [HomeBlock(block=block, visible=visible) for block, visible in rows]


def _filled_blocks(stored: list[HomeBlock]) -> list[HomeBlock]:
    home_blocks = list(stored)
    present = {block.block for block in home_blocks}
    for block in HOME_BLOCKS:
        if block not in present:
            home_blocks.append(HomeBlock(block=block, visible=True))
    return home_blocks


def _name(row: Any, lang: Language) -> str | None:
    """按请求语言取商品名称，缺少时回退英文；与目录相同，空串或只有空格算缺少。"""
    for value in (getattr(row, f"name_{lang}"), row.name_en):
        if value is not None and value.strip(" ") != "":
            return value
    return None


# ---------------------------------------------------------------------------
# 后台读取
# ---------------------------------------------------------------------------


def _featured_ids(db: Session) -> list[int]:
    """精选各行的商品 ID，按位置排序，含已不满足 published() 的商品。"""
    stmt = select(StoreFeaturedProduct.product_id).order_by(StoreFeaturedProduct.position)
    return list(db.scalars(stmt))


def _published_ids(db: Session, product_ids: list[int]) -> set[int]:
    """这些商品里此刻满足 published() 的商品 ID。"""
    if not product_ids:
        return set()
    stmt = (
        select(Product.id)
        .join(Category, Category.id == Product.category_id)
        .where(Product.id.in_(product_ids), published())
    )
    return set(db.scalars(stmt))


def _featured(db: Session, lang: Language) -> list[FeaturedProduct]:
    stmt = (
        select(
            StoreFeaturedProduct.product_id,
            Product.slug,
            Product.name_en,
            Product.name_zh,
            Product.name_ms,
        )
        .join(Product, Product.id == StoreFeaturedProduct.product_id)
        .order_by(StoreFeaturedProduct.position)
    )
    rows = db.execute(stmt).all()
    visible = _published_ids(db, [row.product_id for row in rows])
    return [
        FeaturedProduct(
            product_id=row.product_id,
            slug=row.slug,
            name=_name(row, lang),
            published=row.product_id in visible,
        )
        for row in rows
    ]


def read_admin_store_design(db: Session, lang: Language) -> AdminStoreDesign:
    """后台看到的当前设置。只读。

    主题、主色与区块的读取规则同 read_store_design（设置行不存在时取默认值，区块缺行时补齐）；
    精选商品按位置列出全部各行，已不满足 published() 的也列出、published 为假。
    """
    theme, accent = _theme_and_accent(db)
    return AdminStoreDesign(
        theme=theme,
        accent=accent,
        home_blocks=_filled_blocks(_stored_blocks(db)),
        featured=_featured(db, lang),
    )


def product_choices(db: Session, lang: Language) -> list[ProductChoice]:
    """可挑选为精选的商品：全部满足 published() 的商品，按商品 ID 升序。只读。"""
    stmt = (
        select(Product.id, Product.slug, Product.name_en, Product.name_zh, Product.name_ms)
        .join(Category, Category.id == Product.category_id)
        .where(published())
        .order_by(Product.id)
    )
    return [
        # published() 要求英文名称齐全，名称不会为空。
        ProductChoice(product_id=row.id, slug=row.slug, name=_name(row, lang) or row.name_en)
        for row in db.execute(stmt).all()
    ]


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def lock_setting_statement() -> Select[tuple[StoreDesignSetting]]:
    """锁定装修设置单例行的查询（SELECT … FOR UPDATE）。"""
    return (
        select(StoreDesignSetting)
        .where(StoreDesignSetting.singleton_slot == SINGLETON_SLOT)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def check_theme_and_accent(theme: str, accent: str | None) -> None:
    """主题不在 THEMES 抛 theme_invalid；主色非空且不属于该主题抛 accent_invalid。不查库。"""
    if theme not in THEMES:
        raise StoreDesignInvalid("theme_invalid")
    if accent is not None and accent not in THEME_ACCENTS.get(theme, ()):
        raise StoreDesignInvalid("accent_invalid")


def audit_value(
    theme: str, accent: str | None, home_blocks: list[HomeBlock], featured_ids: list[int]
) -> str:
    """审计的旧值或新值：无空格的 JSON 数组 [主题, 主色或 null, 区块串, [精选商品 ID…]]。

    区块串按位置依次为区块代号加 1（显示）或 0（隐藏），如 H1W1C1F0。超过审计列长即实现
    错误，抛 AssertionError，不截断。不含商品名称。
    """
    blocks = "".join(f"{_BLOCK_CODES[b.block]}{1 if b.visible else 0}" for b in home_blocks)
    value = json.dumps([theme, accent, blocks, featured_ids], separators=(",", ":"))
    if len(value) > AUDIT_VALUE_MAX_LENGTH:
        raise AssertionError("store design audit value exceeds the column length")
    return value


def _lock_setting(db: Session, now: datetime) -> StoreDesignSetting:
    setting = db.scalars(lock_setting_statement()).one_or_none()
    if setting is not None:
        return setting
    # 迁移已写入单例行，正常不会走到这里。并发插入时后到的一方撞上单例槽的唯一约束，回滚保存点
    # 后再锁定对方插入的行。
    try:
        with db.begin_nested():
            db.add(
                StoreDesignSetting(
                    singleton_slot=SINGLETON_SLOT,
                    theme=DEFAULT_THEME,
                    accent=DEFAULT_ACCENT,
                    updated_at=now,
                )
            )
            db.flush()
    except IntegrityError:
        pass
    return db.scalars(lock_setting_statement()).one()


def save_store_design(
    db: Session,
    admin_account_id: int,
    theme: str,
    accent: str | None,
    home_blocks: list[HomeBlock],
    featured_ids: list[int],
    lang: Language,
    now: datetime,
) -> tuple[AdminStoreDesign, bool]:
    """保存装修设置，返回保存后的设置与是否实际改动（与当前完全相同为假）。

    调用方须已校验结构：home_blocks 恰为四个区块的一个排列（列表顺序即位置），featured_ids
    为 0 到 FEATURED_MAX 个不重复的正整数（列表顺序即位置）；admin_account_id 须在调用前取出
    （这里先回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序，任一步不通过即回滚并停止，
    不写入任何东西：
    1. 主题与主色（check_theme_and_accent），不查库。
    2. 结束此前的事务（此前只有读取，回滚即可），以 SELECT … FOR UPDATE 锁定设置单例行，使锁定
       成为新事务的第一条语句：MySQL 默认的 REPEATABLE READ 下读取快照在第一条非锁定读时建立，
       锁定之后才读取的当前设置能看到等锁期间别人提交的保存。单例行不存在时在保存点里按默认值
       插入，撞上唯一约束即回滚该保存点，然后再锁定。
    3. 任一精选商品不存在或此刻不满足 published() 抛 featured_unavailable。
    4. 读取当前设置；与请求完全相同时回滚（释放锁），返回当前设置，不写审计。
    5. 改写主题、主色与更新时间；先删除原有区块与精选各行并 flush，再按请求插入（逐行就地
       改位置会在中途撞上位置、区块与商品的唯一约束）；写一条 admin_store_design_saved 审计
       （旧值与新值见 audit_value），一起提交。
    """
    check_theme_and_accent(theme, accent)

    db.rollback()
    setting = _lock_setting(db, now)
    if len(_published_ids(db, featured_ids)) != len(featured_ids):
        db.rollback()
        raise StoreDesignInvalid("featured_unavailable")

    stored_blocks = _stored_blocks(db)
    current_ids = _featured_ids(db)
    current = (setting.theme, setting.accent, stored_blocks, current_ids)
    if current == (theme, accent, home_blocks, featured_ids):
        design = read_admin_store_design(db, lang)
        db.rollback()
        return design, False

    old_value = audit_value(
        setting.theme, setting.accent, _filled_blocks(stored_blocks), current_ids
    )
    new_value = audit_value(theme, accent, home_blocks, featured_ids)

    setting.theme = theme
    setting.accent = accent
    setting.updated_at = now
    db.execute(delete(StoreHomeBlock))
    db.execute(delete(StoreFeaturedProduct))
    db.flush()
    for position, block in enumerate(home_blocks, start=1):
        db.add(StoreHomeBlock(block=block.block, position=position, is_visible=block.visible))
    for position, product_id in enumerate(featured_ids, start=1):
        db.add(StoreFeaturedProduct(position=position, product_id=product_id))
    db.flush()
    record_audit(
        db,
        ADMIN_STORE_DESIGN_SAVED,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_STORE_DESIGN,
        target_id=setting.id,
        old_value=old_value,
        new_value=new_value,
    )
    design = read_admin_store_design(db, lang)
    db.commit()
    return design, True
