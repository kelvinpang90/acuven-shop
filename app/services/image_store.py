"""图片的 R2 存储读写：配置判定、共用 client、写入、读取与删除。

对象键为键前缀加文件名加 .webp：商品图片的前缀为 KEY_PREFIX（product-images/，默认值），店铺
标志图的为 LOGO_KEY_PREFIX（store-logos/），依据 docs/DESIGN.md 1.20（提交 a870f78）同一节第 15 条
「展示图存为存储桶里键为 store-logos/<文件名>.webp 的对象」。按 docs/HANDOFF.md 0.84 的落实写法，
前缀是写入、读取与删除只限关键字的参数；两者共用同一组 client、超时、尝试次数与日志约束。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条开头一段：展示图存在
Cloudflare R2 的一个私有存储桶，经 S3 兼容接口用 boto3 访问；四项配置缺任一项或为空字符串即视为
存储不可用；区域固定写 auto；进程内共用 client（首次使用时创建）；连接超时 5 秒、读取超时 10 秒，
写入与读取共至多 3 次尝试，删除只尝试 1 次；request_checksum_calculation 设为 when_required；
R2 调用失败只记操作名、文件名与异常类名，不开 botocore 的调试日志。以及 docs/HANDOFF.md 0.79 的
落实写法：botocore 的尝试次数按 client 设定、不能逐次调用设定，所以写入与读取共用一个 client
（至多 3 次尝试），删除共用另一个 client（只尝试 1 次），两者其余配置相同。

本模块的函数都是同步函数，由调用方放进线程；不校验文件名格式与图片内容，不碰数据库与接口
（SHOP-TASK-189 至 191）。日志与异常里不出现异常文本、端点、账号 ID、存储桶名与密钥。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "product-images/"
LOGO_KEY_PREFIX = "store-logos/"
CONTENT_TYPE = "image/webp"

REGION_NAME = "auto"
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
READ_WRITE_MAX_ATTEMPTS = 3
DELETE_MAX_ATTEMPTS = 1


class ImageStoreUnavailable(Exception):
    """存储未配置，或写入、读取失败（含凭据错误与超时）。不带异常文本。"""


class ImageNotFound(Exception):
    """读取时 R2 回答对象不存在（NoSuchKey 或 HTTP 404）。不带异常文本。"""


def image_store_configured(settings: Settings) -> bool:
    """四项配置都不为空串才算已配置；不另检查格式。"""
    return all(
        (
            settings.r2_endpoint_url,
            settings.r2_bucket,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
        )
    )


# ---------------------------------------------------------------- client

# 两个 client 都在首次使用时于同一把锁内创建，之后共用；测试可把它们重置为 None。
_client_lock = threading.Lock()
_read_write_client: Any = None
_delete_client: Any = None


def _new_client(settings: Settings, max_attempts: int) -> Any:
    config = Config(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "total_max_attempts": max_attempts},
        request_checksum_calculation="when_required",
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name=REGION_NAME,
        config=config,
    )


def read_write_client(settings: Settings) -> Any:
    """写入与读取共用的 client（至多 3 次尝试）。"""
    global _read_write_client
    with _client_lock:
        if _read_write_client is None:
            _read_write_client = _new_client(settings, READ_WRITE_MAX_ATTEMPTS)
        return _read_write_client


def delete_client(settings: Settings) -> Any:
    """删除共用的 client（只尝试 1 次）。"""
    global _delete_client
    with _client_lock:
        if _delete_client is None:
            _delete_client = _new_client(settings, DELETE_MAX_ATTEMPTS)
        return _delete_client


# ---------------------------------------------------------------- 读写与删除


def _key(prefix: str, file_name: str) -> str:
    return f"{prefix}{file_name}.webp"


def _log_failure(operation: str, file_name: str, exc: BaseException) -> None:
    # 只记操作名、文件名与异常类名；不带 exc_info，异常文本里可能有端点或存储桶名。
    logger.error(
        "image_store_failed operation=%s file=%s error=%s",
        operation,
        file_name,
        type(exc).__name__,
    )


def _is_not_found(exc: ClientError) -> bool:
    response = exc.response
    if response.get("Error", {}).get("Code") == "NoSuchKey":
        return True
    return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404


# 下面三个函数取 client 也放在 try 里：端点写错时 boto3 创建 client 即抛出，异常文本里带着端点。
def put_image(settings: Settings, file_name: str, data: bytes, *, prefix: str = KEY_PREFIX) -> None:
    """把 data 写成键为 <prefix><file_name>.webp、Content-Type 为 image/webp 的对象。"""
    if not image_store_configured(settings):
        raise ImageStoreUnavailable from None
    try:
        client = read_write_client(settings)
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=_key(prefix, file_name),
            Body=data,
            ContentType=CONTENT_TYPE,
        )
    except Exception as exc:
        _log_failure("put", file_name, exc)
        raise ImageStoreUnavailable from None


def get_image(settings: Settings, file_name: str, *, prefix: str = KEY_PREFIX) -> bytes:
    """返回键为 <prefix><file_name>.webp 的对象的字节。"""
    if not image_store_configured(settings):
        raise ImageStoreUnavailable from None
    try:
        client = read_write_client(settings)
        response = client.get_object(Bucket=settings.r2_bucket, Key=_key(prefix, file_name))
        return response["Body"].read()
    except ClientError as exc:
        if _is_not_found(exc):
            raise ImageNotFound from None
        _log_failure("get", file_name, exc)
        raise ImageStoreUnavailable from None
    except Exception as exc:
        _log_failure("get", file_name, exc)
        raise ImageStoreUnavailable from None


def delete_image(settings: Settings, file_name: str, *, prefix: str = KEY_PREFIX) -> None:
    """删除键为 <prefix><file_name>.webp 的对象；未配置或失败都只记日志，不抛出。"""
    if not image_store_configured(settings):
        logger.info("image_store_unconfigured operation=delete file=%s", file_name)
        return
    try:
        client = delete_client(settings)
        client.delete_object(Bucket=settings.r2_bucket, Key=_key(prefix, file_name))
    except Exception as exc:
        _log_failure("delete", file_name, exc)
