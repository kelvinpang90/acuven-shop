# TODO — 开发任务清单

> 最后更新：2026-10-24（登记会员前端第二批）

---

## 本文件的约定

1. 一个 session 做一个编号任务：读本文件 → 做完 → 验收 → commit，中途不需要逐步审批。
2. **对计划本身有异议时才停下来讨论**，不要在每个任务上重新走一遍「先计划、等确认」。
3. 每个编号任务的记录写在自己的文件 `docs/task-records/<任务 id>.md`（如 `docs/task-records/SHOP-TASK-118.md`），**不写进本文件**：第一行 `# <任务 id> <标题>`，下面写做了什么、偏离了什么、验证到什么程度，做完的复选框写 `[x]`。复选框只允许 `[ ]` / `[x]`。
   本文件由 Worker 按固定上限读取（控制面子进程输出上限 1,000,000 字节，超过即计划无效、自动收尾失败），所以这里只放约定、planning-v1 块与下面的索引；`tests/test_platform_tasks.py` 在 CI 里守住本文件与 `.platform/tasks.yaml` 的大小。
4. 本文件里**没有**的工作（临时需求、探索型任务）才需要先写计划、等确认。

---

## 规划（OpenClaw planning-v1）

> 下面两个标记之间的块由 OpenClaw 控制面按固定格式解析，决定下一项任务；只管「做什么、先做什么」，执行权限仍以 `.platform/tasks.yaml` 为准。格式不对整块作废，项目 fail closed。

<!-- 块内每一行只能是下面三种格式之一（空行可以有，别的内容一律不行，包括注释）：
     当前计划：`1. `<任务 id>` <标题>`，从 1 连续编号，最多 20 项；每一项都必须在 tasks.yaml 登记为 ready，第一项就是下一次「开启」的任务。
     已阻塞：  `- `<任务 id>` <标题>｜阻塞：<原因>` 或 `- 待登记：<标题>｜阻塞：<原因>`（分隔符是全角竖线）。
     后续计划：`- `<任务 id>` <标题>` 或 `- 待登记：<标题>`。
     任务 id 必须符合控制面登记的 task_id_pattern，全块不重复；tasks.yaml 里每个 ready 任务都必须出现在块里。
     「待登记」行不带 id，只给人看，不会变成可执行的东西。三个标题与两个标记逐字不改。 -->

<!-- openclaw:planning-v1:begin -->
### 当前计划
1. `SHOP-TASK-151` 会员会话的请求模块与全站共享的会话状态
2. `SHOP-TASK-152` 会员中心 P13 的框架与我的订单
3. `SHOP-TASK-153` 页头的登录入口按会员会话切换为会员中心
4. `SHOP-TASK-154` 订单详情 P09 的会员模式与会员下单订单的券、积分两行
5. `SHOP-TASK-155` 退款申请 P10 的会员模式与积分返还、追回三行

### 已阻塞
- 待登记：结账会员路径 P05（V1 结账用途、会员第 3 步的券与积分）与支付页 P06、结果页 P07 的会员访问（按 DESIGN 1.13 第 7 条暂存订单号；P09 会员模式待支付订单的 account.order_pay；P07 的 result.track 进入 P09 会员模式）｜阻塞：等会员前端第二批（SHOP-TASK-149 至 155）合并后按实际代码登记
- 待登记：会员中心 P13 的积分、优惠券、设置与注销（含 V1 注销用途；手机版遮掩号码与退出移入设置段）｜阻塞：等会员前端第二批（SHOP-TASK-149 至 155）合并后按实际代码登记
- 待登记：后台商品 A04（含每单限购）｜阻塞：依赖后台框架（随 A02 交付）与管理后台业务接口（商品与每单限购）
- 待登记：运费区与演示汇率 A06｜阻塞：依赖管理后台业务接口（运费与汇率）
- 待登记：后台站点设置 A09（短信验证开关，含保存接口）｜阻塞：等短信验证服务与会员结账路径上线后再做（Kelvin 2026-10-07 决定：开关打开会拒绝马新号码的游客下单，在那之前不提供入口）
- 待登记：运营者处置转人工的 Stripe 会话记录（在 Stripe 后台核实后，把创建结果长期不明或付款长期未定的记录标为 expired 并按现有规则取消订单；设计第 3、5 条只写「告警由人工核对」，登记时的预审指出站内没有处置手段；另含来源为会话（迟到与不符的付款）的退款尝试失败后的处置，设计只给退款申请来源提供手动重试，SHOP-TASK-124 登记时的预审指出这一缺口；涉及支付状态，登记前须按设计闸门写明核实条件并经 Kelvin 批准）｜阻塞：涉及支付状态，登记前须按设计闸门写明核实条件并经 Kelvin 批准

### 后续计划
- 待登记：店铺标志图上传与移除（随图片的格式、大小与存放规则，与商品图一起做，Kelvin 2026-10-06 决定）
- 待登记：管理后台业务接口（商品与每单限购、运费与汇率；各操作写审计记录）
- 待登记：运营告警邮件（上线前提：含定时任务失败告警，Kelvin 2026-10-01 决定；含管理员登录锁定告警，Kelvin 2026-10-04 决定；含会员注销删除失败告警，DESIGN 1.11「资料保留」；含 Stripe 测试环境支付的各类告警，SHOP-TASK-111 起先记 error 级日志，DESIGN 1.12）
- 待登记：马来文文案母语审校（上线后补，Kelvin 2026-09-30 决定）
<!-- openclaw:planning-v1:end -->

---

## 任务记录

SHOP-TASK-001 至 117 的记录在 2026-10-19 从本文件原样移出，一个任务一个文件；已交付任务登记时的验收标准原文也随之从 `.platform/tasks.yaml` 移到同一文件末尾。之后的任务不再往这里加行，直接看 [task-records/](task-records/) 目录。

- [SHOP-TASK-001](task-records/SHOP-TASK-001.md) 前端页面结构、线框与三语文案审阅稿
- [SHOP-TASK-002](task-records/SHOP-TASK-002.md) 计价逐件分摊纯函数
- [SHOP-TASK-003](task-records/SHOP-TASK-003.md) 按设计 1.8 修订页面线框与三语文案审阅稿
- [SHOP-TASK-004](task-records/SHOP-TASK-004.md) 商品目录数据模型与迁移
- [SHOP-TASK-005](task-records/SHOP-TASK-005.md) 商品目录只读查询与筛选 API
- [SHOP-TASK-006](task-records/SHOP-TASK-006.md) 示例商品目录种子数据
- [SHOP-TASK-007](task-records/SHOP-TASK-007.md) 运费区与演示汇率的数据模型、示例费率与查询函数
- [SHOP-TASK-008](task-records/SHOP-TASK-008.md) 按 Kelvin 审阅意见把线框与三语文案修订为 0.3
- [SHOP-TASK-009](task-records/SHOP-TASK-009.md) 购物车与结账计价 API（游客计价）
- [SHOP-TASK-010](task-records/SHOP-TASK-010.md) 订单数据模型、迁移与状态迁移规则
- [SHOP-TASK-011](task-records/SHOP-TASK-011.md) 手机号 E.164 规范化与短信白名单判定
- [SHOP-TASK-012](task-records/SHOP-TASK-012.md) 会员、会话、短信验证记录与每日预算的数据模型
- [SHOP-TASK-013](task-records/SHOP-TASK-013.md) 商品目录接口补充首页与列表页所需字段
- [SHOP-TASK-014](task-records/SHOP-TASK-014.md) 前端基础：设计变量、三语字典、路由、全站框架与隐私说明页 P14
- [SHOP-TASK-015](task-records/SHOP-TASK-015.md) 首页区块 P01 与商品列表 P02
- [SHOP-TASK-016](task-records/SHOP-TASK-016.md) 每单限购：商品限购字段、迁移与计价接口校验
- [SHOP-TASK-017](task-records/SHOP-TASK-017.md) 商品详情 P03 与浏览器购物车
- [SHOP-TASK-018](task-records/SHOP-TASK-018.md) 购物车 P04
- [SHOP-TASK-019](task-records/SHOP-TASK-019.md) 订单访问授权：服务端会话、授权记录、cookie 与 CSRF
- [SHOP-TASK-020](task-records/SHOP-TASK-020.md) 游客下单 API
- [SHOP-TASK-021](task-records/SHOP-TASK-021.md) 模拟支付、取消与支付超时 API
- [SHOP-TASK-022](task-records/SHOP-TASK-022.md) 站点设置：短信验证开关的存储与读取
- [SHOP-TASK-023](task-records/SHOP-TASK-023.md) 结账参考数据：地区与国家码列表、马来西亚州属列表
- [SHOP-TASK-024](task-records/SHOP-TASK-024.md) 模拟支付 P06 与支付结果 P07（游客）
- [SHOP-TASK-025](task-records/SHOP-TASK-025.md) 结账 P05 游客路径
- [SHOP-TASK-026](task-records/SHOP-TASK-026.md) Redis 连接与限流基础
- [SHOP-TASK-027](task-records/SHOP-TASK-027.md) 订单查询与确认收货 API
- [SHOP-TASK-028](task-records/SHOP-TASK-028.md) 订单查询 P08 与订单详情 P09（查单模式）
- [SHOP-TASK-029](task-records/SHOP-TASK-029.md) 退款申请 API（查单模式）
- [SHOP-TASK-030](task-records/SHOP-TASK-030.md) 退款申请 P10 与订单详情 P09 的退款部分（查单模式）
- [SHOP-TASK-031](task-records/SHOP-TASK-031.md) 定时任务运行器与支付超时取消
- [SHOP-TASK-032](task-records/SHOP-TASK-032.md) 每日库存重置
- [SHOP-TASK-033](task-records/SHOP-TASK-033.md) 框架测试按路由表推算尚未实现的页面
- [SHOP-TASK-034](task-records/SHOP-TASK-034.md) 管理员账号、后台会话与审计记录的数据模型
- [SHOP-TASK-035](task-records/SHOP-TASK-035.md) 管理员密码、建账号命令、后台会话与登录锁定规则
- [SHOP-TASK-036](task-records/SHOP-TASK-036.md) 管理员登录、退出与会话 API
- [SHOP-TASK-037](task-records/SHOP-TASK-037.md) 后台订单查看与模拟发货 API
- [SHOP-TASK-038](task-records/SHOP-TASK-038.md) 后台登录页 A01
- [SHOP-TASK-039](task-records/SHOP-TASK-039.md) 退款申请的审核人与审核理由（数据模型）
- [SHOP-TASK-040](task-records/SHOP-TASK-040.md) 退款审核规则与全部退款后冻结确认收货
- [SHOP-TASK-041](task-records/SHOP-TASK-041.md) 后台退款审核 API
- [SHOP-TASK-042](task-records/SHOP-TASK-042.md) 发货满 7 天自动确认收货
- [SHOP-TASK-043](task-records/SHOP-TASK-043.md) 公开接口限流（计价、下单）
- [SHOP-TASK-044](task-records/SHOP-TASK-044.md) 店铺装修设置的数据模型与默认值（不含标志图）
- [SHOP-TASK-045](task-records/SHOP-TASK-045.md) 店铺装修设置的公开读取接口
- [SHOP-TASK-046](task-records/SHOP-TASK-046.md) 后台退款申请列表按订单筛选
- [SHOP-TASK-047](task-records/SHOP-TASK-047.md) 后台框架与登录后进入 A02
- [SHOP-TASK-048](task-records/SHOP-TASK-048.md) 后台订单列表 A02
- [SHOP-TASK-049](task-records/SHOP-TASK-049.md) 管理员以邮箱登录（存储、建账号命令与登录接口）
- [SHOP-TASK-050](task-records/SHOP-TASK-050.md) 后台登录页 A01 以邮箱登录
- [SHOP-TASK-051](task-records/SHOP-TASK-051.md) 后台店铺装修的读取与保存接口（不含标志图）
- [SHOP-TASK-052](task-records/SHOP-TASK-052.md) 后台库存重置结果的查询接口
- [SHOP-TASK-053](task-records/SHOP-TASK-053.md) 商品列表接口按 slug 筛选
- [SHOP-TASK-054](task-records/SHOP-TASK-054.md) 前台按店铺装修显示
- [SHOP-TASK-055](task-records/SHOP-TASK-055.md) 后台订单详情与模拟发货 A02
- [SHOP-TASK-056](task-records/SHOP-TASK-056.md) 后台退款申请列表 A03
- [SHOP-TASK-057](task-records/SHOP-TASK-057.md) 后台退款详情与审核 A03
- [SHOP-TASK-058](task-records/SHOP-TASK-058.md) A02 待审退款数链接到该单的退款申请
- [SHOP-TASK-059](task-records/SHOP-TASK-059.md) 后台库存重置页的路由与导航 A07
- [SHOP-TASK-060](task-records/SHOP-TASK-060.md) 后台库存重置结果列表与明细 A07
- [SHOP-TASK-061](task-records/SHOP-TASK-061.md) 后台店铺装修保存时放行原已在精选里的下架商品
- [SHOP-TASK-062](task-records/SHOP-TASK-062.md) 后台店铺装修页的路由与导航 A08
- [SHOP-TASK-063](task-records/SHOP-TASK-063.md) 后台店铺装修的读取与保存请求模块 A08
- [SHOP-TASK-064](task-records/SHOP-TASK-064.md) 后台店铺装修的主题、主色、首页区块与保存 A08
- [SHOP-TASK-065](task-records/SHOP-TASK-065.md) 后台店铺装修的精选商品挑选 A08
- [SHOP-TASK-066](task-records/SHOP-TASK-066.md) 后台店铺装修的预览 A08
- [SHOP-TASK-067](task-records/SHOP-TASK-067.md) 短信验证与人机挑战的服务适配器
- [SHOP-TASK-068](task-records/SHOP-TASK-068.md) 短信每日总量与费用预算的原子预占与结算
- [SHOP-TASK-069](task-records/SHOP-TASK-069.md) 短信验证的发送与核验规则
- [SHOP-TASK-070](task-records/SHOP-TASK-070.md) 发送短信验证码接口
- [SHOP-TASK-071](task-records/SHOP-TASK-071.md) 会员会话与密码登录锁定规则
- [SHOP-TASK-072](task-records/SHOP-TASK-072.md) 短信验证记录满 30 天删除
- [SHOP-TASK-073](task-records/SHOP-TASK-073.md) 站点设置接口提供 WhatsApp 联系链接
- [SHOP-TASK-074](task-records/SHOP-TASK-074.md) 页脚与隐私说明 P14 在已配置时显示 WhatsApp 联系入口
- [SHOP-TASK-075](task-records/SHOP-TASK-075.md) 短信验证后登录或注册与认领游客订单规则
- [SHOP-TASK-076](task-records/SHOP-TASK-076.md) 短信登录或注册接口、会员会话与退出
- [SHOP-TASK-077](task-records/SHOP-TASK-077.md) 会员密码登录与首次设置密码接口
- [SHOP-TASK-078](task-records/SHOP-TASK-078.md) 会员重设密码的一次性凭据与重设规则
- [SHOP-TASK-079](task-records/SHOP-TASK-079.md) 会员重设密码接口
- [SHOP-TASK-080](task-records/SHOP-TASK-080.md) 会员注销与注销批准的规则
- [SHOP-TASK-081](task-records/SHOP-TASK-081.md) 发送与核验会员注销验证码的接口
- [SHOP-TASK-082](task-records/SHOP-TASK-082.md) 确认会员注销的接口
- [SHOP-TASK-083](task-records/SHOP-TASK-083.md) 优惠券与用券记录的数据模型
- [SHOP-TASK-084](task-records/SHOP-TASK-084.md) 积分批次与积分流水的数据模型
- [SHOP-TASK-085](task-records/SHOP-TASK-085.md) 优惠券的查券、预占、释放与核销规则
- [SHOP-TASK-086](task-records/SHOP-TASK-086.md) 积分的获得、预占、释放、到期与作废规则
- [SHOP-TASK-087](task-records/SHOP-TASK-087.md) 积分的退款返还与追回规则
- [SHOP-TASK-088](task-records/SHOP-TASK-088.md) 会员下单的计价与下单规则
- [SHOP-TASK-089](task-records/SHOP-TASK-089.md) 会员计价与下单的接口
- [SHOP-TASK-090](task-records/SHOP-TASK-090.md) 模拟支付成功、取消与超时接入优惠券与积分
- [SHOP-TASK-091](task-records/SHOP-TASK-091.md) 支付页与结果页的会员接口
- [SHOP-TASK-092](task-records/SHOP-TASK-092.md) 退款批准接入积分返还与追回
- [SHOP-TASK-093](task-records/SHOP-TASK-093.md) 退款申请页的积分预估接口（查单模式）
- [SHOP-TASK-094](task-records/SHOP-TASK-094.md) 会员注销时作废积分余额
- [SHOP-TASK-095](task-records/SHOP-TASK-095.md) 会员中心的积分概览与积分明细查询
- [SHOP-TASK-096](task-records/SHOP-TASK-096.md) 积分到期的定时任务
- [SHOP-TASK-097](task-records/SHOP-TASK-097.md) 超时取消逐张订单独立提交
- [SHOP-TASK-098](task-records/SHOP-TASK-098.md) 后台优惠券的列表、新建与停用规则
- [SHOP-TASK-099](task-records/SHOP-TASK-099.md) 后台优惠券的列表、新建与停用接口
- [SHOP-TASK-100](task-records/SHOP-TASK-100.md) 后台优惠券页的路由与导航 A05
- [SHOP-TASK-101](task-records/SHOP-TASK-101.md) 后台优惠券的请求模块 A05
- [SHOP-TASK-102](task-records/SHOP-TASK-102.md) 后台优惠券的列表、翻页与停用 A05
- [SHOP-TASK-103](task-records/SHOP-TASK-103.md) 订单视图补券与积分，确认收货与退款申请可由会员操作
- [SHOP-TASK-104](task-records/SHOP-TASK-104.md) 我的订单的列表、详情与确认收货接口
- [SHOP-TASK-105](task-records/SHOP-TASK-105.md) 会员模式的退款申请与积分预估接口
- [SHOP-TASK-106](task-records/SHOP-TASK-106.md) 后台退款详情给出积分返还与追回
- [SHOP-TASK-107](task-records/SHOP-TASK-107.md) 后台退款详情显示积分一行 A03
- [SHOP-TASK-108](task-records/SHOP-TASK-108.md) 会员中心的积分与优惠券查询接口
- [SHOP-TASK-109](task-records/SHOP-TASK-109.md) 后台优惠券的新建表单与提交 A05
- [SHOP-TASK-110](task-records/SHOP-TASK-110.md) Stripe 测试会话记录与支付方式列的数据模型
- [SHOP-TASK-111](task-records/SHOP-TASK-111.md) Stripe 测试环境的配置与适配器
- [SHOP-TASK-112](task-records/SHOP-TASK-112.md) 支付途径判定与创建 Stripe 测试会话
- [SHOP-TASK-113](task-records/SHOP-TASK-113.md) 确认 Stripe 测试付款
- [SHOP-TASK-114](task-records/SHOP-TASK-114.md) 有 Stripe 记录的订单的模拟支付、本人取消与超时取消
- [SHOP-TASK-115](task-records/SHOP-TASK-115.md) Stripe 事件与退款尝试的数据模型
- [SHOP-TASK-116](task-records/SHOP-TASK-116.md) 有 Stripe 记录的订单的定时处理
- [SHOP-TASK-117](task-records/SHOP-TASK-117.md) 迟到与不符的 Stripe 测试付款
