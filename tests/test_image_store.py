"""商品图片的 R2 存储读写（app/services/image_store.py，SHOP-TASK-187）。

用 botocore.stub.Stubber 挂在本模块创建的 client 上，不访问网络；端点是不可解析的示例值。
每条测试的文档字符串写明它守住的 docs/DESIGN.md 1.19「计价、优惠、积分与库存」第 13 条原句
或 docs/HANDOFF.md 0.79 的落实写法。
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.core.config import Settings
from app.services import image_store
from app.services.image_store import ImageNotFound, ImageStoreUnavailable

ENDPOINT_HOST = "r2-endpoint.example.invalid"
ENDPOINT = f"https://{ENDPOINT_HOST}"
BUCKET = "bucket-name-for-tests"
ACCESS_KEY_ID = "access-key-id-for-tests"
SECRET_ACCESS_KEY = "secret-access-key-for-tests"
STUB_MESSAGE = "stubbed-error-message-must-not-be-logged"

FILE_NAME = "0123456789abcdef0123456789abcdef"
KEY = f"product-images/{FILE_NAME}.webp"
DATA = b"RIFF\x00\x00\x00\x00WEBPVP8 fake-image-bytes"

R2_NAMES = ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

LOGGER = "app.services.image_store"


def _settings(**overrides: str) -> Settings:
    values = {
        "R2_ENDPOINT_URL": ENDPOINT,
        "R2_BUCKET": BUCKET,
        "R2_ACCESS_KEY_ID": ACCESS_KEY_ID,
        "R2_SECRET_ACCESS_KEY": SECRET_ACCESS_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def _reset_clients() -> Iterator[None]:
    image_store._read_write_client = None
    image_store._delete_client = None
    yield
    image_store._read_write_client = None
    image_store._delete_client = None


def _forbid_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("boto3.client must not be called")

    monkeypatch.setattr(boto3, "client", fail)


def _record_client_creation(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple, dict]]:
    calls: list[tuple[tuple, dict]] = []
    original = boto3.client

    def record(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(boto3, "client", record)
    return calls


def _assert_safe_log(text: str, operation: str, error_class: str) -> None:
    assert f"operation={operation}" in text
    assert FILE_NAME in text
    assert error_class in text
    assert STUB_MESSAGE not in text
    assert ENDPOINT_HOST not in text
    assert BUCKET not in text
    assert ACCESS_KEY_ID not in text
    assert SECRET_ACCESS_KEY not in text


def _assert_bare(exc: BaseException) -> None:
    assert str(exc) == ""
    assert exc.args == ()
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


# ---------------------------------------------------------------- 配置


def test_settings_read_original_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「四项配置的变量名为 R2_ENDPOINT_URL、R2_BUCKET、R2_ACCESS_KEY_ID、
    R2_SECRET_ACCESS_KEY」：以原名读取环境变量，带 SHOP_ 前缀的同名变量不生效。"""
    for name in R2_NAMES:
        monkeypatch.setenv(name, f"value-of-{name}")
        monkeypatch.setenv(f"SHOP_{name}", "prefixed-value")
    settings = Settings(_env_file=None)
    assert settings.r2_endpoint_url == "value-of-R2_ENDPOINT_URL"
    assert settings.r2_bucket == "value-of-R2_BUCKET"
    assert settings.r2_access_key_id == "value-of-R2_ACCESS_KEY_ID"
    assert settings.r2_secret_access_key == "value-of-R2_SECRET_ACCESS_KEY"

    for name in R2_NAMES:
        monkeypatch.delenv(name)
    prefixed_only = Settings(_env_file=None)
    assert prefixed_only.r2_endpoint_url == ""
    assert prefixed_only.r2_bucket == ""
    assert prefixed_only.r2_access_key_id == ""
    assert prefixed_only.r2_secret_access_key == ""


def test_settings_accept_original_names_as_keywords() -> None:
    """守住「四项配置的变量名为 R2_ENDPOINT_URL……」：以四个原名作关键字参数构造 Settings。"""
    settings = _settings()
    assert settings.r2_endpoint_url == ENDPOINT
    assert settings.r2_bucket == BUCKET
    assert settings.r2_access_key_id == ACCESS_KEY_ID
    assert settings.r2_secret_access_key == SECRET_ACCESS_KEY


def test_configured_only_when_all_four_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「缺任一项或为空字符串即视为存储不可用」：四项都不为空串才算已配置；
    都没设置时为未配置；不另检查格式。"""
    assert image_store.image_store_configured(_settings()) is True
    assert image_store.image_store_configured(_settings(R2_ENDPOINT_URL="not a url")) is True
    for name in R2_NAMES:
        assert image_store.image_store_configured(_settings(**{name: ""})) is False, name
    for name in R2_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert image_store.image_store_configured(Settings(_env_file=None)) is False


@pytest.mark.parametrize("missing", R2_NAMES)
def test_unconfigured_functions_do_not_create_client(
    missing: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「缺任一项或为空字符串即视为存储不可用」与「存储不可用时：上传与提供都回答『图片存储
    不可用』……不访问 R2；移除……提交后不调用 R2，只记日志」：写入与读取抛
    ImageStoreUnavailable，删除记一条 info 日志（操作名与文件名）后返回；
    三者都不创建 client。"""
    _forbid_client_creation(monkeypatch)
    settings = _settings(**{missing: ""})
    caplog.set_level(logging.INFO, logger=LOGGER)

    with pytest.raises(ImageStoreUnavailable) as put_error:
        image_store.put_image(settings, FILE_NAME, DATA)
    _assert_bare(put_error.value)
    with pytest.raises(ImageStoreUnavailable) as get_error:
        image_store.get_image(settings, FILE_NAME)
    _assert_bare(get_error.value)
    assert image_store.delete_image(settings, FILE_NAME) is None

    assert image_store._read_write_client is None
    assert image_store._delete_client is None
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert "delete" in message
    assert FILE_NAME in message


# ---------------------------------------------------------------- client


def test_clients_region_timeouts_attempts_and_checksum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守住「区域固定写 auto」「每次调用设连接超时 5 秒、读取超时 10 秒，写入与读取共至多 3 次
    尝试（retries 为 standard 模式、total_max_attempts 为 3），删除（含移除与清理）只尝试 1 次；
    request_checksum_calculation 设为 when_required」：两个 client 的端点与密钥取自配置，
    只有尝试次数不同。"""
    calls = _record_client_creation(monkeypatch)
    settings = _settings()
    read_write = image_store.read_write_client(settings)
    delete = image_store.delete_client(settings)
    assert read_write is not delete

    assert len(calls) == 2
    for (args, kwargs), attempts in zip(calls, (3, 1), strict=True):
        assert args == ("s3",)
        assert kwargs["endpoint_url"] == ENDPOINT
        assert kwargs["aws_access_key_id"] == ACCESS_KEY_ID
        assert kwargs["aws_secret_access_key"] == SECRET_ACCESS_KEY
        assert kwargs["region_name"] == "auto"
        config = kwargs["config"]
        assert config.connect_timeout == 5
        assert config.read_timeout == 10
        assert config.retries == {"mode": "standard", "total_max_attempts": attempts}
        assert config.request_checksum_calculation == "when_required"

    for client, attempts in ((read_write, 3), (delete, 1)):
        assert client.meta.service_model.service_name == "s3"
        assert client.meta.region_name == "auto"
        assert client.meta.endpoint_url == ENDPOINT
        assert client.meta.config.connect_timeout == 5
        assert client.meta.config.read_timeout == 10
        assert client.meta.config.retries["mode"] == "standard"
        assert client.meta.config.retries["total_max_attempts"] == attempts
        assert client.meta.config.request_checksum_calculation == "when_required"


def test_clients_are_created_once_and_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「进程内共用一个 boto3 client（首次使用时创建）」与 HANDOFF 0.79「写入与读取共用一个
    client，删除共用另一个 client……之后不再新建」：连续两次调用得到同一个 client，写入、读取与
    删除各两次也只创建两个 client。"""
    calls = _record_client_creation(monkeypatch)
    settings = _settings()
    assert image_store._read_write_client is None
    assert image_store._delete_client is None

    first = image_store.read_write_client(settings)
    assert image_store.read_write_client(settings) is first
    assert image_store._read_write_client is first
    delete = image_store.delete_client(settings)
    assert image_store.delete_client(settings) is delete
    assert image_store._delete_client is delete
    assert len(calls) == 2

    with Stubber(first) as rw_stub, Stubber(delete) as delete_stub:
        for _ in range(2):
            rw_stub.add_response("put_object", {})
            rw_stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(DATA), len(DATA))})
            delete_stub.add_response("delete_object", {})
        for _ in range(2):
            image_store.put_image(settings, FILE_NAME, DATA)
            assert image_store.get_image(settings, FILE_NAME) == DATA
            image_store.delete_image(settings, FILE_NAME)
        rw_stub.assert_no_pending_responses()
        delete_stub.assert_no_pending_responses()
    assert len(calls) == 2


def test_botocore_debug_logging_is_not_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「也不开 botocore 的调试日志」：创建 client 不调用 boto3.set_stream_logger，
    不改 botocore 与 boto3 的日志级别。"""

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("set_stream_logger must not be called")

    monkeypatch.setattr(boto3, "set_stream_logger", fail)
    botocore_level = logging.getLogger("botocore").level
    boto3_level = logging.getLogger("boto3").level
    settings = _settings()
    image_store.read_write_client(settings)
    image_store.delete_client(settings)
    assert logging.getLogger("botocore").level == botocore_level
    assert logging.getLogger("boto3").level == boto3_level


# ---------------------------------------------------------------- 写入、读取与删除


def test_put_writes_bucket_key_and_content_type() -> None:
    """守住「展示图存为存储桶里键为 product-images/<文件名>.webp 的对象（Content-Type
    image/webp）」：写入的 Bucket、Key、Body 与 ContentType。"""
    settings = _settings()
    client = image_store.read_write_client(settings)
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {"Bucket": BUCKET, "Key": KEY, "Body": DATA, "ContentType": "image/webp"},
        )
        assert image_store.put_image(settings, FILE_NAME, DATA) is None
        stubber.assert_no_pending_responses()


def test_get_returns_object_bytes() -> None:
    """守住「按文件名查到 ProductImage 的行才从存储桶读取该对象」：读取键为
    product-images/<文件名>.webp 的对象，返回它的字节。"""
    settings = _settings()
    client = image_store.read_write_client(settings)
    with Stubber(client) as stubber:
        stubber.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(DATA), len(DATA)), "ContentType": "image/webp"},
            {"Bucket": BUCKET, "Key": KEY},
        )
        assert image_store.get_image(settings, FILE_NAME) == DATA
        stubber.assert_no_pending_responses()


def test_delete_removes_object() -> None:
    """守住「提交后再删存储桶里的对象」与「删除（含移除与清理）只尝试 1 次」：删除走删除用的
    client，键为 product-images/<文件名>.webp。"""
    settings = _settings()
    client = image_store.delete_client(settings)
    with Stubber(client) as stubber:
        stubber.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": KEY})
        assert image_store.delete_image(settings, FILE_NAME) is None
        stubber.assert_no_pending_responses()


# ---------------------------------------------------------------- 失败


@pytest.mark.parametrize(
    ("code", "status"),
    [("NoSuchKey", 404), ("NoSuchKey", 400), ("NotFound", 404)],
)
def test_get_missing_object_is_not_found(
    code: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「R2 回答对象不存在（NoSuchKey 或 HTTP 404）即 404」：错误码为 NoSuchKey 或
    HTTP 状态为 404 时抛 ImageNotFound（不带异常文本），不记日志。"""
    settings = _settings()
    client = image_store.read_write_client(settings)
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_object",
            service_error_code=code,
            service_message=STUB_MESSAGE,
            http_status_code=status,
        )
        with pytest.raises(ImageNotFound) as error:
            image_store.get_image(settings, FILE_NAME)
    _assert_bare(error.value)
    assert [r for r in caplog.records if r.name == LOGGER] == []


@pytest.mark.parametrize(
    ("code", "status"),
    [("AccessDenied", 403), ("InvalidAccessKeyId", 403), ("InternalError", 500)],
)
def test_get_other_client_errors_are_unavailable(
    code: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「其余错误（含凭据错误与超时）一律 503」与「R2 调用失败（写入、读取、删除）只记
    操作名、文件名与异常类名，不记异常文本、端点、账号 ID 或存储桶名」：抛
    ImageStoreUnavailable，记一条 error 日志。"""
    settings = _settings()
    client = image_store.read_write_client(settings)
    caplog.set_level(logging.INFO, logger=LOGGER)
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_object",
            service_error_code=code,
            service_message=STUB_MESSAGE,
            http_status_code=status,
        )
        with pytest.raises(ImageStoreUnavailable) as error:
            image_store.get_image(settings, FILE_NAME)
    _assert_bare(error.value)
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is None
    _assert_safe_log(caplog.text, "get", "ClientError")


@pytest.mark.parametrize(
    "exc",
    [ReadTimeoutError(endpoint_url=ENDPOINT), EndpointConnectionError(endpoint_url=ENDPOINT)],
)
def test_get_transport_errors_are_unavailable(
    exc: Exception, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「其余错误（含凭据错误与超时）一律 503」与「不记异常文本、端点」：读取超时或连不上
    （异常文本里带着端点）抛 ImageStoreUnavailable，日志只有操作名、文件名与异常类名。"""
    settings = _settings()
    client = image_store.read_write_client(settings)

    def raise_exc(**kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(client, "get_object", raise_exc)
    caplog.set_level(logging.INFO, logger=LOGGER)
    with pytest.raises(ImageStoreUnavailable) as error:
        image_store.get_image(settings, FILE_NAME)
    _assert_bare(error.value)
    assert ENDPOINT_HOST in str(exc)
    _assert_safe_log(caplog.text, "get", type(exc).__name__)


def test_put_errors_are_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「写入失败或超时即回答『图片存储不可用』」与「R2 调用失败（写入、读取、删除）只记
    操作名、文件名与异常类名」：服务端错误与超时都抛 ImageStoreUnavailable、记 error 日志，
    日志里没有错误消息、端点与存储桶名。"""
    settings = _settings()
    client = image_store.read_write_client(settings)
    caplog.set_level(logging.INFO, logger=LOGGER)
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "put_object",
            service_error_code="AccessDenied",
            service_message=STUB_MESSAGE,
            http_status_code=403,
        )
        with pytest.raises(ImageStoreUnavailable) as error:
            image_store.put_image(settings, FILE_NAME, DATA)
    _assert_bare(error.value)
    _assert_safe_log(caplog.text, "put", "ClientError")
    caplog.clear()

    def raise_timeout(**kwargs: Any) -> Any:
        raise ReadTimeoutError(endpoint_url=ENDPOINT)

    monkeypatch.setattr(client, "put_object", raise_timeout)
    with pytest.raises(ImageStoreUnavailable) as timeout_error:
        image_store.put_image(settings, FILE_NAME, DATA)
    _assert_bare(timeout_error.value)
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    _assert_safe_log(caplog.text, "put", "ReadTimeoutError")


def test_delete_errors_are_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「删对象失败按开头的日志约束只记日志」：删除出错不抛出，记一条 error 日志，只含操作名、
    文件名与异常类名。"""
    settings = _settings()
    client = image_store.delete_client(settings)
    caplog.set_level(logging.INFO, logger=LOGGER)
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "delete_object",
            service_error_code="InternalError",
            service_message=STUB_MESSAGE,
            http_status_code=500,
        )
        assert image_store.delete_image(settings, FILE_NAME) is None
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    _assert_safe_log(caplog.text, "delete", "ClientError")
    caplog.clear()

    def raise_connection(**kwargs: Any) -> Any:
        raise EndpointConnectionError(endpoint_url=ENDPOINT)

    monkeypatch.setattr(client, "delete_object", raise_connection)
    assert image_store.delete_image(settings, FILE_NAME) is None
    _assert_safe_log(caplog.text, "delete", "EndpointConnectionError")
