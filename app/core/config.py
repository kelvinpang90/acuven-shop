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


@lru_cache
def get_settings() -> Settings:
    return Settings()
