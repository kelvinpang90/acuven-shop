"""后台商品图片：读取、上传入库、调序与移除。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条（下称「第 13 条」）
「存放」「排列与移除」两段与「上传」一段的「图片已满」，以及 docs/HANDOFF.md 0.79 的落实写法：
三个改动函数都先回滚此前的事务，再以 lock_product_statement 锁商品行；存储经 image_store 的
模块属性调用；审计对象为商品（以商品 ID 记录）；调序时顺序未变不写库、不写审计；移除或调序时
请求的图片不属于该商品即「图片已被改动」；移除后其余图片的排列序号不重排。

处理结果来自 image_processing.process_image（SHOP-TASK-188），存储读写为 image_store
（SHOP-TASK-187）。本模块的函数都是同步函数，由调用方放进线程；不做接口（SHOP-TASK-190）。
三个改动函数都不改商品的版本号；成功时都返回提交后的图片列表（同 load_product_images）。
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Product, ProductImage
from app.services import image_store
from app.services.admin_auth import record_audit
from app.services.admin_products import (
    AUDIT_TARGET_PRODUCT,
    MAX_ROW_ID,
    AdminProductImage,
    lock_product_statement,
)
from app.services.image_processing import ProcessedImage

# 审计操作名（对象类别为 product，对象 ID 为商品 ID）。
ADMIN_PRODUCT_IMAGE_ADDED = "admin_product_image_added"
ADMIN_PRODUCT_IMAGES_REORDERED = "admin_product_images_reordered"
ADMIN_PRODUCT_IMAGE_REMOVED = "admin_product_image_removed"

# 第 13 条「上传」：该商品已有 8 张即回答「图片已满」。
MAX_IMAGES = 8
# 第 13 条「存放」：文件名为 32 位小写十六进制。
FILE_NAME_BYTES = 16
STORAGE_REF_PREFIX = "/api/product-images/"
STORAGE_REF_SUFFIX = ".webp"


class ProductMissing(Exception):
    """商品不存在。"""


class ImagesFull(Exception):
    """该商品已有 8 张图片。"""


class ImagesChanged(Exception):
    """图片已被改动：调序时列出的 ID 不恰好是该商品现有的全部图片，或移除的图片不存在、
    不属于该商品。"""


def _valid_id(value: object) -> bool:
    # 超出 INT 范围的 ID 不会存在；不拿去比较，免得驱动报溢出。bool 不算 ID。
    return type(value) is int and 1 <= value <= MAX_ROW_ID


def load_product_images(db: Session, product_id: int) -> list[AdminProductImage] | None:
    """该商品的图片（按排列序号、再按 id）；商品不存在时返回 None。只发 SELECT，不写库。"""
    if not _valid_id(product_id):
        return None
    if db.scalar(select(Product.id).where(Product.id == product_id)) is None:
        return None
    stmt = (
        select(ProductImage.id, ProductImage.storage_ref)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.sort_order, ProductImage.id)
    )
    return [AdminProductImage(id=row[0], storage_ref=row[1]) for row in db.execute(stmt)]


def _lock_product(db: Session, product_id: int) -> None:
    """以 lock_product_statement 锁商品行；商品不存在抛 ProductMissing。"""
    product_row = None
    if _valid_id(product_id):
        product_row = db.scalars(lock_product_statement(product_id)).one_or_none()
    if product_row is None:
        raise ProductMissing("the product does not exist")


def _image_rows(db: Session, product_id: int) -> list[ProductImage]:
    # 图片行只经本模块改动，商品行已锁，不另加锁。
    stmt = (
        select(ProductImage)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.sort_order, ProductImage.id)
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(stmt))


def _id_sequence(image_ids: Sequence[int]) -> str:
    """调序审计的图片 ID 序列：英文逗号分隔、不带空格。"""
    return ",".join(str(image_id) for image_id in image_ids)


def _loaded(db: Session, product_id: int) -> list[AdminProductImage]:
    images = load_product_images(db, product_id)
    assert images is not None
    return images


def add_image(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    product_id: int,
    image: ProcessedImage,
    now: datetime,
) -> list[AdminProductImage]:
    """把一张处理好的展示图写入存储桶并记入 ProductImage，返回提交后的图片列表。

    now 为不带时区的 UTC 时间，交给 record_audit；admin_account_id 须在调用前取出（这里先
    回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. 文件名取 secrets.token_hex(16)；回滚此前的事务，在任何事务之外调用
       image_store.put_image，失败即抛 ImageStoreUnavailable，不插入行。
    2. 以 lock_product_statement 锁商品行，商品不存在抛 ProductMissing；该商品已有 8 张抛
       ImagesFull。
    3. 插入 ProductImage（storage_ref 为 /api/product-images/<文件名>.webp，排列序号为现有
       最大值加一、没有图片时为 0，字节数为展示图的字节数）与一条 admin_product_image_added
       审计（新值为图片 ID），提交。
    第 2、3 步出现任何异常（含提交失败）都先回滚，再以 image_store.delete_image 删除刚写入
    的对象（只尝试 1 次，失败只记日志），然后重新抛出。
    """
    file_name = secrets.token_hex(FILE_NAME_BYTES)

    db.rollback()
    image_store.put_image(settings, file_name, image.data)
    try:
        _lock_product(db, product_id)
        stmt = select(ProductImage.sort_order).where(ProductImage.product_id == product_id)
        sort_orders = list(db.scalars(stmt))
        if len(sort_orders) >= MAX_IMAGES:
            raise ImagesFull(f"the product already has {MAX_IMAGES} images")

        image_row = ProductImage(
            product_id=product_id,
            storage_ref=f"{STORAGE_REF_PREFIX}{file_name}{STORAGE_REF_SUFFIX}",
            sort_order=max(sort_orders, default=-1) + 1,
            file_name=file_name,
            source_format=image.source_format,
            width=image.width,
            height=image.height,
            byte_size=len(image.data),
        )
        db.add(image_row)
        db.flush()
        record_audit(
            db,
            ADMIN_PRODUCT_IMAGE_ADDED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_PRODUCT,
            target_id=product_id,
            new_value=str(image_row.id),
        )
        saved = _loaded(db, product_id)
        # 提交失败同样要删除刚写入的对象，所以提交也在 try 里。
        db.commit()
    except BaseException:
        db.rollback()
        image_store.delete_image(settings, file_name)
        raise
    return saved


def reorder_images(
    db: Session,
    admin_account_id: int,
    product_id: int,
    image_ids: Sequence[int],
    now: datetime,
) -> list[AdminProductImage]:
    """按 image_ids 的顺序重排该商品的图片，返回提交后的图片列表；不碰存储桶。

    now 与 admin_account_id 同 add_image。回滚此前的事务后锁商品行，商品不存在抛
    ProductMissing；image_ids 不恰好是该商品现有的全部图片 ID（缺、多、重复、不是整数或属于
    别的商品）抛 ImagesChanged。顺序未变时回滚、不写库、不写审计。否则先把排列序号依次改为
    -1、-2…并 flush，再改为 0、1…并 flush（避开（商品、排列序号）唯一约束的中间冲突），写一条
    admin_product_images_reordered 审计（旧值与新值为英文逗号分隔、不带空格的图片 ID 序列）后
    提交。任何异常都先回滚再抛出。
    """
    db.rollback()
    try:
        _lock_product(db, product_id)
        image_rows = _image_rows(db, product_id)
        current_ids = [row.id for row in image_rows]
        wanted_ids = list(image_ids)
        if (
            any(type(image_id) is not int for image_id in wanted_ids)
            or len(wanted_ids) != len(current_ids)
            or set(wanted_ids) != set(current_ids)
        ):
            raise ImagesChanged("the images were changed since they were read")

        if wanted_ids == current_ids:
            unchanged = _loaded(db, product_id)
            db.rollback()
            return unchanged

        rows_by_id = {row.id: row for row in image_rows}
        for index, image_id in enumerate(wanted_ids):
            rows_by_id[image_id].sort_order = -(index + 1)
        db.flush()
        for index, image_id in enumerate(wanted_ids):
            rows_by_id[image_id].sort_order = index
        db.flush()
        record_audit(
            db,
            ADMIN_PRODUCT_IMAGES_REORDERED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_PRODUCT,
            target_id=product_id,
            old_value=_id_sequence(current_ids),
            new_value=_id_sequence(wanted_ids),
        )
        saved = _loaded(db, product_id)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return saved


def remove_image(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    product_id: int,
    image_id: int,
    now: datetime,
) -> list[AdminProductImage]:
    """移除该商品的一张图片，返回提交后的图片列表。

    now 与 admin_account_id 同 add_image。回滚此前的事务后锁商品行，商品不存在抛
    ProductMissing；该 ID 的图片不存在或不属于该商品抛 ImagesChanged。否则删除行、写一条
    admin_product_image_removed 审计（旧值为图片 ID）后提交；其余图片的排列序号不重排。提交
    之后，该行有文件名时以 image_store.delete_image 删除存储桶里的对象（存储未配置或删除失败
    都只记日志）；示例数据的占位图没有文件名，不调用。提交之前的任何异常都先回滚再抛出。
    """
    db.rollback()
    try:
        _lock_product(db, product_id)
        image_row = None
        if _valid_id(image_id):
            stmt = (
                select(ProductImage)
                .where(ProductImage.id == image_id)
                .execution_options(populate_existing=True)
            )
            image_row = db.scalars(stmt).one_or_none()
        if image_row is None or image_row.product_id != product_id:
            raise ImagesChanged("the image was changed since it was read")

        # 提交后 ORM 对象过期，先取出文件名。
        file_name = image_row.file_name
        db.delete(image_row)
        db.flush()
        record_audit(
            db,
            ADMIN_PRODUCT_IMAGE_REMOVED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_PRODUCT,
            target_id=product_id,
            old_value=str(image_id),
        )
        saved = _loaded(db, product_id)
        db.commit()
    except BaseException:
        db.rollback()
        raise

    if file_name is not None:
        image_store.delete_image(settings, file_name)
    return saved
