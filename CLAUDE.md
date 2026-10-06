# acuven_shop — 项目规则

> 本文件与同目录 `AGENTS.md` 是同一份内容的两个副本（分别给 Claude Code / Codex），改一个必须同步另一个。

---

## 项目概况

- **是什么**：Acuven 品牌的公开网店演示站，给有意购买网店方案的潜在客户亲自走一遍完整购物流程，之后自行经 WhatsApp 联系 Acuven。
  商品、金额、库存、支付、运费、发货与退款全是演示，不发生真实交易或履约。
- **技术栈**：前端 TypeScript + React + Vite；后端 Python 3.12 + FastAPI + Pydantic + SQLAlchemy + Alembic；
  数据库用共享 MySQL 里的本项目独立库，Redis 用共享 Redis 里分给本项目的独立库编号（本项目不起数据库容器）；
  Docker Compose 部署、接入现有反向代理，镜像按提交 SHA 标记。
- **需求与设计**：[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 1.5、[docs/DESIGN.md](docs/DESIGN.md) 1.6（金额与个人资料代码的设计依据）；
  批准状态以 [docs/HANDOFF.md](docs/HANDOFF.md) 为准。
- **任务编号**：`SHOP-TASK-NNN`（控制面登记的 task_id_pattern：`^SHOP-TASK-[0-9]{3}$`）
- **任务清单**：[docs/TODO.md](docs/TODO.md)（顶部的 planning-v1 块决定下一项任务）
- **OpenClaw 契约**：[.platform/](.platform/)（project / commands / tasks 三个文件，默认 deny）

## 角色与流程

- **Claude Code 负责实现，Codex 负责独立审查；合并按 PR 来源分（Kelvin 2026-09-30 决定）：**
  Claude Code 开的运营者 PR（登记、收尾、文档与规则）在必需 CI 全绿、Codex 最后一轮结论为「可合并」后由 Claude Code 合并，
  合并后确认 main 上该提交的 CI 与部署成功；OpenClaw Worker 的 PR 仍由 Kelvin 在 Telegram 发「批准」、由 Worker 合并
  （`.platform/project.yaml` 的 `merge_owner: kelvin` 指的就是这一类，不变）。控制面仓库的 PR 按控制面自己的规则。不直推默认分支。
  Worker 的 PR 开着时不合并运营者 PR：main 的必需检查要求分支与 main 同步，main 一前进 Worker 的 PR 就变成 BEHIND，
  而 Worker 合并只接受 CLEAN 并锁定 head SHA（更新分支也会被拒），批准后必然以 `merge_rejected` 结束。运营者 PR 可以先跑 CI 与审查，等 Worker 的 PR 合并后再合并
  （起因：2026-10-04 #88 在 SHOP-TASK-019 的 PR #89 开出 42 秒后合并，#89 变为 BEHIND）。
  自动收尾 PR 正常不会撞上：它在上一个 Worker PR 合并后才开，收尾合并、计划块前进之后才轮到下一项开启；若发现收尾 PR 与另一个 Worker PR 同时开着，立即告诉 Kelvin：收尾 PR 的自动合并由控制面执行，本仓库的规则拦不住，是否暂停、先合哪个由 Kelvin 在控制面决定；运营者不手工合并任何一个。
- 一个编号任务一个分支一个 PR，不夹带；以 Draft PR 交付，CI 全绿后再请求审查。
- **碰钱、碰个人数据的任务先出设计、Kelvin 批准后才写代码**：钱包 / 账本 / 定价 / 支付 / 退款 / 幂等 / 状态机，
  以及收集、存储、展示、导出个人数据（姓名、电话、邮箱、地址、证件等）的改动。设计一变，之前的批准作废。
  纯前端样式、文档、CI、脚本不走闸门。PR 正文写明「设计闸门：」加设计所在的 Issue 或 PR，或「设计闸门：不适用」并给出理由。
- `.platform/` 只由运营者的 PR 与下面的自动收尾 PR 改；OpenClaw 的 run 仍然改不了它。任务完成后由 OpenClaw 自动开收尾 PR，CI 通过后自动合并：
  只含两处机械改动——`tasks.yaml` 里该任务 `ready` 改成 `done`、从 planning-v1「当前计划」移除这一项（其后序号减一），
  不触发部署（`deploy.yml` 的 `paths-ignore`）。只有自动收尾失败（Telegram 收到「自动收尾失败」通知，附原因与建议）时才由人手工开收尾 PR。

## 密钥与敏感信息不进仓库

- 绝不提交凭据、密钥、token、`.env`、证书私钥、客户数据。配置用 `.env.example` 写**变量名与明显的示例值**。
- 不写真实主机名、IP、域名、服务器上的绝对路径、本机路径。部署连接信息放 GitHub secrets，健康检查地址放 repository variables。
- 发现已经提交的密钥：立即告诉 Kelvin 去轮换，不要只删文件——历史里还在。

## 登记任务（.platform/tasks.yaml 与 planning-v1）

- 每个 `ready` 任务都要出现在 [docs/TODO.md](docs/TODO.md) 的 planning-v1 块里；「当前计划」里的每一项都要在 `tasks.yaml` 登记为 `ready`。
  格式见 `docs/TODO.md` 块上方的注释，格式不对整块作废、项目 fail closed。
- `allowed_change_paths` 逐个写精确文件路径（不是目录、不是通配符）；`allowed_commands` 只引用 `.platform/commands.yaml` 里已有的 id。
- **只在 CI 跑的检查不写进 `allowed_commands`**：用 TestClient / asyncio 的后端 pytest（`tests.unit`）和前端检查只在 CI 跑。
  Worker 的 MXC 沙箱里 ssl 等扩展加载失败、回环 socket 被禁，这类测试跑不通；Worker 本来就要等 PR 的必需 CI 检查
  （backend、frontend）全绿才进入「等待批准」，测试照样被强制。任务的 `allowed_commands` 目前只写 `lint.check`、`format.check`；
  验收标准依赖测试通过时，写明由 PR 的必需 CI 检查 `backend`（或 `frontend`）执行。
- **任务的标题、目的与验收标准里不写任何 `xxx://` 形式的地址、主机名、邮箱地址**（也不写 IP、绝对路径）：它们会原样进 PR 正文并过泄漏规则，被拒即 run 失败。
- **接口任务写「所有响应带 no-store」时写明范围**：这些接口的处理函数与依赖产生的响应（含错误）；路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料，不在此列。
  同理，现有代码已有落实写法的要求写明照哪个写：422 不回显沿用 `app/api/pay.py` 的 `_body_errors`、`_language`；请求指纹与令牌摘要沿用 `request_fingerprint_length`、`token_hash_length`，只约束长度 64，十六进制由服务层 `hexdigest` 保证，数据库层不另加十六进制约束。
  起因：SHOP-TASK-027 的 run babdccb4 以 `review_repair_exhausted` 失败（2026-10-05），三轮评审中的两轮都卡在这几处没写明的地方（405 缺 no-store、默认 422 带出 input、指纹未约束十六进制）。
- **前端样式文件（Kelvin 2026-10-01 决定）**：`frontend/src/styles/acuven-shop.css` 是生成文件，只读，必须和 `docs/design/tokens/acuven-shop.css` 逐字节相同；
  框架和页面的布局、显隐、媒体查询统一写在 `frontend/src/styles/site.css`，由 `frontend/src/main.tsx` 引入；不声明颜色、字体、圆角属性（由设计系统的类提供，用变量也不行），间距与尺寸照视觉稿直接写像素值（`acuven-shop.css` 没有间距与尺寸变量）。
  登记任务时验收标准照这个写法写，不写「只用设计变量」：SHOP-TASK-038 的 run 因评审把它读成不许写像素间距，以 `review_repair_exhausted` 失败（2026-10-06）。
  每个有用户可见页面的前端任务，`allowed_change_paths` 固定带 `frontend/src/styles/site.css`，和 `frontend/src/router.tsx`、`frontend/src/i18n/copy.ts` 一样。
- **新增路由的前端任务带上页面表**：`frontend/src/App.tsx` 的页面表按 `router.tsx` 的路由表穷举，路由表加了路径而页面表没加，类型检查不通过；
  所以目的或验收标准里新增路由（「路由 /…」「路径为 /…」）或允许改 `frontend/src/router.tsx` 的任务，`allowed_change_paths` 必须带 `frontend/src/App.tsx`，
  由 `tests/test_platform_tasks.py`（CI 的 backend）检查。会让 `frontend/src/App.test.tsx` 现有断言失效的任务（改首页内容、在每个页面的框架里加入带变量的文字等）另带它，
  并在验收标准里写明只为此改；新路由被「只出现字典文字」这类按路由表遍历的测试自动覆盖，本身不是带它的理由。
  起因：SHOP-TASK-015 的 run 因缺这两个路径以 `contract_scope_insufficient` 失败（2026-10-02），登记时的模型预审没有发现。
- **按条件预审（Kelvin 2026-10-01 决定）**：任务属于设计闸门类（碰钱：钱包、账本、定价、支付、退款、幂等；碰个人数据；状态机；
  改数据库结构或迁移），或是有用户可见页面的前端任务（`--design` 指向 `docs/UX.md` 或对应的 `docs/design/pages/` 页面），或 `contract_check` 对它给出拆分 WARN、预计改动接近约 1500 行（运营者按设计粗估，拿不准就当作接近）时，登记为 `ready` 之前必须在控制面仓库运行
  `python -m worker.design_precheck --repo <本仓库干净检出或 git archive 导出> --project-id acuven_shop --task <任务 id> --design <设计文件相对路径> --claude <claude 程序绝对路径>`，
  把完整输出贴进登记 PR；`NOT_READY` 的发现要么修掉，要么在 PR 里逐条写明为什么不成立。它调用模型、不是确定性的，`READY` 不保证没有缺陷。
  不满足条件的任务（例如简单增删改查）在登记 PR 里写一行「预审：不适用」并给出理由。一次预审约 3 分钟、一次只读会话的额度。
  前端页面任务这一条是 Kelvin 2026-10-01 的决定，起因是 SHOP-TASK-014 以 `contract_scope_insufficient` 失败（唯一可写的样式文件是生成文件，布局媒体查询无处可写）。
- **页面任务开启前做骨架实测（Kelvin 2026-10-04 决定）**：有用户可见页面的前端任务（新增路由、改页头页脚或其他每页都出现的内容），
  在 Telegram 发「开启」之前，由运营者在当时 main 的临时导出里实测一次：按契约加上路由与页面表、只渲染初始状态（接口尚未返回、没有本浏览器存储）的空页面、
  契约要求的页头页脚入口与字典键，然后跑前端类型检查、lint 与全部测试。改动只留在临时目录，不提交。
  失败的测试逐个对照契约：文件不在 `allowed_change_paths` 里、或契约没写明可以怎样改的，先开运营者 PR 修契约（PR 里贴失败表与结论），合并后再开启；
  契约已允许的失败（例如路由表断言）在 PR 或回复里写明即可。它是确定性的，补模型预审的不足；放在开启前而不是登记时，是因为前面的任务合并后测试会变。
  纯后端任务不做，开启前只核对迁移的 revision 与 down_revision 仍接在当前 main 的最新迁移之后。
  起因：SHOP-TASK-017 的 run b223df69 以 `DESIGN_DEFECT` 停下（`SiteFrame.test.tsx` 按路由表遍历、不在允许路径里），018 开启前的实测又在 `ProductDetailPage.test.tsx` 发现同类冲突（2026-10-03）。
- **拆分规则**：一个任务只做一层（数据库 / 业务规则 / 接口 / 前端，用 `depends_on` 串起来，每层合并后都能单独通过检查与 CI）；
  规则多的设计先交付规则和与设计例子对应的测试，再接接口；`allowed_change_paths` 超过 12 个、验收标准超过 8 条、或预计改动超过约 1500 行（这一条只有预审能估计，以它报的 `TOO_LARGE` 为准）先拆，
  拆不开要写明原因；预审报出的 `SCOPE_GAP` 先改契约，不留到实现时。
  （预审与拆分的完整说明在控制面仓库 `docs/ONBOARD-PROJECT.md`「登记任务之前」一节。）

```text
登记任务时的浏览器验收约定（OpenClaw 浏览器验收契约）：
- 有用户可见页面的前端任务，登记时默认带 acceptance 块：沿用项目已有的验收命令，writes: false，按页面写 2–4 个只读步骤
  （小写下划线步骤名），timeout_seconds 不超过 300；验收命令不写进 allowed_commands；allowed_change_paths 加上验收脚本。
- 验收标准里写清每个新步骤验什么，通用约束一句「沿用验收脚本已有的约束」带过；脚本只执行 stdin 里 steps 列出的步骤。
- 纯后端、数据库、文档任务不带；运营者说「这个不验」时不带。
- 任务的标题、目的与验收标准会原样进 PR 正文并过泄漏规则：不写任何 xxx:// 形式的地址、主机名、邮箱地址。
```

（上面这段只在项目启用了浏览器验收——`.platform/project.yaml` 有 `acceptance_hosts`、`commands.yaml` 有验收命令——之后才生效；
在那之前任务不带 `acceptance` 块，最后一条照样适用。）

## 高风险操作——失败一次即停

SSH / 远程登录、数据库连接与密码、任何认证连接测试、生产环境的删除 / 重启 / 清空、防火墙与安全组规则：
执行一次失败后停止并汇报现象，等 Kelvin 下一步指示，不换参数连续重试。诊断默认只读。

---

## 12 条规则

以下规则适用于本项目中的所有任务，除非被明确覆盖。
基本原则：在非平凡的工作上，谨慎优先于速度。简单任务自行判断。

### 规则一——先想清楚再写代码
把假设显式地说出来。拿不准就问，别猜。
存在歧义时，列出多种可能的理解方式。
如果有更简单的方案，大胆提出来。
搞不明白就停下来，说清楚哪里不明白。

### 规则二——简洁至上
用最少的代码解决问题。不写投机性代码。
不做超出要求的功能。只用一次的代码不搞抽象。
检验标准：资深工程师会不会说"搞复杂了"？如果会，简化。

### 规则三——精准手术式修改
只动必须动的地方。只清理自己造成的混乱。
不要"顺手优化"旁边的代码、注释或格式。
没坏的不要重构。遵循现有风格。

### 规则四——以目标驱动执行
定义成功标准。反复迭代直到验证通过。
不要按步骤执行，而是定义成功的样子，然后自己迭代。
清晰的成功标准能让你独立完成闭环。

### 规则五——只在需要判断力的地方使用模型
适合我做的：分类、起草文本、摘要、信息提取。
不该让我做的：路由、重试、确定性转换。
代码能回答的问题，就让代码来回答。

### 规则六——Token预算不是"建议"
单任务上限：4,000 tokens。单会话上限：30,000 tokens。
接近上限时，做一次总结，重新开始。
把超支暴露出来，不要悄悄超标。

### 规则七——遇到冲突要挑明，不要折中调和
如果两种模式互相矛盾，选一个（更新的/测试更充分的）。
说明理由，把另一个标记为待清理项。
不要把冲突的模式揉在一起。

### 规则八——先读再写
添加代码之前，先读懂导出接口、直接调用方和公共工具函数。
"看起来没关系"是最危险的判断。如果搞不懂代码为何如此组织，先问。

### 规则九——测试要验证意图，而不仅仅是行为
测试必须表达行为为什么重要，而不仅仅是它做了什么。
如果一条测试在业务逻辑变更时不会失败，那它就是错的。

### 规则十——每完成一个关键步骤，做一次检查点
总结做了什么、验证了什么、还剩什么。
如果你无法向我描述清楚当前状态，就不要继续。
如果你自己都搞不清了，停下来，重新梳理。

### 规则十一——遵循代码库的现有惯例，即使你不认同
在代码库内部，一致性 > 个人品味。
如果你真心认为某个惯例有害，提出来。但不要悄悄另立门户。

### 规则十二——大声报错
如果有任何东西被悄悄跳过，就不要说"已完成"。
如果跳过了任何测试，就不要说"测试通过"。
默认暴露不确定性，而不是掩盖它。
