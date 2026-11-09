"""运营告警邮件的发送机制（app/services/ops_alert.py，SHOP-TASK-217）。

依据 docs/DESIGN.md 1.21（提交 5be346e）「运营告警邮件」第 1 至 5 条与第 9 条，
以及 docs/HANDOFF.md 0.88 记录的落实写法。每条测试的文档字符串写明它守住的是
第几条的哪一句。

不连 SMTP 服务器：smtplib 的 SMTP 与 SMTP_SSL 以本文件的 FakeSmtp 代替，每次连接
按预先排好的 Plan 成功或在某一步失败。Redis 照现有测试的 FakeRedis 写法，按替身
时钟计过期。时钟可注入，发信线程不启动（start_thread 只记下调用），测试直接调用
Dispatcher.run_due 同步推进，不真实等待；只有
test_real_sender_thread_does_not_block_callers 起真实的守护线程，以事件同步。

配置值与服务器回复都用 sentinel 标记串（不是真实的主机、账号或地址），用来断言
它们不进日志、邮件头（发件人除外）与正文。
"""

from __future__ import annotations

import ast
import logging
import re
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
import redis

from app.core.config import Settings
from app.services import ops_alert

HOST = "smtp.sentinel-host.invalid"
USERNAME = "sentinel-smtp-user"
PASSWORD = "sentinel-smtp-password"
SENDER = "sentinel-sender@sentinel-mail.invalid"
FIRST = "sentinel-first@sentinel-rcpt.invalid"
SECOND = "sentinel-second@sentinel-rcpt.invalid"
THIRD = "sentinel-third@sentinel-rcpt.invalid"
RECIPIENTS = [FIRST, SECOND, THIRD]
REPLY = b"sentinel-server-reply"
SECRETS = [HOST, USERNAME, PASSWORD, SENDER, *RECIPIENTS, REPLY.decode(), "sentinel"]

# 20:30:15 UTC 是马来西亚时间次日 04:30:15。
START = datetime(2026, 11, 6, 20, 30, 15, tzinfo=UTC)

LOGGER = "app.services.ops_alert"

# 测试用的三个类别（第 6 条的类别由 SHOP-TASK-218 至 222 加入）：
# 按类别限频、以对象作去重键（60 分钟）与去重键为日期（当天）。
CATEGORY = "demo_failure"
BY_TARGET = "demo_job"
BY_DATE = "demo_daily"


def _settings(**overrides: str) -> Settings:
    values = {
        "alert_smtp_host": HOST,
        "alert_smtp_username": USERNAME,
        "alert_smtp_password": PASSWORD,
        "alert_from": SENDER,
        "alert_to": ", ".join(RECIPIENTS),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def categories(monkeypatch: pytest.MonkeyPatch) -> None:
    guidance = "按类别码查日志。"
    hour = ops_alert.HOUR_SECONDS
    day = ops_alert.DAY_SECONDS
    table = {
        CATEGORY: ops_alert.AlertCategory("演示告警", guidance, False, hour),
        BY_TARGET: ops_alert.AlertCategory("演示对象", guidance, True, hour),
        BY_DATE: ops_alert.AlertCategory("演示日期", guidance, True, day),
    }
    for code, spec in table.items():
        monkeypatch.setitem(ops_alert.CATEGORIES, code, spec)


class FakeClock:
    """monotonic 计时与当前 UTC 时间一起前进，不真实等待。"""

    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = START

    def monotonic(self) -> float:
        return self.mono

    def utc_now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class FakeRedis:
    """内存替身：值、按替身时钟计的过期时刻、命令记录与可注入的连接错误。"""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.values: dict[str, str] = {}
        self.expires_at: dict[str, float] = {}
        self.commands: list[tuple[Any, ...]] = []
        self.fail = False

    def _purge(self, key: str) -> None:
        if key in self.expires_at and self.expires_at[key] <= self.clock.mono:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        self.commands.append(("set", key, value, nx, ex))
        if self.fail:
            raise redis.exceptions.ConnectionError("Error connecting to redis-host:6379")
        self._purge(key)
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.expires_at[key] = self.clock.mono + ex
        return True


@dataclass
class Plan:
    """一次连接的表现：在某一步抛出给定的异常，或 starttls 回给定的状态码。

    refuse 为被拒收件人到状态码：全部被拒时 send_message 抛 SMTPRecipientsRefused，
    部分被拒时返回被拒的收件人（照 smtplib）。
    """

    connect: BaseException | None = None
    starttls: BaseException | int = 220
    login: BaseException | None = None
    send: BaseException | None = None
    refuse: dict[str, int] = field(default_factory=dict)
    entered: threading.Event | None = None
    block: threading.Event | None = None


class FakeConnection:
    def __init__(
        self,
        server: FakeSmtp,
        kind: str,
        host: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext | None,
        plan: Plan,
    ) -> None:
        self.server = server
        self.kind = kind
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.plan = plan
        self.events: list[str] = []
        self.tls_context: ssl.SSLContext | None = None
        self.login_args: tuple[str, str] | None = None
        self.from_addr: str | None = None
        self.to_addrs: list[str] = []
        self.message: EmailMessage | None = None

    def starttls(self, *, context: ssl.SSLContext | None = None) -> tuple[int, bytes]:
        self.events.append("starttls")
        self.tls_context = context
        if isinstance(self.plan.starttls, BaseException):
            raise self.plan.starttls
        return self.plan.starttls, REPLY

    def login(self, user: str, password: str) -> None:
        self.events.append("login")
        self.login_args = (user, password)
        if self.plan.login is not None:
            raise self.plan.login

    def send_message(
        self, msg: EmailMessage, from_addr: str | None = None, to_addrs: list[str] | None = None
    ) -> dict[str, tuple[int, bytes]]:
        self.events.append("send")
        self.message = msg
        self.from_addr = from_addr
        self.to_addrs = list(to_addrs or [])
        if self.plan.entered is not None:
            self.plan.entered.set()
        if self.plan.block is not None:
            assert self.plan.block.wait(5)
        if self.plan.send is not None:
            raise self.plan.send
        refused = {
            address: (self.plan.refuse[address], REPLY)
            for address in self.to_addrs
            if address in self.plan.refuse
        }
        if refused and len(refused) == len(self.to_addrs):
            raise smtplib.SMTPRecipientsRefused(refused)
        self.server.delivered += [a for a in self.to_addrs if a not in refused]
        return refused

    def quit(self) -> None:
        self.events.append("quit")

    def close(self) -> None:
        self.events.append("close")


class FakeSmtp:
    """smtplib.SMTP 与 SMTP_SSL 的替身：每次连接取下一个 Plan（没有了就全部成功）。"""

    def __init__(self, *plans: Plan) -> None:
        self.plans = list(plans)
        self.connections: list[FakeConnection] = []
        self.delivered: list[str] = []

    def _open(
        self, kind: str, host: str, port: int, timeout: float, context: ssl.SSLContext | None
    ) -> FakeConnection:
        plan = self.plans.pop(0) if self.plans else Plan()
        connection = FakeConnection(self, kind, host, port, timeout, context, plan)
        self.connections.append(connection)
        if plan.connect is not None:
            raise plan.connect
        return connection

    def smtp(self, host: str, port: int, *, timeout: float) -> FakeConnection:
        return self._open("smtp", host, port, timeout, None)

    def smtp_ssl(
        self, host: str, port: int, *, timeout: float, context: ssl.SSLContext
    ) -> FakeConnection:
        return self._open("smtp_ssl", host, port, timeout, context)


@dataclass
class Harness:
    dispatcher: ops_alert.Dispatcher
    clock: FakeClock
    smtp: FakeSmtp
    redis: FakeRedis
    started: list[Any]
    waits: list[float]


def _harness(
    *plans: Plan,
    settings: Settings | None = None,
    redis_available: bool = True,
    wait: Any = None,
) -> Harness:
    """不启动线程的 Dispatcher：start_thread 只记下调用；默认的等待只把时钟拨到时。"""
    clock = FakeClock()
    smtp = FakeSmtp(*plans)
    fake_redis = FakeRedis(clock)
    started: list[Any] = []
    waits: list[float] = []
    configured = _settings() if settings is None else settings

    def default_wait(condition: threading.Condition, timeout: float) -> None:
        waits.append(timeout)
        clock.advance(timeout)

    dispatcher = ops_alert.Dispatcher(
        lambda: configured,
        smtp=smtp.smtp,
        smtp_ssl=smtp.smtp_ssl,
        redis_client=(lambda: fake_redis) if redis_available else (lambda: None),
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
        start_thread=started.append,
        wait=wait or default_wait,
    )
    return Harness(dispatcher, clock, smtp, fake_redis, started, waits)


def _send_with(*plans: Plan) -> Harness:
    """入队一封 CATEGORY，推进到重试也做完为止。"""
    h = _harness(*plans)
    h.dispatcher.enqueue(CATEGORY)
    for _ in range(3):
        h.dispatcher.run_due()
        h.clock.advance(ops_alert.RETRY_DELAY_SECONDS)
    return h


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER]


def _assert_clean(caplog: pytest.LogCaptureFixture) -> None:
    for message in _messages(caplog):
        for secret in SECRETS:
            assert secret not in message, message


def _failure_log(error: BaseException) -> str:
    text = f"ops_alert_send_failed category=demo_failure error={type(error).__name__}"
    code = getattr(error, "smtp_code", None)
    return text if code is None else f"{text} code={code}"


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)


# ---------------------------------------------------------------------------
# 配置（第 1 条）
# ---------------------------------------------------------------------------


def test_unconfigured_alert_only_logs_once(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 1 条「缺一或不合格即未配置：……不入队，进程在第一次遇到告警时
    记一条 warning ops_alert_not_configured」：不入队、不启动发信线程、
    不连接，warning 只记一次，只带变量名。"""
    h = _harness(settings=Settings(_env_file=None))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))
    h.dispatcher.run_due()

    assert h.started == []
    assert h.smtp.connections == []
    assert h.redis.commands == []
    assert h.dispatcher.wait_until_sent() == 0
    assert _messages(caplog) == [
        "ops_alert_not_configured missing=SHOP_ALERT_SMTP_HOST,SHOP_ALERT_SMTP_USERNAME,"
        "SHOP_ALERT_SMTP_PASSWORD,SHOP_ALERT_FROM,SHOP_ALERT_TO"
    ]


@pytest.mark.parametrize(
    ("overrides", "variable"),
    [
        ({"alert_smtp_port": "sentinel-port"}, "SHOP_ALERT_SMTP_PORT"),
        ({"alert_smtp_security": "sentinel-tls"}, "SHOP_ALERT_SMTP_SECURITY"),
        ({"alert_from": "sentinel-no-at"}, "SHOP_ALERT_FROM"),
        ({"alert_from": "sentinel@two@at"}, "SHOP_ALERT_FROM"),
        ({"alert_to": "sentinel-a@x, sentinel-b"}, "SHOP_ALERT_TO"),
        ({"alert_to": "sentinel-a@x,,sentinel-b@x"}, "SHOP_ALERT_TO"),
        ({"alert_to": "@sentinel-domain"}, "SHOP_ALERT_TO"),
        ({"alert_to": "sentinel-local@"}, "SHOP_ALERT_TO"),
        ({"alert_to": ",".join(f"sentinel{i}@x" for i in range(6))}, "SHOP_ALERT_TO"),
        ({"alert_smtp_host": "   "}, "SHOP_ALERT_SMTP_HOST"),
        ({"alert_smtp_password": ""}, "SHOP_ALERT_SMTP_PASSWORD"),
    ],
)
def test_invalid_config_logs_variable_name_only(
    categories: None,
    caplog: pytest.LogCaptureFixture,
    overrides: dict[str, str],
    variable: str,
) -> None:
    """第 1 条「端口与加密方式……给了就须合格」「其余五项都非空且合格
    （每个地址恰含一个 @ 且两侧非空），七项才算已配置」「（可带不合格的
    变量名，不带任何配置值）」：不合格即未配置、不入队，warning 只写变量名。"""
    settings = _settings(**overrides)
    assert ops_alert.parse_config(settings).config is None
    h = _harness(settings=settings)
    h.dispatcher.enqueue(CATEGORY)

    assert h.started == []
    [warning] = _messages(caplog)
    assert warning.startswith("ops_alert_not_configured ")
    assert variable in warning
    _assert_clean(caplog)


def test_config_defaults_and_whitespace() -> None:
    """第 1 条「端口与加密方式可省略（省略即取上面的默认值）」：端口为空即 587、
    加密方式为空即 starttls；收件人逗号分隔，去掉两侧空白、保持配置顺序。"""
    config = ops_alert.parse_config(_settings(alert_to=f"  {FIRST} ,{SECOND}  ")).config
    assert config is not None
    assert (config.port, config.security) == (587, "starttls")
    assert config.recipients == (FIRST, SECOND)
    assert config.sender == SENDER

    settings = _settings(alert_smtp_port=" 465 ", alert_smtp_security="ssl", alert_to=FIRST)
    config = ops_alert.parse_config(settings).config
    assert config is not None
    assert (config.port, config.security, config.recipients) == (465, "ssl", (FIRST,))
    assert PASSWORD not in repr(config)


@pytest.mark.parametrize(
    ("port", "valid"),
    [
        ("1", True),
        ("65535", True),
        ("0", False),
        ("65536", False),
        ("+25", False),
        ("5.0", False),
        ("-1", False),
    ],
)
def test_port_must_be_an_integer_from_1_to_65535(port: str, valid: bool) -> None:
    """第 1 条「端口为 1 到 65535 的整数」。"""
    check = ops_alert.parse_config(_settings(alert_smtp_port=port))
    assert (check.config is not None) is valid
    if not valid:
        assert check.invalid == ("SHOP_ALERT_SMTP_PORT",)


# ---------------------------------------------------------------------------
# 入队（第 2 条）
# ---------------------------------------------------------------------------


def test_enqueue_is_memory_only_and_starts_one_thread(categories: None) -> None:
    """第 2 条「以不阻塞的方式放进队列」「限频判断（第 4 条，读写 Redis）
    与 SMTP 连接都只在发信线程里做」：入队不连接 SMTP、不碰 Redis；
    发信线程在第一次有告警入队时才启动，每个进程（Dispatcher）一个。"""
    h = _harness()
    assert h.started == []
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))

    assert len(h.started) == 1
    assert h.smtp.connections == []
    assert h.redis.commands == []
    assert h.dispatcher.wait_until_sent(timeout=0) == 2


def test_module_does_not_import_the_database() -> None:
    """第 2 条「发信线程不访问数据库，所以入队即使发生在事务之中、
    持有行锁时也不等网络」与第 4 条「不改用 MySQL」：模块不引入
    SQLAlchemy、会话或模型。"""
    source = Path(ops_alert.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("sqlalchemy", "app.db", "app.models")
    assert not [name for name in imported if name.startswith(forbidden)]


def test_unknown_and_test_categories_are_dropped(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """HANDOFF 0.88「类别未登记时记 warning ops_alert_unknown_category
    后丢弃」「alert 不接受 ops_alert_test」：第 6 条 ops_alert_test
    「只由 send-test-alert 触发」。"""
    h = _harness()
    h.dispatcher.enqueue("not_registered")
    h.dispatcher.enqueue(ops_alert.TEST_CATEGORY)

    assert h.started == []
    assert _messages(caplog) == [
        "ops_alert_unknown_category category=not_registered",
        "ops_alert_unknown_category category=ops_alert_test",
    ]


def test_alert_never_raises(
    categories: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """第 2 条「告警发送的成败不改变业务结果、响应、状态码或事务」与
    HANDOFF 0.88「alert 不抛异常」：读取配置出错、类别或对象不合格时
    模块的 alert 都不抛异常。"""

    def broken() -> Settings:
        raise RuntimeError("sentinel settings failure")

    dispatcher = ops_alert.Dispatcher(broken, start_thread=lambda target: None)
    monkeypatch.setattr(ops_alert, "_dispatcher", dispatcher)
    ops_alert.alert(CATEGORY)
    ops_alert.alert(None)  # type: ignore[arg-type]
    ops_alert.alert(CATEGORY, ["job", "x"])  # type: ignore[arg-type]
    ops_alert.alert(CATEGORY, ("job", "contains space"))
    ops_alert.alert(CATEGORY, error_class="not a class")

    assert ops_alert.wait_until_sent(timeout=0) == 0
    assert _messages(caplog) == [
        "ops_alert_dropped category=demo_failure",
        "ops_alert_unknown_category category=None",
        "ops_alert_invalid_target category=demo_failure",
        "ops_alert_invalid_target category=demo_failure",
        "ops_alert_invalid_target category=demo_failure",
    ]


def test_same_rate_limit_key_is_merged_before_queueing(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 2 条「入队前先在本进程内存里合并：同一个限频键（第 4 条）已有
    一封在队列里……时不再入队，记一条 info ops_alert_suppressed
    category=<类别>」：以对象作去重键的类别按对象分开合并。"""
    h = _harness()
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))
    h.dispatcher.enqueue(BY_TARGET, ("job", "cancel_expired_orders"))

    assert _messages(caplog) == [
        "ops_alert_suppressed category=demo_failure",
        "ops_alert_suppressed category=demo_job",
    ]
    assert h.dispatcher.wait_until_sent(timeout=0) == 3


def test_merged_while_sending_or_waiting_for_retry(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 2 条「已有一封在队列里或正在发送（含等待重试）时不再入队」：
    等待重试的那封仍占着限频键，发完之后才可以再入队。"""
    h = _harness(Plan(connect=ConnectionRefusedError(111, "refused")))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 1

    caplog.clear()
    h.dispatcher.enqueue(CATEGORY)
    assert _messages(caplog) == ["ops_alert_suppressed category=demo_failure"]
    assert h.dispatcher.wait_until_sent(timeout=0) == 1

    h.clock.advance(ops_alert.RETRY_DELAY_SECONDS)
    h.dispatcher.run_due()
    assert h.dispatcher.wait_until_sent(timeout=0) == 0
    h.dispatcher.enqueue(CATEGORY)
    assert h.dispatcher.wait_until_sent(timeout=0) == 1


def test_full_queue_drops_and_retries_do_not_count(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 2 条「合并后队列仍满（容量 100……）才丢弃这封并记一条 warning
    ops_alert_dropped category=<类别>」与 HANDOFF 0.88「等待重试的告警
    不占队列容量」。"""
    h = _harness(Plan(connect=TimeoutError("timed out")))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()  # 这封等待重试，不在队列里。

    for internal_id in range(ops_alert.QUEUE_CAPACITY):
        h.dispatcher.enqueue(BY_TARGET, ("job", f"job_{internal_id}"))
    caplog.clear()
    h.dispatcher.enqueue(BY_TARGET, ("job", "one_too_many"))

    assert _messages(caplog) == ["ops_alert_dropped category=demo_job"]
    assert h.dispatcher.wait_until_sent(timeout=0) == ops_alert.QUEUE_CAPACITY + 1


def test_occurred_at_is_the_enqueue_time(categories: None) -> None:
    """第 2 条「把告警（类别、对象、发生时刻）……放进队列」与第 5 条
    「发生时间（入队时刻，UTC 与马来西亚时间各一行，精确到秒）」：
    入队之后才发出，也写入队时刻。"""
    h = _harness()
    h.dispatcher.enqueue(CATEGORY)
    h.clock.advance(125)
    h.dispatcher.run_due()

    body = h.smtp.connections[0].message.get_content()
    assert "发生时间（UTC）：2026-11-06 20:30:15\n" in body
    assert "发生时间（马来西亚时间 UTC+8）：2026-11-07 04:30:15\n" in body


# ---------------------------------------------------------------------------
# 发信与邮件内容（第 1、3、5 条）
# ---------------------------------------------------------------------------


def test_starttls_completes_before_login_and_send(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 1 条「starttls 连上后必须完成 STARTTLS 才登录与发信」「TLS 用
    ssl.create_default_context()，校验证书与主机名」「一律以账号与密码
    登录」与第 3 条「连接、TLS 握手与每条 SMTP 命令的超时都是 10 秒」；
    第 5 条「收件人只放在 SMTP 信封里（全部配置的收件人）」。"""
    h = _harness()
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()

    [connection] = h.smtp.connections
    assert (connection.kind, connection.host, connection.port) == ("smtp", HOST, 587)
    assert connection.timeout == 10
    assert connection.events == ["starttls", "login", "send", "quit", "close"]
    context = connection.tls_context
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert connection.login_args == (USERNAME, PASSWORD)
    assert (connection.from_addr, connection.to_addrs) == (SENDER, RECIPIENTS)
    assert _messages(caplog) == ["ops_alert_sent category=demo_failure"]


def test_ssl_connects_with_tls_from_the_start(categories: None) -> None:
    """第 1 条「ssl 从一开始就以 TLS 连接（用于 465 端口）」：用 SMTP_SSL
    与默认的校验上下文，不再发 STARTTLS。"""
    settings = _settings(alert_smtp_security="ssl", alert_smtp_port="465")
    h = _harness(settings=settings)
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()

    [connection] = h.smtp.connections
    assert (connection.kind, connection.port, connection.timeout) == ("smtp_ssl", 465, 10)
    assert isinstance(connection.context, ssl.SSLContext)
    assert connection.context.verify_mode == ssl.CERT_REQUIRED
    assert connection.events == ["login", "send", "quit", "close"]


@pytest.mark.parametrize(
    ("starttls", "expected"),
    [
        (
            smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."),
            "ops_alert_send_failed category=demo_failure error=SMTPNotSupportedError",
        ),
        (
            554,
            "ops_alert_send_failed category=demo_failure error=SMTPResponseException code=554",
        ),
    ],
)
def test_starttls_failure_never_sends_in_plaintext(
    categories: None, caplog: pytest.LogCaptureFixture, starttls: Any, expected: str
) -> None:
    """第 1 条「服务器不支持 STARTTLS 即算失败，不以明文发送」与第 3 条
    「不支持 STARTTLS……不重试」：STARTTLS 没有完成（不支持，或回了 220
    以外的状态码）时不登录、不发信。"""
    h = _harness(Plan(starttls=starttls))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    h.clock.advance(ops_alert.RETRY_DELAY_SECONDS)
    h.dispatcher.run_due()

    [connection] = h.smtp.connections
    assert connection.events == ["starttls", "close"]
    assert h.smtp.delivered == []
    assert _messages(caplog) == [expected]


def test_headers_do_not_contain_recipient_addresses(categories: None) -> None:
    """第 5 条「邮件头 From 为配置的发件人；收件人只放在 SMTP 信封里……
    邮件头固定为 To: undisclosed-recipients:;、不设 Cc 与 Bcc」
    「Message-ID 以发件人地址的域名生成」「纯文本、UTF-8」
    「主题为 [Acuven Shop] <类别码>」。"""
    h = _harness()
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()

    message = h.smtp.connections[0].message
    assert message["Subject"] == "[Acuven Shop] demo_failure"
    assert message["From"] == SENDER
    assert message["To"] == "undisclosed-recipients:;"
    assert message["Cc"] is None and message["Bcc"] is None
    assert re.fullmatch(r"<[^@<>]+@sentinel-mail\.invalid>", str(message["Message-ID"]))
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    headers = "\n".join(f"{name}: {value}" for name, value in message.items())
    for secret in [HOST, USERNAME, PASSWORD, *RECIPIENTS]:
        assert secret not in headers
        assert secret not in message.as_string()


def test_body_has_only_the_four_items(categories: None) -> None:
    """第 5 条「正文……只有四项：类别（类别码与一句中文说明）、发生时间
    （……UTC 与马来西亚时间各一行……）、对象（对象类别与本站内部 ID……）、
    处理指引」「末尾固定一句限频说明」与 HANDOFF 0.88：以对象作去重键的
    类别写明限的是本封所写的对象。正文不含任何配置值与地址。"""
    h = _harness()
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"), error_class="OperationalError")
    h.dispatcher.run_due()

    body = h.smtp.connections[0].message.get_content()
    assert body.splitlines() == [
        "类别：demo_job（演示对象）",
        "发生时间（UTC）：2026-11-06 20:30:15",
        "发生时间（马来西亚时间 UTC+8）：2026-11-07 04:30:15",
        "对象：job reset_daily_stock（异常类名 OperationalError）",
        "处理指引：按类别码查日志。",
        "",
        "本类告警按对象限频，限的是本封所写的对象：同一对象在本封之后 60 分钟内不再发信，"
        "期间只记在日志里，按类别码搜索日志；其他对象照常发信。",
    ]
    for secret in SECRETS:
        assert secret not in body


def test_body_note_for_category_and_date_keys(categories: None) -> None:
    """第 5 条「没有对象写「无」」「末尾固定一句限频说明：本类告警在本封
    之后 60 分钟内（去重键为日期的为当天）不再发信……」：不以对象作去重键
    的类别照原句；去重键为日期的写当天。"""
    h = _harness()
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.enqueue(BY_DATE, ("date", "2026-11-07"))
    h.dispatcher.run_due()

    first = h.smtp.connections[0].message.get_content().splitlines()
    assert first[3] == "对象：无"
    assert first[-1] == (
        "本类告警在本封之后 60 分钟内（去重键为日期的为当天）不再发信，"
        "期间其余对象只记在日志里，按类别码搜索日志。"
    )
    second = h.smtp.connections[1].message.get_content().splitlines()
    assert second[3] == "对象：date 2026-11-07"
    assert "同一对象当天不再发信" in second[-1]


# ---------------------------------------------------------------------------
# 失败与重试（第 3 条）
# ---------------------------------------------------------------------------


def test_temporary_failure_retries_once_and_others_are_sent_meanwhile(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「临时失败……在 60 秒后重试一次」「等待重试的那封放回发信线程
    的待发列表、到时再发，等待期间线程照常发其他告警」「按到期先后取下一封
    （新入队的立即到期，等待重试的 60 秒后到期）」。"""
    h = _harness(Plan(connect=ConnectionRefusedError(111, "refused")))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))
    h.dispatcher.run_due()

    assert len(h.smtp.connections) == 2
    assert _messages(caplog) == ["ops_alert_sent category=demo_job"]

    h.clock.advance(ops_alert.RETRY_DELAY_SECONDS - 1)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 2

    # 重试 1 秒前已到期；之后才入队的那封到期更晚，排在它后面。
    h.clock.advance(2)
    h.dispatcher.enqueue(BY_DATE, ("date", "2026-11-07"))
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 4
    assert h.smtp.connections[2].message["Subject"] == "[Acuven Shop] demo_failure"
    assert h.smtp.connections[3].message["Subject"] == "[Acuven Shop] demo_daily"
    assert h.dispatcher.wait_until_sent(timeout=0) == 0


TEMPORARY = [
    ("connect", ConnectionRefusedError(111, "refused")),
    ("connect", TimeoutError("timed out")),
    ("connect", smtplib.SMTPConnectError(421, REPLY)),
    ("starttls", TimeoutError("_ssl.c handshake timed out")),
    ("starttls", ssl.SSLEOFError(8, "EOF occurred")),
    ("starttls", ConnectionResetError(104, "reset")),
    ("login", smtplib.SMTPAuthenticationError(454, REPLY)),
    ("send", smtplib.SMTPSenderRefused(451, REPLY, SENDER)),
    ("send", smtplib.SMTPDataError(450, REPLY)),
    ("send", smtplib.SMTPServerDisconnected("closed")),
]


@pytest.mark.parametrize(("stage", "error"), TEMPORARY)
def test_temporary_failures_are_tried_twice(
    categories: None, caplog: pytest.LogCaptureFixture, stage: str, error: BaseException
) -> None:
    """第 3 条「临时失败（连不上、超时、连接中断、服务器回 4xx）……重试一次，
    每封最多尝试 2 次」「最终失败……只记一条 error 日志 ops_alert_send_failed
    category=<类别> error=<异常类名>（服务器回了状态码时另带 code=…）」与
    HANDOFF 0.88「登录回 4xx 为临时」「握手时超时与连接被中断（含
    ssl.SSLEOFError）为临时失败」。"""
    h = _send_with(Plan(**{stage: error}), Plan(**{stage: error}))

    assert len(h.smtp.connections) == 2
    assert h.smtp.delivered == []
    assert _messages(caplog) == [_failure_log(error)]
    _assert_clean(caplog)


PERMANENT = [
    ("starttls", ssl.SSLCertVerificationError(1, "certificate verify failed")),
    ("starttls", ssl.SSLError(1, "sslv3 alert handshake failure")),
    ("connect", smtplib.SMTPConnectError(554, REPLY)),
    ("login", smtplib.SMTPAuthenticationError(535, REPLY)),
    ("login", smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported by server.")),
    ("send", smtplib.SMTPDataError(554, REPLY)),
    ("send", smtplib.SMTPSenderRefused(550, REPLY, SENDER)),
]


@pytest.mark.parametrize(("stage", "error"), PERMANENT)
def test_permanent_failures_are_not_retried(
    categories: None, caplog: pytest.LogCaptureFixture, stage: str, error: BaseException
) -> None:
    """第 3 条「永久失败（登录被拒、服务器回 5xx、不支持 STARTTLS、TLS 握手或
    证书校验失败）不重试」与 HANDOFF 0.88「ssl.SSLError 是 OSError 的子类，
    先于连接类错误判断为永久失败」。"""
    h = _send_with(Plan(**{stage: error}))

    assert len(h.smtp.connections) == 1
    assert h.smtp.delivered == []
    assert _messages(caplog) == [_failure_log(error)]
    _assert_clean(caplog)


def test_certificate_failure_with_ssl_is_not_retried(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「TLS 握手或证书校验失败……不重试」：ssl 方式在连接时就握手，
    证书校验失败同样是永久失败。"""
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    h = _harness(Plan(connect=error), settings=_settings(alert_smtp_security="ssl"))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    h.clock.advance(ops_alert.RETRY_DELAY_SECONDS)
    h.dispatcher.run_due()

    assert [c.kind for c in h.smtp.connections] == ["smtp_ssl"]
    assert _messages(caplog) == [_failure_log(error)]


def test_final_failure_reports_the_last_attempt(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """HANDOFF 0.88「最终失败日志的 error 与 code 取最后一次尝试的结果」：
    第一次回 451、重试连不上时 error 为连接异常的类名、不带 code。"""
    _send_with(
        Plan(send=smtplib.SMTPDataError(451, REPLY)),
        Plan(connect=ConnectionRefusedError(111, "refused")),
    )

    assert _messages(caplog) == [
        "ops_alert_send_failed category=demo_failure error=ConnectionRefusedError"
    ]


# ---------------------------------------------------------------------------
# 收件人被拒（第 3 条）
# ---------------------------------------------------------------------------


def test_partially_refused_4xx_retries_only_those(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「只有部分收件人被拒时（smtplib 此时不抛异常、返回被拒的
    收件人）……4xx 的只对这些收件人照上面重试一次」「已接受的收件人不再
    重发」「所有配置的收件人都被服务器接受才算这封发出」。"""
    h = _send_with(Plan(refuse={SECOND: 450}))

    assert [c.to_addrs for c in h.smtp.connections] == [RECIPIENTS, [SECOND]]
    assert sorted(h.smtp.delivered) == sorted(RECIPIENTS)
    assert _messages(caplog) == ["ops_alert_sent category=demo_failure"]


def test_partially_refused_5xx_is_final_without_addresses(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「5xx 的即为最终失败」「部分收件人被拒时另带 refused=<被拒
    人数>」「不带……地址」与 HANDOFF 0.88「部分收件人被拒时 error 写
    SMTPRecipientsRefused」。"""
    h = _send_with(Plan(refuse={SECOND: 550}))

    assert len(h.smtp.connections) == 1
    assert _messages(caplog) == [
        "ops_alert_send_failed category=demo_failure error=SMTPRecipientsRefused code=550 refused=1"
    ]
    _assert_clean(caplog)


def test_mixed_refusals_report_the_first_configured_recipient(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """HANDOFF 0.88「一封只记一条最终失败日志，refused 为最终仍未被接受的
    人数，code 取其中按配置顺序第一个人最后一次的状态码」：第一人 550 即
    最终失败；第三人 451 重试后全部被拒，仍为 452。"""
    h = _send_with(Plan(refuse={FIRST: 550, THIRD: 451}), Plan(refuse={THIRD: 452}))

    assert [c.to_addrs for c in h.smtp.connections] == [RECIPIENTS, [THIRD]]
    assert h.smtp.delivered == [SECOND]
    assert _messages(caplog) == [
        "ops_alert_send_failed category=demo_failure error=SMTPRecipientsRefused code=550 refused=2"
    ]


def test_all_refused_4xx_is_retried(categories: None, caplog: pytest.LogCaptureFixture) -> None:
    """第 3 条「全部收件人被拒时（smtplib 抛 SMTPRecipientsRefused，带各
    收件人的状态码）」同样按各自的状态码处理：全部 4xx 时全部重试一次。"""
    h = _send_with(Plan(refuse={FIRST: 450, SECOND: 451, THIRD: 452}))

    assert [c.to_addrs for c in h.smtp.connections] == [RECIPIENTS, RECIPIENTS]
    assert _messages(caplog) == ["ops_alert_sent category=demo_failure"]


def test_all_refused_5xx_is_final(categories: None, caplog: pytest.LogCaptureFixture) -> None:
    """第 3 条「全部收件人被拒时……5xx 的即为最终失败」；日志不带地址。"""
    h = _send_with(Plan(refuse={FIRST: 553, SECOND: 550, THIRD: 550}))

    assert len(h.smtp.connections) == 1
    assert _messages(caplog) == [
        "ops_alert_send_failed category=demo_failure error=SMTPRecipientsRefused code=553 refused=3"
    ]
    _assert_clean(caplog)


def test_refused_retry_failing_to_connect(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """HANDOFF 0.88「重试在连接阶段失败时 error 为该连接异常的类名、没有
    状态码就不带 code」；收件人被拒过的另带最终仍未被接受的人数。"""
    _send_with(Plan(refuse={SECOND: 450}), Plan(connect=TimeoutError("timed out")))

    assert _messages(caplog) == [
        "ops_alert_send_failed category=demo_failure error=TimeoutError refused=1"
    ]


# ---------------------------------------------------------------------------
# 限频（第 4 条）
# ---------------------------------------------------------------------------


def test_claim_before_sending_and_kept_after_failure(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 4 条「发信线程发送前以一条 SET <键> 1 NX EX <窗口秒数> 占用」
    「占不到就记一条 info ops_alert_suppressed category=<类别> 后丢弃；
    发送失败也不删这个键」。"""
    h = _harness(Plan(login=smtplib.SMTPAuthenticationError(535, REPLY)))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()

    assert h.redis.commands == [("set", "ops_alert:demo_failure", "1", True, 3600)]
    assert "ops_alert:demo_failure" in h.redis.values

    caplog.clear()
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 1
    assert _messages(caplog) == ["ops_alert_suppressed category=demo_failure"]

    h.clock.advance(ops_alert.HOUR_SECONDS)
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 2


def test_keys_and_windows_follow_the_category(categories: None) -> None:
    """第 4 条「键为 ops_alert: 加类别，有去重键时再加 : 与去重键；窗口为
    3,600 秒，去重键为日期的为 86,400 秒」与 HANDOFF 0.88「去重键都是对象
    本身」。"""
    h = _harness()
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))
    h.dispatcher.enqueue(BY_DATE, ("date", "2026-11-07"))
    h.dispatcher.enqueue(BY_TARGET, ("job", "cancel_expired_orders"))
    h.dispatcher.run_due()

    assert h.redis.commands == [
        ("set", "ops_alert:demo_job:reset_daily_stock", "1", True, 3600),
        ("set", "ops_alert:demo_daily:2026-11-07", "1", True, 86400),
        ("set", "ops_alert:demo_job:cancel_expired_orders", "1", True, 3600),
    ]
    assert len(h.smtp.delivered) == 3 * len(RECIPIENTS)


def test_retries_do_not_claim_again(categories: None) -> None:
    """HANDOFF 0.88「限频键在每封第一次尝试之前占用一次，重试不再占用」：
    含只对部分收件人的重试。"""
    h = _send_with(Plan(connect=TimeoutError("timed out")))
    assert len(h.smtp.connections) == 2
    assert len(h.redis.commands) == 1

    h = _send_with(Plan(refuse={THIRD: 421}))
    assert len(h.smtp.connections) == 2
    assert len(h.redis.commands) == 1


@pytest.mark.parametrize("failure", ["unconfigured", "error"])
def test_redis_unavailable_falls_back_to_process_memory(
    categories: None, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """第 4 条「Redis 未配置、连不上、超时……或出错时，改用本进程内存里
    同样的键与窗口」与 HANDOFF 0.88「Redis 不可用时在进程内存里占到的键在
    窗口内一直有效，Redis 恢复后仍先查进程内存」。"""
    h = _harness(redis_available=failure != "unconfigured")
    h.redis.fail = True
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 1

    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 1
    assert "ops_alert_suppressed category=demo_failure" in _messages(caplog)

    # Redis 恢复：窗口内仍以进程内存为准，不再去 Redis 占用。
    h.redis.fail = False
    commands = len(h.redis.commands)
    h.clock.advance(ops_alert.HOUR_SECONDS - 1)
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 1
    assert len(h.redis.commands) == commands

    h.clock.advance(1)
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    assert len(h.smtp.connections) == 2
    _assert_clean(caplog)


def test_default_redis_client_uses_two_second_timeouts() -> None:
    """第 4 条「这里的每次 Redis 操作超时 2 秒」与 HANDOFF 0.88「告警的 Redis
    客户端由模块自建，每次操作超时 2 秒」：未配置时没有客户端；建客户端
    不连接 Redis。"""
    dispatcher = ops_alert.Dispatcher(lambda: Settings(_env_file=None))
    assert dispatcher._default_redis() is None

    url = "redis://sentinel-redis.invalid:6379/7"
    dispatcher = ops_alert.Dispatcher(lambda: Settings(_env_file=None, redis_url=url))
    try:
        client = dispatcher._default_redis()
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["socket_timeout"] == 2
        assert kwargs["socket_connect_timeout"] == 2
    finally:
        ops_alert._client_for.cache_clear()


# ---------------------------------------------------------------------------
# 测试告警与补跑命令的等待（第 1、2 条）
# ---------------------------------------------------------------------------


def test_send_test_alert_is_synchronous_and_not_rate_limited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 1 条「在命令自己的进程里同步发一封类别为 ops_alert_test 的告警
    （对象为「无」），不经队列、不受第 4 条限频」与 HANDOFF 0.88
    「ops_alert_test 不受限频，正文末尾不写限频说明」。"""
    smtp = FakeSmtp()
    for _ in range(2):
        result = ops_alert.send_test_alert(
            _settings(), smtp=smtp.smtp, smtp_ssl=smtp.smtp_ssl, utc_now=lambda: START
        )
        assert result == ops_alert.SENT
    assert len(smtp.connections) == 2

    message = smtp.connections[0].message
    assert message["Subject"] == "[Acuven Shop] ops_alert_test"
    assert message.get_content().splitlines() == [
        "类别：ops_alert_test（测试告警）",
        "发生时间（UTC）：2026-11-06 20:30:15",
        "发生时间（马来西亚时间 UTC+8）：2026-11-07 04:30:15",
        "对象：无",
        "处理指引：收到即说明告警邮件配置可用，无须处理。",
    ]
    assert _messages(caplog) == ["ops_alert_sent category=ops_alert_test"] * 2


def test_send_test_alert_results(caplog: pytest.LogCaptureFixture) -> None:
    """第 1 条「照第 3 条的超时与重试，最终由服务器接受即以 0 退出；未配置时
    记上面的 warning 并以非零退出码退出，最终失败也以非零退出码退出」：
    临时失败等 60 秒重试一次。"""
    assert ops_alert.send_test_alert(Settings(_env_file=None)) == ops_alert.NOT_CONFIGURED
    assert _messages(caplog)[0].startswith("ops_alert_not_configured missing=")

    sleeps: list[float] = []
    smtp = FakeSmtp(Plan(connect=TimeoutError("timed out")))
    result = ops_alert.send_test_alert(
        _settings(), smtp=smtp.smtp, smtp_ssl=smtp.smtp_ssl, sleep=sleeps.append
    )
    assert (result, sleeps, len(smtp.connections)) == (ops_alert.SENT, [60.0], 2)

    sleeps.clear()
    smtp = FakeSmtp(Plan(login=smtplib.SMTPAuthenticationError(535, REPLY)))
    result = ops_alert.send_test_alert(
        _settings(), smtp=smtp.smtp, smtp_ssl=smtp.smtp_ssl, sleep=sleeps.append
    )
    assert (result, sleeps, len(smtp.connections)) == (ops_alert.FAILED, [], 1)
    assert _messages(caplog)[-1] == (
        "ops_alert_send_failed category=ops_alert_test error=SMTPAuthenticationError code=535"
    )
    _assert_clean(caplog)


def test_wait_until_sent_waits_for_the_retry(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 2 条「补跑命令退出前等队列发完（含第 3 条的一次重试），最多等
    180 秒」：重试在等待期间发出，返回 0、不记未发封数。"""
    holder: dict[str, Harness] = {}

    def wait(condition: threading.Condition, timeout: float) -> None:
        holder["h"].dispatcher.run_due()
        holder["h"].clock.advance(30)

    h = holder["h"] = _harness(Plan(connect=TimeoutError("timed out")), wait=wait)
    h.dispatcher.enqueue(CATEGORY)

    assert h.dispatcher.wait_until_sent() == 0
    assert len(h.smtp.connections) == 2
    assert _messages(caplog) == ["ops_alert_sent category=demo_failure"]


def test_wait_until_sent_gives_up_after_180_seconds(
    categories: None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 2 条「到时仍有未发完的，记一条 warning ops_alert_unsent
    count=<封数> 后照常退出」：等待重试的与仍在队列里的都算。"""
    h = _harness(Plan(connect=TimeoutError("timed out")))
    h.dispatcher.enqueue(CATEGORY)
    h.dispatcher.run_due()
    h.dispatcher.enqueue(BY_TARGET, ("job", "reset_daily_stock"))

    assert h.dispatcher.wait_until_sent() == 2
    assert sum(h.waits) == ops_alert.DRAIN_TIMEOUT_SECONDS == 180
    assert _messages(caplog) == ["ops_alert_unsent count=2"]


def test_wait_until_sent_returns_at_once_when_idle() -> None:
    """第 2 条「补跑命令退出前等队列发完」：没有告警时不等。"""
    h = _harness()
    assert h.dispatcher.wait_until_sent() == 0
    assert h.waits == []


def test_real_sender_thread_does_not_block_callers(categories: None) -> None:
    """第 2 条「API 与定时任务每个进程各有一个发信线程」「以不阻塞的方式
    放进队列」：发信线程是守护线程；它卡在 SMTP 上时，调用方照样立即入队。"""
    entered = threading.Event()
    release = threading.Event()
    smtp = FakeSmtp(Plan(entered=entered, block=release))
    fake_redis = FakeRedis(FakeClock())
    threads: list[threading.Thread] = []

    def start(target: Any) -> None:
        thread = threading.Thread(target=target, daemon=True, name="ops-alert-sender-test")
        threads.append(thread)
        thread.start()

    dispatcher = ops_alert.Dispatcher(
        lambda: _settings(),
        smtp=smtp.smtp,
        smtp_ssl=smtp.smtp_ssl,
        redis_client=lambda: fake_redis,
        start_thread=start,
    )
    try:
        dispatcher.enqueue(CATEGORY)
        assert entered.wait(5)
        target = ("job", "reset_daily_stock")
        caller = threading.Thread(target=dispatcher.enqueue, args=(BY_TARGET, target))
        caller.start()
        caller.join(5)
        assert not caller.is_alive()
        assert len(threads) == 1 and threads[0].daemon
    finally:
        release.set()
    assert dispatcher.wait_until_sent(timeout=5) == 0
    assert len(smtp.connections) == 2


# ---------------------------------------------------------------------------
# 类别表（第 5、6 条）
# ---------------------------------------------------------------------------


def test_categories_text_has_no_links_or_addresses() -> None:
    """第 5 条「处理指引同样不含链接与主机」与 HANDOFF 0.88「217 的测试对
    表里每一项检查说明与指引不含链接与地址」；第 6 条 ops_alert_test 的
    说明为「测试告警」、处理指引为「收到即说明告警邮件配置可用，无须
    处理」、不限频。"""
    link = re.compile(r"://|www\.|@|\b[\w-]+\.(com|net|org|io|my|sg|dev|app|cloud)\b", re.I)
    for code, spec in ops_alert.CATEGORIES.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", code)
        for text in (spec.description, spec.guidance):
            assert isinstance(text, str) and text.strip(), code
            assert not link.search(text), code
        assert isinstance(spec.dedupe_by_target, bool)
        if code == ops_alert.TEST_CATEGORY:
            assert spec.window_seconds is None
        else:
            assert spec.window_seconds in (ops_alert.HOUR_SECONDS, ops_alert.DAY_SECONDS)

    test = ops_alert.CATEGORIES[ops_alert.TEST_CATEGORY]
    assert test.description == "测试告警"
    assert test.guidance == "收到即说明告警邮件配置可用，无须处理。"
    assert test.dedupe_by_target is False
