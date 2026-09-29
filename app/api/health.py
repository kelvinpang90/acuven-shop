"""Liveness endpoint that also reports which commit is running.

刻意不查数据库或 Redis：它回答的是「进程活着、跑的是哪个提交」，依赖故障不该让容器被判死而反复重启。
路径放在 /api/ 下，边缘反向代理只需一条 /api/ 转发规则就能从外部探到它。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    commit: str


@router.get("/api/healthz", response_model=Health)
def healthz(request: Request) -> Health:
    return Health(status="ok", commit=request.app.state.settings.git_sha)
