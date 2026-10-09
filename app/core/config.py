"""Application settings, read from environment variables (prefix SHOP_).

默认值里不许出现任何真实主机名或凭据。本地模板见 .env.example。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHOP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 镜像构建时由 Dockerfile 的 GIT_SHA 构建参数写进 SHOP_GIT_SHA。部署工作流靠健康检查
    # 返回的这个值确认线上跑的就是刚部署的提交；没注入时是 unknown，那样部署检查必然失败。
    git_sha: str = "unknown"

    # 空串 = 未配置。只有 alembic 用到；应用本身还没有任何表。
    database_url: str = ""

    # 空串 = 未配置。共享 Redis 里分给本项目的独立库（末尾数字是库编号），存短时限流计数；
    # 未配置或连不上时依赖限流的接口拒绝请求（app/services/rate_limit.py）。
    redis_url: str = ""

    # 短信验证（Twilio Verify）。空串 = 未配置；四项缺一或接口地址不是 https:// 时发送与核验
    # 一律归为 unavailable、不发请求（app/services/sms_provider.py）。接口地址只写到主机，
    # 如 https://VERIFY_API_HOST，路径由适配器拼接。
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_verify_service_sid: str = ""
    twilio_verify_base_url: str = ""

    # 发送前的人机挑战（Cloudflare Turnstile，Kelvin 2026-10-08 决定）。空串 = 未配置；缺一或
    # 核验地址不是 https:// 时核验一律归为 unavailable、不发请求（app/services/captcha.py）。
    # 核验地址是完整的网址，含路径。
    turnstile_secret_key: str = ""
    turnstile_verify_url: str = ""

    # 短信每日总量与费用预算的 MySQL 兜底（app/services/sms_budget.py）。
    # 默认值按 Kelvin 2026-10-08 的决定：全站每天 200 条、费用上限 20 美元。
    # 金额一律是整数微美元（百万分之一美元）。
    sms_daily_count_limit: int = 200
    sms_daily_cost_limit_micro_usd: int = 20_000_000
    # 按目的地（马来西亚 60、新加坡 65）的单次最高费用，发送前按它预占、按它结算。
    # 0 = 未配置：该目的地停发。
    sms_max_cost_micro_usd_my: int = 0
    sms_max_cost_micro_usd_sg: int = 0

    # 短信发送的 Redis 限流（app/services/sms_verification.py），默认值按 Kelvin 2026-10-08 的
    # 标准档：每个号码 60 秒 1 条、1 小时 5 条、24 小时 10 条，每个来源 1 小时 10 条。
    # 每个国家呼叫码 24 小时的上限直接取 sms_daily_count_limit，不另设配置项。
    sms_phone_limit_per_minute: int = 1
    sms_phone_limit_per_hour: int = 5
    sms_phone_limit_per_day: int = 10
    sms_source_limit_per_hour: int = 10

    # WhatsApp 联系链接（https 开头的完整链接）。空串 = 未配置：GET /api/site-settings 返回 null，
    # 前台隐藏联系入口（UX Q10）。不合格的值同样按未配置处理（app/api/site_settings.py）。
    whatsapp_contact_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
