"""店铺标志图：读取当前标志、替换（上传入库）与移除。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条（下称「第 15 条」）
「存放与替换」「移除」「审计」三段与「读取与显示」一段的读取部分，以及 docs/HANDOFF.md 0.84 的
落实写法：两个改动函数都先回滚此前的事务，再以 store_design._lock_setting 锁定装修设置单例行
（锁定是新事务的第一条语句，单例行不存在时按默认值插入后再锁定，同 A08 的保存）；存储经
image_store 的模块属性调用，一律传 prefix=image_store.LOGO_KEY_PREFIX；审计对象与 A08 的保存相同
（store_design，以单例行 ID 记录），旧值与新值只是文件名，不含图片内容。

标志记在单例行的五列（SHOP-TASK-209）：文件名、上传时的格式与展示图的宽、高与字节数。处理结果来自
image_processing.process_image（调用方传标志的尺寸下限，SHOP-TASK-210）。本模块的函数都是同步
函数，由调用方放进线程；不写日志（存储模块自己的日志除外），不做接口（SHOP-TASK-212、213）。
A08 的保存（store_design.save_store_design）不读写这五列。
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import StoreDesignSetting
from app.models.store_design import SINGLETON_SLOT
from app.services import image_store, store_design
from app.services.admin_auth import record_audit
from app.services.image_processing import ProcessedImage

# 审计操作名（对象类别为 store_design，对象 ID 为设置单例行的 ID）。
ADMIN_STORE_LOGO_UPLOADED = "admin_store_logo_uploaded"
ADMIN_STORE_LOGO_REMOVED = "admin_store_logo_removed"

# 第 15 条「存放与替换」：文件名格式同第 13 条，为 32 位小写十六进制。
FILE_NAME_BYTES = 16
LOGO_PATH_PREFIX = "/api/store-logos/"
LOGO_PATH_SUFFIX = ".webp"


def logo_path(file_name: str) -> str:
    """标志的提供路径 /api/store-logos/<文件名>.webp。不查库、不校验文件名。"""
    return f"{LOGO_PATH_PREFIX}{file_name}{LOGO_PATH_SUFFIX}"


def current_logo_file_name(db: Session) -> str | None:
    """单例行当前的标志文件名；没有单例行或没有标志时为 None。

    只发 SELECT，不写库；查询期间关闭 autoflush，调用方会话里未提交的改动不会因读取被 flush。
    """
    stmt = select(StoreDesignSetting.logo_file_name).where(
        StoreDesignSetting.singleton_slot == SINGLETON_SLOT
    )
    with db.no_autoflush:
        return db.scalar(stmt)


def replace_logo(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    image: ProcessedImage,
    now: datetime,
) -> str:
    """把一张处理好的展示图写入存储桶并记为店铺标志，返回新标志的提供路径。

    now 为不带时区的 UTC 时间，交给 record_audit；admin_account_id 须在调用前取出（这里先
    回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. 文件名取 secrets.token_hex(16)；回滚此前的事务，在任何事务之外调用
       image_store.put_image，失败即抛 ImageStoreUnavailable，记录不变。
    2. 以 _lock_setting 锁定单例行，读出原有文件名；改写五列（文件名、处理结果的格式、宽、高，
       字节数为展示图的字节数）与更新时间，写一条 admin_store_logo_uploaded 审计（旧值为原有
       文件名、原来没有标志时为空，新值为新文件名），提交。
    第 2 步出现任何异常（含提交失败）都先回滚，再以 image_store.delete_image 删除刚写入的对象
    （只尝试 1 次，失败只记日志），然后重新抛出。提交成功后，原来有标志的再以 delete_image
    删除原有对象（失败只记日志，不影响返回）。
    """
    file_name = secrets.token_hex(FILE_NAME_BYTES)

    db.rollback()
    image_store.put_image(settings, file_name, image.data, prefix=image_store.LOGO_KEY_PREFIX)
    try:
        setting = store_design._lock_setting(db, now)
        old_file_name = setting.logo_file_name
        setting.logo_file_name = file_name
        setting.logo_source_format = image.source_format
        setting.logo_width = image.width
        setting.logo_height = image.height
        setting.logo_byte_size = len(image.data)
        setting.updated_at = now
        db.flush()
        record_audit(
            db,
            ADMIN_STORE_LOGO_UPLOADED,
            admin_account_id,
            now,
            target_type=store_design.AUDIT_TARGET_STORE_DESIGN,
            target_id=setting.id,
            old_value=old_file_name,
            new_value=file_name,
        )
        # 提交失败同样要删除刚写入的对象，所以提交也在 try 里。
        db.commit()
    except BaseException:
        db.rollback()
        image_store.delete_image(settings, file_name, prefix=image_store.LOGO_KEY_PREFIX)
        raise

    if old_file_name is not None:
        image_store.delete_image(settings, old_file_name, prefix=image_store.LOGO_KEY_PREFIX)
    return logo_path(file_name)


def remove_logo(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    now: datetime,
) -> None:
    """移除店铺标志。

    now 与 admin_account_id 同 replace_logo。回滚此前的事务后以 _lock_setting 锁定单例行；
    没有标志时回滚，不写审计、不调用存储，照样正常返回。有标志时五列清空、改更新时间，写一条
    admin_store_logo_removed 审计（旧值为原有文件名，新值为空）后提交；提交之后以
    image_store.delete_image 删除该对象（存储未配置或删除失败都只记日志，记录照常清空）。
    提交之前的任何异常都先回滚再抛出。
    """
    db.rollback()
    try:
        setting = store_design._lock_setting(db, now)
        # 提交后 ORM 对象过期，先取出文件名。
        file_name = setting.logo_file_name
        if file_name is None:
            db.rollback()
            return

        setting.logo_file_name = None
        setting.logo_source_format = None
        setting.logo_width = None
        setting.logo_height = None
        setting.logo_byte_size = None
        setting.updated_at = now
        db.flush()
        record_audit(
            db,
            ADMIN_STORE_LOGO_REMOVED,
            admin_account_id,
            now,
            target_type=store_design.AUDIT_TARGET_STORE_DESIGN,
            target_id=setting.id,
            old_value=file_name,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    image_store.delete_image(settings, file_name, prefix=image_store.LOGO_KEY_PREFIX)
