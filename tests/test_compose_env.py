"""docker-compose.yml 把短信、人机挑战与联系链接的配置传进 shop_api（SHOP-TASK-143），以及商品图片的
R2 配置与 API 内存上限（DESIGN 1.19），以及运营告警邮件的七项配置与 shop_jobs 的 SHOP_REDIS_URL
（DESIGN 1.21「运营告警邮件」第 1、4 条，SHOP-TASK-217）。

写法照 tests/test_stripe_gateway.py 的 test_compose_passes_all_six_variables_to_api_and_jobs：
读取 docker-compose.yml，逐字比较 environment 里的写法。每条测试的文档字符串写明它守住的是什么。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"

# 前八项未设置时为空串（未配置）；后八项未设置时取 app/core/config.py 的默认值。
EXPECTED = {
    "SHOP_TWILIO_ACCOUNT_SID": "${SHOP_TWILIO_ACCOUNT_SID:-}",
    "SHOP_TWILIO_AUTH_TOKEN": "${SHOP_TWILIO_AUTH_TOKEN:-}",
    "SHOP_TWILIO_VERIFY_SERVICE_SID": "${SHOP_TWILIO_VERIFY_SERVICE_SID:-}",
    "SHOP_TWILIO_VERIFY_BASE_URL": "${SHOP_TWILIO_VERIFY_BASE_URL:-}",
    "SHOP_TURNSTILE_SECRET_KEY": "${SHOP_TURNSTILE_SECRET_KEY:-}",
    "SHOP_TURNSTILE_VERIFY_URL": "${SHOP_TURNSTILE_VERIFY_URL:-}",
    "SHOP_TURNSTILE_SITE_KEY": "${SHOP_TURNSTILE_SITE_KEY:-}",
    "SHOP_WHATSAPP_CONTACT_URL": "${SHOP_WHATSAPP_CONTACT_URL:-}",
    "SHOP_SMS_DAILY_COUNT_LIMIT": "${SHOP_SMS_DAILY_COUNT_LIMIT:-200}",
    "SHOP_SMS_DAILY_COST_LIMIT_MICRO_USD": "${SHOP_SMS_DAILY_COST_LIMIT_MICRO_USD:-20000000}",
    "SHOP_SMS_MAX_COST_MICRO_USD_MY": "${SHOP_SMS_MAX_COST_MICRO_USD_MY:-0}",
    "SHOP_SMS_MAX_COST_MICRO_USD_SG": "${SHOP_SMS_MAX_COST_MICRO_USD_SG:-0}",
    "SHOP_SMS_PHONE_LIMIT_PER_MINUTE": "${SHOP_SMS_PHONE_LIMIT_PER_MINUTE:-1}",
    "SHOP_SMS_PHONE_LIMIT_PER_HOUR": "${SHOP_SMS_PHONE_LIMIT_PER_HOUR:-5}",
    "SHOP_SMS_PHONE_LIMIT_PER_DAY": "${SHOP_SMS_PHONE_LIMIT_PER_DAY:-10}",
    "SHOP_SMS_SOURCE_LIMIT_PER_HOUR": "${SHOP_SMS_SOURCE_LIMIT_PER_HOUR:-10}",
}


def _environment(service: str) -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"][service]["environment"]


def test_compose_passes_all_sixteen_variables_to_api() -> None:
    """守住验收第 1 条「shop_api.environment 加入这 16 项，写法为 ${变量名:-} 或
    ${变量名:-默认值}」：每项都在、写法逐字相同，未设置时照常部署。"""
    environment = _environment("shop_api")
    for name, value in EXPECTED.items():
        assert environment.get(name) == value, name


def test_compose_defaults_match_settings_defaults() -> None:
    """守住验收第 1 条「默认值与 app/core/config.py 的默认值相同」：compose 里 :- 之后的默认值
    就是 Settings 对应字段的默认值，以后只改了一边时失败。"""
    fields = Settings.model_fields
    for name, value in EXPECTED.items():
        field = name.removeprefix("SHOP_").lower()
        default = value.split(":-", 1)[1].removesuffix("}")
        assert default == str(fields[field].default), name


def test_compose_does_not_pass_them_to_jobs() -> None:
    """守住验收第 1 条「shop_jobs 不加（定时任务不发短信）」：这 16 项都不在 shop_jobs.environment
    里，定时任务容器拿不到短信与人机挑战的凭据。"""
    environment = _environment("shop_jobs")
    for name in EXPECTED:
        assert name not in environment, name


def test_every_setting_is_passed_to_api() -> None:
    """守住验收第 5 条「Settings 每个字段（git_sha 除外）对应的 SHOP_ 变量都出现在
    shop_api.environment 里」：以后在 app/core/config.py 新增配置却忘了传进容器时失败。
    git_sha 在镜像构建时写入，不经 compose 传入。四个 r2_ 字段以原名读取（不带 SHOP_ 前缀），
    由 test_compose_passes_r2_variables_only_to_api 守住。"""
    environment = _environment("shop_api")
    r2_fields = {"r2_endpoint_url", "r2_bucket", "r2_access_key_id", "r2_secret_access_key"}
    missing = [
        f"SHOP_{field.upper()}"
        for field in Settings.model_fields
        if field != "git_sha"
        and field not in r2_fields
        and f"SHOP_{field.upper()}" not in environment
    ]
    assert missing == []


R2_EXPECTED = {
    "R2_ENDPOINT_URL": "${R2_ENDPOINT_URL:-}",
    "R2_BUCKET": "${R2_BUCKET:-}",
    "R2_ACCESS_KEY_ID": "${R2_ACCESS_KEY_ID:-}",
    "R2_SECRET_ACCESS_KEY": "${R2_SECRET_ACCESS_KEY:-}",
}


def test_compose_passes_r2_variables_only_to_api() -> None:
    """DESIGN 1.19 第 13 条「连接端点、存储桶名、访问密钥 ID 与访问密钥都由私有配置注入」
    「缺任一项或为空字符串即视为存储不可用」：四项照设计原名传给 shop_api，未设置时为空串、
    照常部署；定时任务不碰图片，不传给 shop_jobs。"""
    environment = _environment("shop_api")
    for name, value in R2_EXPECTED.items():
        assert environment.get(name) == value, name
    jobs = _environment("shop_jobs")
    assert not any(name.startswith("R2_") for name in jobs)


ALERT_EXPECTED = {
    "SHOP_ALERT_SMTP_HOST": "${SHOP_ALERT_SMTP_HOST:-}",
    "SHOP_ALERT_SMTP_PORT": "${SHOP_ALERT_SMTP_PORT:-}",
    "SHOP_ALERT_SMTP_SECURITY": "${SHOP_ALERT_SMTP_SECURITY:-}",
    "SHOP_ALERT_SMTP_USERNAME": "${SHOP_ALERT_SMTP_USERNAME:-}",
    "SHOP_ALERT_SMTP_PASSWORD": "${SHOP_ALERT_SMTP_PASSWORD:-}",
    "SHOP_ALERT_FROM": "${SHOP_ALERT_FROM:-}",
    "SHOP_ALERT_TO": "${SHOP_ALERT_TO:-}",
}


def test_compose_passes_alert_variables_to_api_and_jobs() -> None:
    """DESIGN 1.21「运营告警邮件」第 1 条「配置项经私有配置注入 API 与定时任务两个容器，仓库只记
    变量名」：七个 SHOP_ALERT_ 变量在 shop_api 与 shop_jobs 里都在，写法为 ${变量名:-}（照 Stripe
    变量），未设置时为空串（未配置，告警只记日志）、照常部署。"""
    for service in ("shop_api", "shop_jobs"):
        environment = _environment(service)
        for name, value in ALERT_EXPECTED.items():
            assert environment.get(name) == value, (service, name)


def test_alert_settings_default_to_empty_strings() -> None:
    """DESIGN 1.21「运营告警邮件」第 1 条与 HANDOFF 0.88「七项配置在 Settings 里都是字符串、默认
    空串」：compose 里的空默认值与 Settings 的默认值一致，端口与加密方式也是字符串，不合格时由
    ops_alert 判断、不让设置加载失败。"""
    fields = Settings.model_fields
    for name in ALERT_EXPECTED:
        field = name.removeprefix("SHOP_").lower()
        assert fields[field].annotation is str, name
        assert fields[field].default == "", name
    settings = Settings(_env_file=None, alert_smtp_port="not-a-port", alert_smtp_security="tls")
    assert settings.alert_smtp_port == "not-a-port"
    assert settings.alert_smtp_security == "tls"


def test_compose_passes_redis_url_to_jobs() -> None:
    """DESIGN 1.21「运营告警邮件」第 4 条「计数放共享 Redis 本项目的库编号，API 与定时任务共用」：
    shop_jobs 也传 SHOP_REDIS_URL，写法与 shop_api 相同（未设置时为空串，改用进程内存限频）。"""
    api = _environment("shop_api")
    jobs = _environment("shop_jobs")
    assert jobs.get("SHOP_REDIS_URL") == "${SHOP_REDIS_URL:-}"
    assert jobs["SHOP_REDIS_URL"] == api["SHOP_REDIS_URL"]


def test_api_memory_limit_is_512m() -> None:
    """DESIGN 1.19「上线依赖与设计闸门」：本站 API 容器的内存上限提到 512m（Pillow 处理图片，另含
    boto3 的常驻内存）。"""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert compose["services"]["shop_api"]["mem_limit"] == "512m"
