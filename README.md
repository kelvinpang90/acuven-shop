# Acuven Shop

Acuven 品牌的公开网店演示站：访客亲自走一遍浏览、下单、模拟支付、查单与退款申请的完整流程。
所有商品、金额、支付、发货与退款均为演示，不发生真实交易或履约。

- 需求：[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)；金额与个人资料设计：[docs/DESIGN.md](docs/DESIGN.md)；批准状态：[docs/HANDOFF.md](docs/HANDOFF.md)
- 规则：[CLAUDE.md](CLAUDE.md)（与 [AGENTS.md](AGENTS.md) 同一份内容）；任务：[docs/TODO.md](docs/TODO.md)；OpenClaw 契约：[.platform/](.platform/)
- 本项目由 Acuven 项目模板生成，生成步骤见 [TEMPLATE-GUIDE.md](TEMPLATE-GUIDE.md)。

## 目录

| 路径 | 内容 |
|------|------|
| `app/` | FastAPI 后端（目前只有健康检查 `/api/healthz`，返回线上版本的 commit SHA） |
| `alembic/` | 数据库迁移（目前只有空基线，没有业务表） |
| `tests/` | 后端测试（pytest，不需要数据库或网络） |
| `frontend/` | React + Vite 前端（目前只有最小外壳页） |

## 本地检查

后端（Python 3.12）：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -I -m pytest
.venv/Scripts/python -I -m ruff check .
.venv/Scripts/python -I -m ruff format --check .
```

前端（Node 24）：

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```
