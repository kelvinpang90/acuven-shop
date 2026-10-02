"""登记任务的契约检查：新增前端路由的任务必须能改页面表。

依据 CLAUDE.md「登记任务」：frontend/src/App.tsx 的页面表 PAGES 按 frontend/src/router.tsx 的
RoutePath 穷举，路由表加了路径而页面表没加，类型检查不通过。
SHOP-TASK-015 的 run 因允许路径里没有 App.tsx 以 contract_scope_insufficient 失败（2026-10-02），
登记时的模型预审没拦住，故在这里确定性地检查。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

TASKS_FILE = Path(__file__).resolve().parent.parent / ".platform" / "tasks.yaml"
ROUTER = "frontend/src/router.tsx"
APP = "frontend/src/App.tsx"

# 任务文本里新增前台路由的写法：「路由 /cart」「路径为 /products」；/api/ 是后端接口，不算。
ROUTE_MENTION = re.compile(r"(?:路由|路径为)\s*(/[a-z0-9/<>_-]*)")


def _adds_route(task: dict) -> bool:
    text = "\n".join([task["purpose"], *task["acceptance_criteria"]])
    mentioned = [path for path in ROUTE_MENTION.findall(text) if not path.startswith("/api/")]
    return bool(mentioned) or ROUTER in task["allowed_change_paths"]


def _missing_app(task: dict) -> bool:
    return _adds_route(task) and APP not in task["allowed_change_paths"]


def _tasks() -> list[dict]:
    return yaml.safe_load(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]


@pytest.mark.parametrize("task", _tasks(), ids=lambda task: task["id"])
def test_route_tasks_may_change_the_page_table(task: dict) -> None:
    """CLAUDE.md「登记任务」：任务的目的或验收标准里新增路由（或允许改 router.tsx）时，
    allowed_change_paths 必须带 frontend/src/App.tsx，否则实现器无法让页面表穷举新路由。"""
    assert not _missing_app(task), f"{task['id']} adds a route but cannot change {APP}"


def _task(text: str, paths: list[str]) -> dict:
    return {
        "purpose": text,
        "acceptance_criteria": ["改动只落在 allowed_change_paths"],
        "allowed_change_paths": paths,
    }


@pytest.mark.parametrize(
    ("text", "paths"),
    [
        ("路由 /cart；页头显示购物车入口", ["frontend/src/pages/CartPage.tsx"]),
        ("商品列表 P02 路径为 /products", ["frontend/src/pages/ProductListPage.tsx"]),
        ("只改页面", [ROUTER]),
    ],
)
def test_route_task_without_app_is_flagged(text: str, paths: list[str]) -> None:
    """守住检查本身：SHOP-TASK-015 那样写了新路由、没带 App.tsx 的登记必须被拦下。"""
    assert _missing_app(_task(text, paths))


@pytest.mark.parametrize(
    "text",
    [
        "调用 GET /api/orders/lookup 的路由 /api/orders",
        "路由表是唯一来源；按路由规则不渲染",
        "不做页面",
    ],
)
def test_backend_and_rule_mentions_are_not_routes(text: str) -> None:
    """后端接口路径与「按路由规则」这类说法不是新增前台路由，不要求 App.tsx。"""
    assert not _missing_app(_task(text, ["app/api/orders.py"]))
