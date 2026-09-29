# Acuven 项目模板

新项目的起点。用它起步的项目，生成出来就是 OpenClaw（控制面：`acuven-openclaw-control-plane`）能直接接手的结构：
`.platform/` 三个契约文件、`docs/TODO.md` 的 planning-v1 计划块、满足部署观察契约 D1–D5 的部署工作流，
以及写好规则的 `CLAUDE.md` / `AGENTS.md`。

**这一轮只有核心**（Kelvin 2026-09-28：「核心先做，模块后沉淀」）。前端 / 后端 / 电商等模块不在这里——
等第二个项目跑通后，再从两个真实项目里提炼。模板里没有任何业务代码，也不替你选技术栈。

## 里面有什么

| 路径 | 作用 |
|------|------|
| [TEMPLATE-GUIDE.md](TEMPLATE-GUIDE.md) | 新项目会话照着走的步骤：需求与设计闸门 → 技术栈 → 骨架 → CI / 部署 → 契约与计划 → 推送 → 自检 → 登记 |
| [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) | 项目规则骨架（同一份内容两个副本）：12 条规则、密钥不进仓库、设计闸门、登记任务与浏览器验收约定 |
| [.platform/project.yaml](.platform/project.yaml) | 项目身份、业务仓库对 Worker 调度的同意、部署工作流声明 |
| [.platform/commands.yaml](.platform/commands.yaml) | Worker 可执行命令的 allowlist（起步为空） |
| [.platform/tasks.yaml](.platform/tasks.yaml) | 可调度任务的 allowlist（起步为空） |
| [docs/TODO.md](docs/TODO.md) | 任务清单与空的 planning-v1 计划块 |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | CI 骨架（检查步骤留占位） |
| [.github/workflows/deploy.yml](.github/workflows/deploy.yml) | 满足 D1–D5 的部署骨架（部署命令留占位） |

占位符一律写成 `<像这样>`；生成的项目里不应再剩任何一个。占位的 CI 与部署步骤**故意以失败结束**，
不会出现「空工作流一路绿」的假象。

## 怎么用

1. **拿到一份副本**，二选一：
   - 复制本目录到工作区根目录下的 `<新项目目录>`（与本模板同级，不带 `.git`）；
   - 或把本仓库在 GitHub 上设为 template repository，用「Use this template」建新仓库再克隆下来。
2. **在新项目目录开一个 Claude Code 会话**，对它说：「按 TEMPLATE-GUIDE.md 把这个项目生成出来」，并给出需求。
3. 会话照 [TEMPLATE-GUIDE.md](TEMPLATE-GUIDE.md) 一步步走；碰钱、碰个人数据的部分会停在设计闸门等 Kelvin 批准。
4. 会话最后在控制面检出里跑 `python -m worker.contract_check`，没有 FAIL 才交接；登记表那一步回控制面会话做。

## 维护这个模板

- 模板只放**通用**内容。某个项目特有的东西（业务、命令参数、任务）不进模板。
- 契约格式以控制面为准：`acuven-openclaw-control-plane` 的 `docs/ONBOARD-PROJECT.md` 与 `worker/contracts.py`、
  `worker/planning.py`。控制面改了格式，这里跟着改，并重新跑一遍「验证模板」。
- 不写真实主机名、IP、域名、密钥、绝对机器路径。
- 本目录里的 `CLAUDE.md` / `AGENTS.md` 是给**生成出来的项目**用的骨架；维护模板本身时以本 README 为准。

### 验证模板

把模板实例化到临时目录、填一个假的 `project_id` 与一两个示例任务，在控制面的干净检出里跑：

```bash
python -m worker.contract_check --repo <临时目录> --project-id <假 id> --task-id-pattern '<规则>'
```

结论为 `result: ready to register` 即模板仍然合格（WARN 是提示，不阻断）。
