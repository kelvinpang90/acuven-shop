"""读取店铺装修设置：主题与主色、首页四个区块的顺序与显隐、精选商品。

依据 docs/REQUIREMENTS.md「店铺装修」（首页由主视觉、演示怎么玩、按分类浏览、精选商品四个区块
组成，管理员调整顺序与显隐；精选商品由管理员挑选最多 4 件已上架商品并排定顺序，前台按该顺序只
显示仍上架的商品）、docs/UX.md 0.8 的 A08 与 P01，以及 docs/HANDOFF.md 0.34 记录的 Kelvin
2026-10-06 决定（存储与公开读取首版不含标志图；精选商品只显示前台目录可见的商品，复用目录的
published() 判定）。表与默认值常量见 app/models/store_design.py（SHOP-TASK-044）。

本模块只发 SELECT，不写库、不写日志。写入与校验由之后的管理后台业务接口任务实现：主色须属于
所选主题（THEME_ACCENTS）、精选最多 4 件且挑选时须满足 published()、四个区块在同一事务里保持
齐全。未挑选或挑选的商品都不可见时显示最新 4 件的规则由前台按设置显示的任务实现，这里不补。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Product, StoreDesignSetting, StoreFeaturedProduct, StoreHomeBlock
from app.models.store_design import DEFAULT_ACCENT, DEFAULT_THEME, HOME_BLOCKS, SINGLETON_SLOT
from app.services.catalog import published


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


def read_store_design(db: Session) -> StoreDesign:
    """当前的店铺装修设置。

    设置行不存在时主题与主色取 DEFAULT_THEME、DEFAULT_ACCENT。区块按位置排序；区块表缺少某些
    区块时，缺少的按 HOME_BLOCKS 的默认顺序补在已有区块之后并显示，结果总是四个区块（四行齐全
    由迁移初始化、由写入方保持，这里只为缺行兜底，不写回库里）。精选商品按位置排序，只留满足
    app/services/catalog.py 的 published() 的商品（商品启用、分类启用、英文资料齐全、至少一件
    启用规格）；之后下架的商品仍在表里，只是不出现在结果中，其余商品的顺序不变。

    只查列值而不加载 ORM 对象；查询期间关闭 autoflush，调用方会话里未提交的改动不会因读取被
    flush 进库（读到的是库里已有的数据）。写入与校验（主色须属于所选主题、精选最多 4 件且须满足
    published()、四个区块在同一事务里保持齐全）由之后的管理后台业务接口任务实现；未挑选或挑选的
    都不可见时显示最新 4 件，由前台按设置显示的任务实现。
    """
    with db.no_autoflush:
        return _read(db)


def _read(db: Session) -> StoreDesign:
    setting_stmt = select(StoreDesignSetting.theme, StoreDesignSetting.accent)
    setting = db.execute(
        setting_stmt.where(StoreDesignSetting.singleton_slot == SINGLETON_SLOT)
    ).first()
    if setting is None:
        theme, accent = DEFAULT_THEME, DEFAULT_ACCENT
    else:
        theme, accent = setting

    block_stmt = select(StoreHomeBlock.block, StoreHomeBlock.is_visible)
    rows = db.execute(block_stmt.order_by(StoreHomeBlock.position)).all()
    home_blocks = [HomeBlock(block=block, visible=visible) for block, visible in rows]
    present = {block.block for block in home_blocks}
    for block in HOME_BLOCKS:
        if block not in present:
            home_blocks.append(HomeBlock(block=block, visible=True))

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
