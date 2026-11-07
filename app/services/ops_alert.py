"""运营告警邮件的发送机制：配置、入队与合并、发信线程、SMTP 发信与重试、限频、测试告警。

依据 docs/DESIGN.md 1.21（提交 5be346e）「运营告警邮件」第 1 至 5 条与第 9 条，
以及 docs/HANDOFF.md 0.88 记录的落实写法（SHOP-TASK-217）。各业务告警点的接线
（第 6、7 条）由 SHOP-TASK-218 至 222 完成，各自把类别加进 CATEGORIES；定时任务失败的
job_failing 与 stock_reset_failed 由 SHOP-TASK-218 在 app/jobs.py 接入；Stripe 结账创建与
对账的五类（stripe_key_not_test、stripe_checkout_needs_review、stripe_create_rejected、
stripe_unreachable、stripe_payment_needs_review）由 SHOP-TASK-219 接入；Stripe 确认付款与
webhook 的六类（stripe_livemode、stripe_confirm_rejected、stripe_session_mismatch、
stripe_late_payment、stripe_late_payment_unrefundable、stripe_refund_cap）由 SHOP-TASK-220
接入，stripe_refund_cap 的退款申请告警点由 SHOP-TASK-221 沿用。

调用方的写法（HANDOFF 0.88）：

    from app.services import ops_alert
    ops_alert.alert("job_failing", ("job", "reset_daily_stock"), error_class="OperationalError")

target 为（对象类别, 内部 ID）二元组或 None；测试以 monkeypatch 替换模块属性 alert。
alert 只在调用方的线程里做内存操作：不访问数据库与网络，任何情况下都不抛异常、不阻塞。

- 配置（第 1 条）：七项 SHOP_ALERT_ 变量见 parse_config；缺一或不合格即未配置，
  告警不入队，进程第一次遇到告警时记一条 warning ops_alert_not_configured
  （只带变量名，不带配置值）。
- 入队（第 2 条）：每个进程一个守护发信线程（第一次有告警入队时才启动）与容量
  QUEUE_CAPACITY 的内存队列。同一限频键已在队列里、正在发送或等待重试时不再入队
  （info ops_alert_suppressed）；队列满即丢弃（warning ops_alert_dropped）。
  等待重试的告警在发信线程的待发列表里，不占队列容量。
- 发信（第 3、5 条）：每封新建一次连接，连接、TLS 握手与每条命令的超时都是
  SMTP_TIMEOUT_SECONDS；starttls 必须完成 STARTTLS 才登录与发信，ssl 一开始就以
  TLS 连接，TLS 一律用 ssl.create_default_context()。临时失败在 RETRY_DELAY_SECONDS
  后重试一次，永久失败不重试，收件人被拒按各自的状态码处理，见 _classify 与 _settle。
- 限频（第 4 条）：每封第一次尝试之前以 SET <键> 1 NX EX <窗口> 占用一次，
  Redis 不可用时改用本进程内存里同样的键与窗口，见 RateLimiter。
- 补跑命令退出前以 wait_until_sent 等队列发完；send_test_alert 供 send-test-alert
  子命令同步发一封 ops_alert_test，不经队列、不受限频。

日志只记类别、异常类名、状态码与人数，不带配置值、服务器回复原文、主机、账号或地址。
本模块不访问 MySQL。
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from functools import lru_cache
from typing import Any

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 发信线程的内存队列容量（第 2 条）。等待重试的告警不占容量。
QUEUE_CAPACITY = 100
# 连接、TLS 握手与每条 SMTP 命令的超时（秒，第 3 条）。
SMTP_TIMEOUT_SECONDS = 10.0
# 临时失败后等这么久重试一次；每封最多尝试 MAX_ATTEMPTS 次（第 3 条）。
RETRY_DELAY_SECONDS = 60.0
MAX_ATTEMPTS = 2
# 补跑命令退出前最多等队列这么久（第 2 条）。
DRAIN_TIMEOUT_SECONDS = 180.0
# 告警的每次 Redis 操作超时（秒，第 4 条）。
REDIS_TIMEOUT_SECONDS = 2.0

# 限频窗口（第 4 条）：同类 60 分钟一封，去重键为日期的一天一封。
HOUR_SECONDS = 3600
DAY_SECONDS = 86400

KEY_PREFIX = "ops_alert"

DEFAULT_PORT = 587
SECURITY_STARTTLS = "starttls"
SECURITY_SSL = "ssl"

# 邮件主题前缀与固定的空收件人组（第 5 条）。
SUBJECT_PREFIX = "[Acuven Shop]"
UNDISCLOSED_RECIPIENTS = "undisclosed-recipients:;"
MAX_RECIPIENTS = 5

MALAYSIA_TZ = timezone(timedelta(hours=8))

# send_test_alert 与一封告警的结果。
SENT = "sent"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"
_RETRY = "retry"
_DISCARDED = "discarded"


@dataclass(frozen=True)
class AlertCategory:
    """一个告警类别（第 6 条表里的一行）。

    description 是邮件「类别」一项的一句中文说明；guidance 是「处理指引」的一段
    固定文字；两者都不得含链接、主机与地址。dedupe_by_target 为真时以对象作去重键
    （限频键再加对象的内部 ID）。window_seconds 是限频窗口秒数（HOUR_SECONDS，
    去重键为日期的为 DAY_SECONDS）；None 表示不限频，只有 ops_alert_test。
    """

    description: str
    guidance: str
    dedupe_by_target: bool
    window_seconds: int | None


TEST_CATEGORY = "ops_alert_test"

# 类别码（英文小写加下划线）到类别。SHOP-TASK-218 至 222 各把自己的类别加进来。
CATEGORIES: dict[str, AlertCategory] = {
    TEST_CATEGORY: AlertCategory(
        description="测试告警",
        guidance="收到即说明告警邮件配置可用，无须处理。",
        dedupe_by_target=False,
        window_seconds=None,
    ),
    # SHOP-TASK-218：定时任务失败（第 6 条表与第 7 条）。
    "job_failing": AlertCategory(
        description="定时任务连续失败满 10 分钟",
        guidance=(
            "按类别码与任务名查定时任务容器的日志，按异常类名排查原因；修复后该任务"
            "在下一轮成功即恢复，也可执行该任务的补跑命令（python -m app.jobs --help "
            "列出各子命令）核对。"
        ),
        dedupe_by_target=True,
        window_seconds=HOUR_SECONDS,
    ),
    "stock_reset_failed": AlertCategory(
        description="当日库存重置失败",
        guidance=(
            "按类别码与营业日期查定时任务容器的日志，按异常类名排查原因；常驻循环下一轮"
            "会重试，修复后也可执行补跑命令 python -m app.jobs reset-stock，"
            "再到后台库存重置页核对当日的结果。"
        ),
        dedupe_by_target=True,
        window_seconds=DAY_SECONDS,
    ),
    # SHOP-TASK-219：Stripe 结账创建与对账（第 6 条表），都按类别限频。
    "stripe_key_not_test": AlertCategory(
        description="配置的 Stripe 密钥不是测试密钥，Stripe 支付未启用",
        guidance=(
            "核对私有配置里的 Stripe 密钥，只能配置 Stripe 测试环境的 sk_test_ 或 rk_test_ "
            "密钥，不得配置正式环境的任何密钥；改好后重启 API 与定时任务容器，"
            "按类别码查日志确认不再出现。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_checkout_needs_review": AlertCategory(
        description="Stripe 结账会话的创建结果 24 小时仍无法确定，已转人工核对",
        guidance=(
            "按类别码与结账记录 ID 查日志，到 Stripe 测试后台核对这笔会话是否建成及其"
            "付款状态；订单在此期间不取消、库存继续预留，本站不再自动处理这条记录。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_create_rejected": AlertCategory(
        description="Stripe 以参数不合格拒绝创建结账会话，结账记录已记为过期",
        guidance=(
            "按类别码与结账记录 ID 查日志，到 Stripe 测试后台核对被拒的原因（如付款方式"
            "或账户设置）；访客可重新发起支付，反复出现时排查创建参数与 Stripe 测试后台的设置。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_unreachable": AlertCategory(
        description="Stripe 结账会话 24 小时仍无法查询",
        guidance=(
            "按类别码与结账记录 ID 查日志，核对 Stripe 配置与 Stripe 测试后台的服务状态，"
            "并到 Stripe 测试后台核对这笔会话；订单不自动取消、库存继续预留，"
            "Stripe 恢复后定时任务按会话的实际状态处理。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_payment_needs_review": AlertCategory(
        description="Stripe 结账会话完成 24 小时后付款仍无结果",
        guidance=(
            "按类别码与结账记录 ID 查日志，到 Stripe 测试后台核对这笔付款的状态；"
            "订单照旧待支付、库存继续预留，定时任务仍会查询并以 Stripe 给出的结果为准。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    # SHOP-TASK-220：Stripe 确认付款与 webhook（第 6 条表），都按类别限频。
    "stripe_livemode": AlertCategory(
        description="Stripe 会话、付款或 webhook 事件为正式环境，已拒绝",
        guidance=(
            "按类别码查日志（有结账记录 ID 时一并核对），核对私有配置里的 Stripe 密钥与 "
            "webhook 签名密钥都只属于 Stripe 测试环境，到 Stripe 测试后台核对 webhook 端点"
            "的设置；本站不处理正式环境的会话、付款与事件，订单与记录都未改。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_confirm_rejected": AlertCategory(
        description="确认付款时向 Stripe 查询结账会话被以请求错误拒绝",
        guidance=(
            "按类别码与结账记录 ID 查日志，到 Stripe 测试后台核对这笔会话是否存在、"
            "是否属于当前配置的测试账户；订单与记录都未改，访客返回、webhook 重发或"
            "定时任务会再次查询。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_session_mismatch": AlertCategory(
        description="Stripe 返回的结账会话与本站记录的创建幂等键或会话不符，未确认付款",
        guidance=(
            "按类别码与结账记录 ID 查日志，到 Stripe 测试后台核对这笔会话属于哪条记录与"
            "哪张订单；订单与记录都未改，反复出现时排查 webhook 端点与 Stripe 测试账户的设置。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_late_payment": AlertCategory(
        description="迟到或不符的 Stripe 付款，已写全额退款尝试",
        guidance=(
            "按类别码与结账记录 ID 查日志，订单状态未改；退款由定时任务向 Stripe 发起，"
            "到 Stripe 测试后台核对这笔付款的退款结果，退款出问题时另有退款类告警。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    "stripe_late_payment_unrefundable": AlertCategory(
        description="迟到或不符的 Stripe 付款缺金额或 PaymentIntent，写不了退款尝试",
        guidance=(
            "按类别码与结账记录 ID 查日志，到 Stripe 测试后台核对这笔付款，需要退款时在 "
            "Stripe 测试后台人工处理；订单状态未改，本站不再为这笔付款自动退款。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
    # 第 6 条（迟到付款，对象为结账记录）与第 8 条（退款申请，SHOP-TASK-221 接入）共用。
    "stripe_refund_cap": AlertCategory(
        description="Stripe 退款合计会超过付款金额，未建退款尝试（迟到付款或退款申请）",
        guidance=(
            "按类别码与对象 ID（结账记录或退款申请）查日志，到 Stripe 测试后台核对这笔付款"
            "已有的退款与金额；本站没有为此发起退款，需要时在后台退款申请页或 Stripe 测试"
            "后台人工处理。"
        ),
        dedupe_by_target=False,
        window_seconds=HOUR_SECONDS,
    ),
}

# 对象类别码（如 stripe_refund、job、date）与内部 ID（非负整数，或任务名、
# ISO 日期这样的短文本）；异常类名（或固定的 database_not_configured）。
_TARGET_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_TARGET_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_ERROR_CLASS_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
_DIGITS = re.compile(r"[0-9]+")

Target = tuple[str, int | str]


# ---------------------------------------------------------------------------
# 配置（第 1 条）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]

    def __repr__(self) -> str:
        # 不让配置值随 repr 进日志或异常。
        return "SmtpConfig(...)"


@dataclass(frozen=True)
class ConfigCheck:
    """parse_config 的结果：config 为空即未配置；missing 与 invalid 只是变量名。"""

    config: SmtpConfig | None
    missing: tuple[str, ...]
    invalid: tuple[str, ...]


def _valid_address(address: str) -> bool:
    if address.count("@") != 1:
        return False
    local, domain = address.split("@")
    return bool(local) and bool(domain)


def parse_config(settings: Settings) -> ConfigCheck:
    """按第 1 条判断七项配置。

    端口为空即 587、给了须为 1 到 65535 的整数；加密方式为空即 starttls、给了须为
    starttls 或 ssl；主机、账号、密码非空；发件人一个地址、收件人逗号分隔 1 到 5 个
    地址，每个地址恰含一个 @ 且两侧非空（去掉两侧空白）。
    """
    missing: list[str] = []
    invalid: list[str] = []

    host = settings.alert_smtp_host.strip()
    if not host:
        missing.append("SHOP_ALERT_SMTP_HOST")

    port = DEFAULT_PORT
    port_text = settings.alert_smtp_port.strip()
    if port_text:
        if _DIGITS.fullmatch(port_text) and 1 <= int(port_text) <= 65535:
            port = int(port_text)
        else:
            invalid.append("SHOP_ALERT_SMTP_PORT")

    security = settings.alert_smtp_security.strip() or SECURITY_STARTTLS
    if security not in (SECURITY_STARTTLS, SECURITY_SSL):
        invalid.append("SHOP_ALERT_SMTP_SECURITY")

    username = settings.alert_smtp_username
    if not username.strip():
        missing.append("SHOP_ALERT_SMTP_USERNAME")
    password = settings.alert_smtp_password
    if not password.strip():
        missing.append("SHOP_ALERT_SMTP_PASSWORD")

    sender = settings.alert_from.strip()
    if not sender:
        missing.append("SHOP_ALERT_FROM")
    elif not _valid_address(sender):
        invalid.append("SHOP_ALERT_FROM")

    recipients: tuple[str, ...] = ()
    to_text = settings.alert_to.strip()
    if not to_text:
        missing.append("SHOP_ALERT_TO")
    else:
        recipients = tuple(part.strip() for part in to_text.split(","))
        valid = all(_valid_address(address) for address in recipients)
        if not valid or len(recipients) > MAX_RECIPIENTS:
            invalid.append("SHOP_ALERT_TO")

    if missing or invalid:
        return ConfigCheck(None, tuple(missing), tuple(invalid))
    config = SmtpConfig(host, port, security, username, password, sender, recipients)
    return ConfigCheck(config, (), ())


def _log_not_configured(check: ConfigCheck) -> None:
    parts = ["ops_alert_not_configured"]
    if check.missing:
        parts.append("missing=" + ",".join(check.missing))
    if check.invalid:
        parts.append("invalid=" + ",".join(check.invalid))
    logger.warning("%s", " ".join(parts))


# ---------------------------------------------------------------------------
# 限频（第 4 条）
# ---------------------------------------------------------------------------


def _no_retry() -> Retry:
    # redis-py 6 起默认会重试；这里显式关掉，失败立即改用进程内存。
    return Retry(NoBackoff(), 0)


@lru_cache
def _client_for(redis_url: str) -> redis.Redis:
    # 写法照 app/services/rate_limit.py 的 _client_for，超时为 2 秒。
    # 只建客户端与连接池，第一次执行命令时才连接。
    pool = redis.ConnectionPool.from_url(
        redis_url,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        retry=_no_retry(),
    )
    return redis.Redis(connection_pool=pool, retry=_no_retry())


class RateLimiter:
    """限频键的占用（只在发信线程里调用）。

    先查本进程内存：内存里占到的键在窗口内一直有效（Redis 恢复后也一样）。
    再以 SET <键> 1 NX EX <窗口> 在 Redis 占用；Redis 未配置、连不上、超时或出错时
    改在内存里以同样的键与窗口占用。占用不释放，到窗口结束为止。
    """

    def __init__(self, client: Callable[[], Any], monotonic: Callable[[], float]) -> None:
        self._client = client
        self._monotonic = monotonic
        self._memory: dict[str, float] = {}

    def claim(self, key: str, window_seconds: int) -> bool:
        """占到返回 True（这封可以发），占不到返回 False。"""
        now = self._monotonic()
        for stale in [k for k, expires in self._memory.items() if expires <= now]:
            del self._memory[stale]
        if key in self._memory:
            return False
        try:
            client = self._client()
            if client is not None:
                return bool(client.set(key, "1", nx=True, ex=window_seconds))
        except Exception:
            pass
        self._memory[key] = now + window_seconds
        return True


def rate_limit_key(category: str, target: Target | None) -> str:
    """限频键：ops_alert:<类别>；类别以对象作去重键且有对象时再加 :<内部 ID>。"""
    spec = CATEGORIES.get(category)
    if spec is not None and spec.dedupe_by_target and target is not None:
        return f"{KEY_PREFIX}:{category}:{target[1]}"
    return f"{KEY_PREFIX}:{category}"


# ---------------------------------------------------------------------------
# 邮件内容（第 5 条）
# ---------------------------------------------------------------------------


def _rate_limit_note(spec: AlertCategory) -> str | None:
    window = spec.window_seconds
    if window is None:
        return None
    if not spec.dedupe_by_target:
        return (
            "本类告警在本封之后 60 分钟内（去重键为日期的为当天）不再发信，"
            "期间其余对象只记在日志里，按类别码搜索日志。"
        )
    if window == DAY_SECONDS:
        period = "当天"
    else:
        period = f"在本封之后 {window // 60} 分钟内"
    return (
        f"本类告警按对象限频，限的是本封所写的对象：同一对象{period}不再发信，"
        "期间只记在日志里，按类别码搜索日志；其他对象照常发信。"
    )


def render_body(
    category: str,
    target: Target | None,
    error_class: str | None,
    occurred_at: datetime,
) -> str:
    """正文：类别、发生时间（UTC 与马来西亚时间各一行）、对象、处理指引四项，
    末尾为限频说明（不限频的类别不写）。"""
    spec = CATEGORIES[category]
    utc = occurred_at.astimezone(UTC)
    malaysia = occurred_at.astimezone(MALAYSIA_TZ)
    subject = "无" if target is None else f"{target[0]} {target[1]}"
    if error_class is not None:
        subject += f"（异常类名 {error_class}）"
    lines = [
        f"类别：{category}（{spec.description}）",
        f"发生时间（UTC）：{utc:%Y-%m-%d %H:%M:%S}",
        f"发生时间（马来西亚时间 UTC+8）：{malaysia:%Y-%m-%d %H:%M:%S}",
        f"对象：{subject}",
        f"处理指引：{spec.guidance}",
    ]
    note = _rate_limit_note(spec)
    if note is not None:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def build_message(alert: _Alert, config: SmtpConfig) -> EmailMessage:
    """纯文本 UTF-8 邮件。From 为发件人；收件人只在 SMTP 信封里，To 固定为
    空收件人组，不设 Cc 与 Bcc；Message-ID 以发件人地址的域名生成。"""
    body = render_body(alert.category, alert.target, alert.error_class, alert.occurred_at)
    message = EmailMessage()
    message["Subject"] = f"{SUBJECT_PREFIX} {alert.category}"
    message["From"] = config.sender
    message["To"] = UNDISCLOSED_RECIPIENTS
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=config.sender.split("@")[1])
    message.set_content(body, charset="utf-8")
    return message


# ---------------------------------------------------------------------------
# 发信、失败分类与重试（第 3 条）
# ---------------------------------------------------------------------------


@dataclass
class _Alert:
    category: str
    target: Target | None
    error_class: str | None
    # 入队时的 UTC 时刻（带时区）。
    occurred_at: datetime
    key: str
    seq: int = 0
    # 到期时刻（monotonic）：新入队的立即到期，等待重试的 60 秒后到期。
    due: float = 0.0
    attempts: int = 0
    config: SmtpConfig | None = None
    # 这次要发的收件人（第一次为全部，重试时为 4xx 被拒的那些），按配置顺序。
    pending: tuple[str, ...] = ()
    # 被拒收件人最后一次的状态码；5xx 被拒（即最终失败）的另记在 rejected 里。
    codes: dict[str, int] = field(default_factory=dict)
    rejected: set[str] = field(default_factory=set)

    def start(self, config: SmtpConfig) -> None:
        self.config = config
        self.pending = config.recipients


@dataclass(frozen=True)
class _Outcome:
    error: BaseException | None
    # 被拒的收件人到（状态码, 回复原文）；回复原文不进日志。
    refused: dict[str, Any]


SmtpFactory = Callable[..., Any]


def _status_code(value: object) -> int | None:
    if isinstance(value, tuple) and value:
        value = value[0]
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 999:
        return value
    return None


def _temporary_code(code: int | None) -> bool:
    return code is not None and 400 <= code <= 499


def _classify(exc: BaseException) -> tuple[bool, int | None]:
    """返回（是否临时失败, 状态码）。

    服务器回了状态码的（含登录时）一律按状态码分类：4xx 临时，其余永久。
    不支持 STARTTLS（或 AUTH）为永久。ssl.SSLError 是 OSError 的子类，先于连接类
    错误判断：连接被对方中断（SSLEOFError、SSLZeroReturnError）为临时，其余
    （含证书校验失败）为永久。连不上、超时与连接中断为临时；其余意外的异常为永久。
    """
    if isinstance(exc, smtplib.SMTPResponseException):
        code = _status_code(exc.smtp_code)
        return _temporary_code(code), code
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True, None
    if isinstance(exc, smtplib.SMTPException):
        # 含 SMTPNotSupportedError（不支持 STARTTLS 或 AUTH）。
        return False, None
    if isinstance(exc, ssl.SSLError):
        return isinstance(exc, ssl.SSLEOFError | ssl.SSLZeroReturnError), None
    if isinstance(exc, OSError):
        return True, None
    return False, None


def _attempt(alert: _Alert, smtp: SmtpFactory | None, smtp_ssl: SmtpFactory | None) -> _Outcome:
    """新建一次连接，向 alert.pending 发一次。不抛异常。"""
    config = alert.config
    assert config is not None
    try:
        message = build_message(alert, config)
        context = ssl.create_default_context()
        if config.security == SECURITY_SSL:
            factory = smtp_ssl or smtplib.SMTP_SSL
            client = factory(
                config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS, context=context
            )
        else:
            factory = smtp or smtplib.SMTP
            client = factory(config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS)
        try:
            if config.security == SECURITY_STARTTLS:
                # 服务器不支持时 starttls 抛 SMTPNotSupportedError；回了 220 以外的
                # 状态码时它不抛异常、原样返回，这里必须当作失败，不以明文接着登录。
                code, _ = client.starttls(context=context)
                if code != 220:
                    raise smtplib.SMTPResponseException(code, b"")
            client.login(config.username, config.password)
            recipients = list(alert.pending)
            try:
                # 位置参数依次为邮件、信封发件人与信封收件人。
                refused = client.send_message(message, config.sender, recipients)
            except smtplib.SMTPRecipientsRefused as exc:
                return _Outcome(exc, dict(exc.recipients))
            try:
                client.quit()
            except Exception:
                pass
            return _Outcome(None, dict(refused or {}))
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as exc:
        return _Outcome(exc, {})


def _log_failed(alert: _Alert, error: str, code: int | None, refused: int | None) -> None:
    parts = [f"ops_alert_send_failed category={alert.category} error={error}"]
    if code is not None:
        parts.append(f"code={code}")
    if refused is not None:
        parts.append(f"refused={refused}")
    logger.error("%s", " ".join(parts))


def _settle(alert: _Alert, outcome: _Outcome) -> str:
    """记下一次尝试的结果，返回 SENT、FAILED 或 _RETRY；最终结果在这里记一条日志。

    error 与 code 取最后一次尝试的结果；收件人被拒过的另带 refused（最终仍未被接受
    的人数），只有收件人被拒时 code 取其中按配置顺序第一个人最后一次的状态码。
    """
    alert.attempts += 1
    can_retry = alert.attempts < MAX_ATTEMPTS
    error = outcome.error
    config = alert.config
    assert config is not None

    if error is not None and not isinstance(error, smtplib.SMTPRecipientsRefused):
        temporary, code = _classify(error)
        if temporary and can_retry:
            return _RETRY
        refused = len(set(alert.pending) | alert.rejected) if alert.codes else None
        _log_failed(alert, type(error).__name__, code, refused)
        return FAILED

    retryable: list[str] = []
    for address in alert.pending:
        if address not in outcome.refused:
            continue
        code = _status_code(outcome.refused[address])
        alert.codes[address] = code or 0
        if _temporary_code(code):
            retryable.append(address)
        else:
            alert.rejected.add(address)

    if retryable and can_retry:
        alert.pending = tuple(retryable)
        return _RETRY
    unaccepted = alert.rejected | set(retryable)
    if not unaccepted:
        logger.info("ops_alert_sent category=%s", alert.category)
        return SENT
    first = next(address for address in config.recipients if address in unaccepted)
    _log_failed(alert, "SMTPRecipientsRefused", alert.codes[first] or None, len(unaccepted))
    return FAILED


# ---------------------------------------------------------------------------
# 队列与发信线程（第 2 条）
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _start_daemon(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="ops-alert-sender", daemon=True).start()


def _wait(condition: threading.Condition, timeout: float) -> None:
    condition.wait(timeout)


def _valid_target(target: object) -> bool:
    if target is None:
        return True
    if not isinstance(target, tuple) or len(target) != 2:
        return False
    kind, internal_id = target
    if not isinstance(kind, str) or not _TARGET_KIND_PATTERN.fullmatch(kind):
        return False
    if isinstance(internal_id, bool):
        return False
    if isinstance(internal_id, int):
        return internal_id >= 0
    return isinstance(internal_id, str) and bool(_TARGET_ID_PATTERN.fullmatch(internal_id))


def _valid_error_class(error_class: object) -> bool:
    if error_class is None:
        return True
    return isinstance(error_class, str) and bool(_ERROR_CLASS_PATTERN.fullmatch(error_class))


class Dispatcher:
    """一个进程的告警队列与发信线程。

    enqueue 只在调用方的线程里做内存操作；发信线程反复调用 run_due，把已到期的
    告警按到期先后逐封发出（限频判断与 SMTP 都在这里），再等到下一封到期或有新
    告警入队。测试直接调用 run_due 同步推进；配置、SMTP、Redis、时钟、线程的启动
    与等待都可注入。
    """

    def __init__(
        self,
        settings: Callable[[], Settings] = get_settings,
        *,
        smtp: SmtpFactory | None = None,
        smtp_ssl: SmtpFactory | None = None,
        redis_client: Callable[[], Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
        start_thread: Callable[[Callable[[], None]], None] = _start_daemon,
        wait: Callable[[threading.Condition, float], None] = _wait,
    ) -> None:
        self._settings = settings
        self._smtp = smtp
        self._smtp_ssl = smtp_ssl
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._start_thread = start_thread
        self._wait = wait
        client = redis_client if redis_client is not None else self._default_redis
        self._limiter = RateLimiter(client, monotonic)
        self._cond = threading.Condition(threading.RLock())
        self._queue: deque[_Alert] = deque()
        self._retry: list[_Alert] = []
        self._sending: _Alert | None = None
        # 已在队列里、正在发送或等待重试的限频键。
        self._active: set[str] = set()
        self._seq = 0
        self._started = False
        self._warned_not_configured = False

    def _default_redis(self) -> redis.Redis | None:
        redis_url = self._settings().redis_url
        if not redis_url:
            return None
        return _client_for(redis_url)

    def _configured(self) -> SmtpConfig | None:
        check = parse_config(self._settings())
        if check.config is None:
            with self._cond:
                first = not self._warned_not_configured
                self._warned_not_configured = True
            if first:
                _log_not_configured(check)
        return check.config

    # -- 调用方的线程 -----------------------------------------------------

    def enqueue(
        self, category: object, target: object = None, *, error_class: object = None
    ) -> None:
        known = isinstance(category, str) and category in CATEGORIES
        if not known or category == TEST_CATEGORY:
            logger.warning("ops_alert_unknown_category category=%s", category)
            return
        if not _valid_target(target) or not _valid_error_class(error_class):
            logger.warning("ops_alert_invalid_target category=%s", category)
            return
        if self._configured() is None:
            return
        key = rate_limit_key(category, target)
        with self._cond:
            if key in self._active:
                logger.info("ops_alert_suppressed category=%s", category)
                return
            if len(self._queue) >= QUEUE_CAPACITY:
                logger.warning("ops_alert_dropped category=%s", category)
                return
            if not self._started:
                self._start_thread(self._run)
                self._started = True
            self._seq += 1
            self._queue.append(
                _Alert(
                    category=category,
                    target=target,
                    error_class=error_class,
                    occurred_at=self._utc_now(),
                    key=key,
                    seq=self._seq,
                    due=self._monotonic(),
                )
            )
            self._active.add(key)
            self._cond.notify_all()

    def wait_until_sent(self, timeout: float = DRAIN_TIMEOUT_SECONDS) -> int:
        """等队列（含等待重试的）发完，最多 timeout 秒；返回到时仍未发完的封数，
        大于 0 时记一条 warning ops_alert_unsent。"""
        deadline = self._monotonic() + timeout
        with self._cond:
            while True:
                count = self._unsent_locked()
                remaining = deadline - self._monotonic()
                if count == 0 or remaining <= 0:
                    break
                self._wait(self._cond, remaining)
        if count:
            logger.warning("ops_alert_unsent count=%d", count)
        return count

    def _unsent_locked(self) -> int:
        return len(self._queue) + len(self._retry) + (self._sending is not None)

    # -- 发信线程 ---------------------------------------------------------

    def _next_locked(self) -> _Alert | None:
        candidates = list(self._retry)
        if self._queue:
            candidates.append(self._queue[0])
        if not candidates:
            return None
        return min(candidates, key=lambda alert: (alert.due, alert.seq))

    def _delay_locked(self) -> float | None:
        upcoming = self._next_locked()
        if upcoming is None:
            return None
        return max(0.0, upcoming.due - self._monotonic())

    def _pop_due_locked(self) -> _Alert | None:
        upcoming = self._next_locked()
        if upcoming is None or upcoming.due > self._monotonic():
            return None
        if self._queue and upcoming is self._queue[0]:
            self._queue.popleft()
        else:
            self._retry.remove(upcoming)
        return upcoming

    def run_due(self) -> None:
        """逐封发出已到期的告警，没有到期的就返回。"""
        while True:
            with self._cond:
                alert = self._pop_due_locked()
                if alert is None:
                    return
                self._sending = alert
            try:
                result = self._process(alert)
            except Exception as exc:
                error = type(exc).__name__
                logger.error("ops_alert_send_failed category=%s error=%s", alert.category, error)
                result = FAILED
            with self._cond:
                self._sending = None
                if result == _RETRY:
                    alert.due = self._monotonic() + RETRY_DELAY_SECONDS
                    self._retry.append(alert)
                else:
                    self._active.discard(alert.key)
                self._cond.notify_all()

    def _process(self, alert: _Alert) -> str:
        if alert.attempts == 0:
            config = self._configured()
            if config is None:
                return _DISCARDED
            # 每封只在第一次尝试之前占用一次；重试不再占用、不再判断。
            window = CATEGORIES[alert.category].window_seconds
            if window is not None and not self._limiter.claim(alert.key, window):
                logger.info("ops_alert_suppressed category=%s", alert.category)
                return _DISCARDED
            alert.start(config)
        return _settle(alert, _attempt(alert, self._smtp, self._smtp_ssl))

    def _run(self) -> None:
        while True:
            try:
                self.run_due()
            except Exception:
                pass
            with self._cond:
                delay = self._delay_locked()
                if delay is None:
                    self._cond.wait()
                elif delay > 0:
                    self._cond.wait(delay)


# ---------------------------------------------------------------------------
# 模块入口
# ---------------------------------------------------------------------------

_dispatcher = Dispatcher()


def alert(category: str, target: Target | None = None, *, error_class: str | None = None) -> None:
    """报一条运营告警：不抛异常、不阻塞、不访问数据库与网络。

    category 须是 CATEGORIES 里的类别码（ops_alert_test 除外，它只由 send-test-alert
    发出），否则记 warning ops_alert_unknown_category 后丢弃。target 为
    （对象类别, 内部 ID）或 None；error_class 是写进「对象」一项的异常类名
    （job_failing 与 stock_reset_failed 用）。
    """
    try:
        _dispatcher.enqueue(category, target, error_class=error_class)
    except Exception:
        try:
            logger.warning("ops_alert_dropped category=%s", category)
        except Exception:
            pass


def wait_until_sent(timeout: float = DRAIN_TIMEOUT_SECONDS) -> int:
    """补跑命令退出前调用：等本进程的队列发完（含一次重试），最多 timeout 秒；
    返回未发完的封数（大于 0 时已记 warning ops_alert_unsent）。不抛异常。"""
    try:
        return _dispatcher.wait_until_sent(timeout)
    except Exception:
        return 0


def send_test_alert(
    settings: Settings,
    *,
    smtp: SmtpFactory | None = None,
    smtp_ssl: SmtpFactory | None = None,
    sleep: Callable[[float], object] = time.sleep,
    utc_now: Callable[[], datetime] = _utc_now,
) -> str:
    """在本进程同步发一封 ops_alert_test（对象为无）：不经队列、不受限频，照第 3 条的
    超时与重试。返回 SENT、NOT_CONFIGURED（已记 warning ops_alert_not_configured）
    或 FAILED（已记 error ops_alert_send_failed）。"""
    check = parse_config(settings)
    if check.config is None:
        _log_not_configured(check)
        return NOT_CONFIGURED
    test = _Alert(
        category=TEST_CATEGORY,
        target=None,
        error_class=None,
        occurred_at=utc_now(),
        key=rate_limit_key(TEST_CATEGORY, None),
    )
    test.start(check.config)
    while True:
        result = _settle(test, _attempt(test, smtp, smtp_ssl))
        if result != _RETRY:
            return result
        sleep(RETRY_DELAY_SECONDS)
