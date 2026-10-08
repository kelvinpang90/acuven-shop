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


@lru_cache
def get_settings() -> Settings:
    return Settings()
