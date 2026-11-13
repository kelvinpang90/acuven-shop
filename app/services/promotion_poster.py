"""节日促销海报图：提供路径、被引用的文件名、替换（上传入库）与移除。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 12 条：海报图沿用
「计价、优惠、积分与库存」第 13 条的格式、文件大小与存储规则（另用自己的存储键
前缀），桌面图宽至少 1,200、手机图宽至少 600，上传即替换该位置的图，存放与替换
的顺序照同一节第 15 条。docs/HANDOFF.md 0.93 确认的一点：海报图的上传与替换立即
生效、不随保存提交、不改活动的版本号，每次上传与替换写后台审计。

SHOP-TASK-240 的落实写法：照店铺标志（app/services/store_logo.py）的顺序；只对
存在且不是 ended 的活动（状态用 admin_promotions.promotion_state）。先回滚此前的
事务，再以 FOR UPDATE 锁活动行、再锁装饰行（与保存的加锁顺序一致，不锁发布锁），
改装饰行该位置的五列与装饰行的更新时间、写审计并提交，不改活动的 version 与
updated_at；替换与移除后删除旧对象（只尝试 1 次，失败只记日志）。已发布
（scheduled 或 running）且海报为上传图模式的活动不能移除图（第 2 条的发布条件
要求两张图都有）。

存储经 image_store 的模块属性调用，一律传 prefix=image_store.POSTER_KEY_PREFIX。
图片由调用方以 process_image(data, min_width=POSTER_MIN_WIDTHS[slot]) 校验与处理
（其余尺寸下限取商品图片的默认值）。本模块的函数都是同步函数，由调用方放进线程；
不写日志（存储模块自己的日志除外），不做接口（SHOP-TASK-250）。
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Promotion, PromotionDecoration
from app.models.promotion import POSTER_MODE_IMAGE, STATUS_PUBLISHED
from app.services import admin_promotions, image_store
from app.services.admin_auth import record_audit
from app.services.admin_promotions import PromotionEnded, PromotionNotFound
from app.services.image_processing import ProcessedImage

# 审计操作名（对象类别为 promotion，对象 ID 为活动 ID）。
ADMIN_PROMOTION_POSTER_UPLOADED = "admin_promotion_poster_uploaded"
ADMIN_PROMOTION_POSTER_REMOVED = "admin_promotion_poster_removed"

# 海报的两个位置与第 12 条的宽度下限（按上传原图转正之后的宽）。
SLOT_DESKTOP = "desktop"
SLOT_PHONE = "phone"
POSTER_SLOTS = (SLOT_DESKTOP, SLOT_PHONE)
POSTER_DESKTOP_MIN_WIDTH = 1200
POSTER_PHONE_MIN_WIDTH = 600
POSTER_MIN_WIDTHS = {SLOT_DESKTOP: POSTER_DESKTOP_MIN_WIDTH, SLOT_PHONE: POSTER_PHONE_MIN_WIDTH}

# 文件名格式同第 13 条，为 32 位小写十六进制。
FILE_NAME_BYTES = 16
# 装饰行每个位置的五列，列名为 poster_<slot>_ 加这些后缀。
_COLUMN_SUFFIXES = ("file_name", "source_format", "width", "height", "byte_size")


class PosterRequired(Exception):
    """已发布（scheduled 或 running）且海报为上传图模式的活动不能移除海报图。"""


def poster_path(file_name: str) -> str:
    """海报图的提供路径 /api/promotion-posters/<文件名>.webp。不查库、不校验文件名。"""
    prefix, suffix = admin_promotions.POSTER_PATH_PREFIX, admin_promotions.POSTER_PATH_SUFFIX
    return f"{prefix}{file_name}{suffix}"


def poster_file_names(db: Session) -> set[str]:
    """所有活动装饰行引用的海报文件名（桌面与手机，不论活动状态）。

    供 SHOP-TASK-250 的公开提供判断「文件名不被任何活动引用即 404」。只发 SELECT；
    查询期间关闭 autoflush，调用方会话里未提交的改动不会因读取被 flush。
    """
    desktop = PromotionDecoration.poster_desktop_file_name
    phone = PromotionDecoration.poster_phone_file_name
    stmt = union(
        select(desktop).where(desktop.is_not(None)),
        select(phone).where(phone.is_not(None)),
    )
    with db.no_autoflush:
        return set(db.scalars(stmt))


def _column_prefix(slot: str) -> str:
    """slot 须为 desktop 或 phone，否则 ValueError；返回该位置五列的列名前缀。"""
    if slot not in POSTER_SLOTS:
        raise ValueError("slot must be desktop or phone")
    return f"poster_{slot}_"


def _lock(db: Session, promotion_id: int, now: datetime) -> tuple[Promotion, PromotionDecoration]:
    """锁活动行、再锁装饰行，按 now 算状态。

    不存在抛 PromotionNotFound，ended 抛 PromotionEnded。
    """
    promotion = db.execute(
        select(Promotion)
        .where(Promotion.id == promotion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if promotion is None:
        raise PromotionNotFound(promotion_id)
    stmt = admin_promotions._decoration_statement(promotion.id, lock=True)
    decoration = db.execute(stmt).scalar_one()
    state = admin_promotions.promotion_state(
        promotion.status,
        promotion.starts_at,
        promotion.ended_early_at or promotion.ends_at,
        now,
    )
    if state == admin_promotions.STATE_ENDED:
        raise PromotionEnded(promotion_id)
    return promotion, decoration


def _audit_value(slot: str, file_name: str | None) -> str:
    """审计的「slot 文件名」；没有图的一侧只写 slot。"""
    return slot if file_name is None else f"{slot} {file_name}"


def replace_poster(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    promotion_id: int,
    slot: str,
    image: ProcessedImage,
    now: datetime,
) -> str:
    """把处理好的展示图写入存储桶并记为该活动 slot 位置的海报图，返回新图的提供路径。

    now 为不带时区的 UTC 时间（算状态、交给 record_audit）；admin_account_id 须在
    调用前取出（这里先回滚）。slot 不是 desktop 或 phone 抛 ValueError，不调用存储。
    按以下顺序：
    1. 文件名取 secrets.token_hex(16)；回滚此前的事务，在任何事务之外调用
       image_store.put_image，失败即抛 ImageStoreUnavailable，记录不变。
    2. 锁活动行、再锁装饰行：不存在抛 PromotionNotFound，ended 抛 PromotionEnded。
       读出该位置原有文件名，改写该位置五列（文件名、处理结果的格式、宽、高，
       字节数为展示图的字节数）与装饰行的更新时间，写一条
       admin_promotion_poster_uploaded 审计（旧值为「slot 原有文件名」，原来没有
       图时只写 slot；新值为「slot 新文件名」），提交。活动的 version 与
       updated_at 不变。
    第 2 步出现任何异常（含提交失败）都先回滚，再以 image_store.delete_image 删除
    刚写入的对象（只尝试 1 次，失败只记日志），然后重新抛出。提交成功后，原来有图
    的再以 delete_image 删除原有对象（失败只记日志，不影响返回）。
    """
    prefix = _column_prefix(slot)
    file_name = secrets.token_hex(FILE_NAME_BYTES)

    db.rollback()
    image_store.put_image(settings, file_name, image.data, prefix=image_store.POSTER_KEY_PREFIX)
    try:
        promotion, decoration = _lock(db, promotion_id, now)
        old_file_name = getattr(decoration, f"{prefix}file_name")
        values = (file_name, image.source_format, image.width, image.height, len(image.data))
        for suffix, value in zip(_COLUMN_SUFFIXES, values, strict=True):
            setattr(decoration, f"{prefix}{suffix}", value)
        decoration.updated_at = now
        db.flush()
        record_audit(
            db,
            ADMIN_PROMOTION_POSTER_UPLOADED,
            admin_account_id,
            now,
            target_type=admin_promotions.AUDIT_TARGET_PROMOTION,
            target_id=promotion.id,
            old_value=_audit_value(slot, old_file_name),
            new_value=_audit_value(slot, file_name),
        )
        # 提交失败同样要删除刚写入的对象，所以提交也在 try 里。
        db.commit()
    except BaseException:
        db.rollback()
        image_store.delete_image(settings, file_name, prefix=image_store.POSTER_KEY_PREFIX)
        raise

    if old_file_name is not None:
        image_store.delete_image(settings, old_file_name, prefix=image_store.POSTER_KEY_PREFIX)
    return poster_path(file_name)


def remove_poster(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    promotion_id: int,
    slot: str,
    now: datetime,
) -> None:
    """移除该活动 slot 位置的海报图。

    now、admin_account_id 与 slot 同 replace_poster。回滚此前的事务后锁活动行、再锁
    装饰行：不存在抛 PromotionNotFound，ended 抛 PromotionEnded；已发布（scheduled
    或 running）且海报为上传图模式抛 PosterRequired（不论该位置当前是否有图）。
    该位置没有图时回滚，不写审计、不调用存储，照样正常返回。有图时五列清空、改装饰行
    的更新时间，写一条 admin_promotion_poster_removed 审计（旧值为「slot 原有文件名」，
    新值为 slot）后提交；提交之后以 image_store.delete_image 删除该对象（存储未配置
    或删除失败都只记日志，记录照常清空）。活动的 version 与 updated_at 不变。
    提交之前的任何异常都先回滚再抛出。
    """
    prefix = _column_prefix(slot)

    db.rollback()
    try:
        promotion, decoration = _lock(db, promotion_id, now)
        # ended 已在 _lock 里排除，已发布的到这里即 scheduled 或 running。
        if promotion.status == STATUS_PUBLISHED and decoration.poster_mode == POSTER_MODE_IMAGE:
            raise PosterRequired(promotion_id)
        # 提交后 ORM 对象过期，先取出文件名。
        file_name = getattr(decoration, f"{prefix}file_name")
        if file_name is None:
            db.rollback()
            return

        for suffix in _COLUMN_SUFFIXES:
            setattr(decoration, f"{prefix}{suffix}", None)
        decoration.updated_at = now
        db.flush()
        record_audit(
            db,
            ADMIN_PROMOTION_POSTER_REMOVED,
            admin_account_id,
            now,
            target_type=admin_promotions.AUDIT_TARGET_PROMOTION,
            target_id=promotion.id,
            old_value=_audit_value(slot, file_name),
            new_value=_audit_value(slot, None),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    image_store.delete_image(settings, file_name, prefix=image_store.POSTER_KEY_PREFIX)
