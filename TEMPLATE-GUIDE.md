# TEMPLATE-GUIDE — 新项目会话的步骤

> 给在新项目目录里工作的 Claude Code 会话。按顺序走，每一步做完做一次检查点（做了什么、验证了什么、还剩什么）。
> 标「停」的地方必须等 Kelvin 回复才往下走。
> 契约格式的权威来源是控制面仓库的 `docs/ONBOARD-PROJECT.md`；本文件与它冲突时以它为准，并把冲突告诉 Kelvin。

---

## 0. 定身份

- **project_id**：`^[a-z][a-z0-9_]{1,30}$`，在控制面登记表里全表唯一。运营者要在 Telegram 里手打它，短一点好。
- **任务编号规则 task_id_pattern**：必须以 `^` 开头、`$` 结尾，只描述本项目自己的编号，例如 `^<PREFIX>-TASK-[0-9]{3}$`。
  不要为了「统一」套别的项目的格式。
- **停**：把两者报给 Kelvin 确认。此时还**不写**控制面登记表。

## 1. 需求分析与设计闸门

- 把需求写成 `docs/` 下的一份需求说明：给谁用、核心流程、明确不做什么、上线标准。歧义列出多种理解，不脑补。
- 找出**碰钱**（钱包、账本、定价、支付、退款、幂等、状态机）与**碰个人数据**（收集、存储、展示、导出姓名 / 电话 / 邮箱 /
  地址 / 证件等）的部分。这些部分**先出设计**：数据模型、状态迁移、失败与重试、权限、数据保留。
- **停**：设计交 Kelvin 批准后，这些部分才写代码。设计一变，之前的批准作废。其余部分不必等。

## 2. 选技术栈

- 按需求选语言、框架、数据库、部署方式。优先沿用 Acuven 已有项目用过的组合，除非有明确理由不用；理由写进需求说明。
- 同时想清楚两件事，它们决定第 5 步能登记什么：
  - 哪些检查能在 **Worker 的 MXC 沙箱**里跑：无网络、无数据库 / 缓存服务、无 Git、环境变量清空。Python 的单元测试、lint 通常可以。
  - **node 检查（vitest、tsc、eslint 等）不能直接进沙箱**：只有控制面代码里的闭集条目授权，新增要改控制面代码、受限评审、
    Kelvin 批准。起步时前端检查**只放 CI**。
- **停**：技术栈报 Kelvin 确认。

## 3. 生成骨架

- 按技术栈生成最小可运行的骨架：入口、一条健康检查端点（见第 4 步的 D3）、一个真实的测试、`.gitignore`、`.env.example`（只写变量名与明显的示例值）。
- 填 `CLAUDE.md` 的「项目概况」，删掉头部那行模板说明，然后**把 `CLAUDE.md` 原样复制成 `AGENTS.md`**（两份始终一致）。
- 不写投机性代码，不做需求外的功能；模块留到对应任务里做。

## 4. CI 与部署工作流

- `.github/workflows/ci.yml`：把占位步骤换成真实检查。`.platform/commands.yaml` 里将要登记的每条检查都要在 CI 里跑，
  沙箱跑不了的（前端、要服务的测试、依赖 Git 的）只放 CI。job 名与 `project.yaml` 的 `required_status_checks` 一致。
- `.github/workflows/deploy.yml`：把占位部署步骤换成真实部署，逐条确认部署观察契约：
  - [ ] 删掉 `ci.yml` 与 `deploy.yml` 里 `if: ${{ !github.event.repository.is_template }}` 那一行（只给模板仓库自己用）
  - [ ] **D1** 由 `push` 到默认分支触发（`workflow_dispatch` 可保留，不计入观察）
  - [ ] **D2** 部署的就是 `DEPLOY_SHA`（触发它的提交），不是 `git pull` 拿到的分支最新
  - [ ] **D3** 只有新版本上线且上线后健康检查通过才 `success`；健康检查失败、回滚（无论成败）、中断都以失败结束；没有 `continue-on-error` / `|| true`
  - [ ] **D4** `concurrency.cancel-in-progress: false`
  - [ ] **D5** 30 分钟内出结论（`timeout-minutes` 不超过 25；不加需要人工批准的 environment）
- 健康检查默认要求端点返回的内容里带线上版本的 commit SHA；骨架的健康端点要能做到（例如从构建参数注入 SHA）。
- 连接信息（主机、用户、密钥、指纹）放 GitHub secrets，健康检查地址放 repository variable `HEALTHCHECK_URL`。
  这些由 Kelvin 在 GitHub 上配，**会话不接触任何凭据**；把需要的 secret / variable 名列给 Kelvin。

## 5. 写 `.platform/` 与 planning-v1

- 三个文件把 `<project_id>` 换成第 0 步定的值。`project.yaml` 的 `execution.worker_enabled: true` 与
  `execution_worker: windows-native` 是**本仓库同意被调度**，不要改；`deploy_workflow: deploy.yml` 保留（按规划执行的项目必须有）。
- `commands.yaml`：只登记沙箱里跑得动、CI 里也在跑的检查。字段规则见文件注释。**不登记 node 检查。**
- `tasks.yaml`：登记第一批任务（通常一到三个），字段规则见文件注释。要点：
  - `allowed_change_paths` 逐个写精确文件路径，不能写 `.platform/` 下的文件；
  - `allowed_commands` 非空，只引用 `commands.yaml` 里的 id；
  - **标题、目的、验收标准里不写 `xxx://` 地址、主机名、邮箱地址**（也不写 IP、绝对路径）。
- `docs/TODO.md` 的 planning-v1 块：每个 `ready` 任务都放进块里；第一个要做的放「当前计划」第 1 项；尚未登记的想法写成
  `- 待登记：…` 放「后续计划」。格式见块上方注释。
- 搜一遍 `<`，确认没有剩下任何占位符。

## 6. 提交并推到默认分支

- **停**：git 仓库怎么建、GitHub 远端放在哪个账号 / 叫什么名、公开还是私有，问 Kelvin。不要自己建远端。
- 推到默认分支之后，请 Kelvin 在 GitHub 上配分支保护（禁直推、禁 force push、必需检查 = CI 的 job 名）与第 4 步的 secrets / variables。
- 推送后看一眼 CI 与部署工作流的第一次运行：部署在 secrets 配好之前失败是预期的，报告现象即可，不要反复重跑。

## 7. 交接前自检

在**控制面仓库**（`acuven-openclaw-control-plane`）的检出里，对本项目**干净的默认分支检出**跑
（Worker 读的是提交里的内容；工作区里未提交、被忽略或只差大小写的文件在这里能过、在 Worker 那里不能）：

```bash
python -m worker.contract_check --repo <本项目干净检出> --project-id <project_id> --task-id-pattern '<task_id_pattern>'
```

计划文件不是 `docs/TODO.md` 时加 `--planning <相对路径>`。

- 结论必须是 `result: ready to register`，**有任何 FAIL 都不交接**：回到对应步骤修，重新提交推送，再跑。
- WARN 不阻断，但要读懂：`executables` 是 Worker 主机要固定的程序；`node <id>` 表示那条检查要控制面授权，起步时应移出 `commands.yaml`；
  非 `ready` 任务的加载问题只是提醒。
- 通过只表示「可以登记」，不表示「可以跑」：Worker 主机侧（固定程序、GitHub 权限、工作流是否启用）由登记后的就绪上报检查。

## 8. 回控制面会话登记

登记**不在本项目会话里做**，也不能从聊天里做。把下面这些交给控制面会话（在控制面目录里另开会话），由它按
`docs/ONBOARD-PROJECT.md` 改登记表、渲染、自查、提 PR，经独立受限评审、Kelvin 合并：

- `project_id`、`display_name`、`task_id_pattern`
- `planning_source`：`{"path": "docs/TODO.md", "format": "planning-v1"}`，同时 `proposals: false`
- **`worker: none`**（新项目一律如此起步；改成 `windows-native` 是之后单独复审的任务）
- `status: pilot`
- 第 7 步 `contract_check` 的完整输出

最后按工作区根目录 `CLAUDE.md`（项目总览）的要求，把新目录登记进项目总览（`CLAUDE.md` 与 `AGENTS.md` 两份同步）。
