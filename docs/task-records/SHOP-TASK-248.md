# SHOP-TASK-248 AI 生成装饰提案（服务层）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 12、13 条：出站内容限定为第 13 条四项；描述不保存；提案不改规则与金额。依赖 SHOP-TASK-239（检查函数）、246（计数与记录表）与 247（客户端）；接口见 SHOP-TASK-251。不改活动的保存与计价、接口、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新文件 `app/services/promotion_ai.py`。

## 供 SHOP-TASK-251 引用

- 引入：`from app.services.promotion_ai import AiDailyLimit, AiNotConfigured, AiResultInvalid, AiUnavailable, DecorationProposal, propose_decoration`（`AiNotConfigured` 与 `AiUnavailable` 就是 `app.services.ai_hub` 的同名类，这里转出）；`PromotionInvalid` 与 `RuleInput` 取自 `app.services.admin_promotions`。
- 签名：`propose_decoration(db: Session, client: AiHubClient, settings: Settings, description: str, rules: Sequence[RuleInput], now: datetime) -> DecorationProposal`。`client` 按 `AiHubClient.from_settings(settings)` 构造；`rules` 为活动的规则（顺序即排序），其中的 `product_ids`、`category_ids` 忽略、不送出；`now` 为不带时区的 UTC。
- 提案 `DecorationProposal`（frozen dataclass，二十个字段，模块常量 `PROPOSAL_FIELDS`）：
  - 八个装饰 ID（`str`，都在 `DECORATION_CHOICES` 里）：`pack`、`palette`、`ambient`、`home_poster`、`list_strip`、`card_badge`、`detail_ribbon`、`cart_progress`；
  - 文案（去掉首尾空白，空串为 `None`）：`title_en`（`str`，总不为空）、`title_zh`、`title_ms`、`subtitle_*`、`badge_*`、`strip_*`（`*` 为 `en`、`zh`、`ms`，`str | None`）。
  - 没有海报模式与替代文字（由管理员定），没有规则、时间、开关与金额的字段。提案只返回、不保存；管理员确认后以普通保存提交（249 照第 12 条再校验一次）。
- 异常（按检查顺序）：
  | 异常 | 何时 | 计数 | 记录 |
  | --- | --- | --- | --- |
  | `PromotionInvalid` | 描述去掉首尾空白后不是 1–500 个字符（问题 `description`/`description`）；规则参数不合格（`check_rule_params` 的问题，`field` 加 `rules.<序号>.` 前缀） | 不计 | 不记 |
  | `TypeError` | `rules` 不是 `RuleInput` 的元组或列表（结构错误，同 239） | 不计 | 不记 |
  | `AiNotConfigured` | `client.configured` 为假 | 不计 | 不记 |
  | `AiDailyLimit` | 当天（马来西亚日期）已达 `settings.ai_daily_call_limit`；不调用 | 不加 | 不记 |
  | `AiUnavailable` | 调用超时（记 `timeout`）或其余失败（非 200、余额不足、网络错误、响应格式不对，记 `error`） | 占用一次 | token 为空 |
  | `AiResultInvalid` | 回答不是一个 JSON 对象、多出或缺少字段、值不是字符串、任何一项不合格 | 占用一次 | `invalid` 与 token 数 |
  | （返回提案） | 合格 | 占用一次 | `ok` 与 token 数 |
- 事务：函数先 `db.rollback()`（调用方此前未提交的改动会丢弃，ORM 属性须先取出），再读店铺装修；计数加一后提交，调用模型时不持锁；调用后写记录再提交。三个异常类的消息固定，不含描述与回答原文。

## 做了什么

- [x] 输入检查：描述去掉首尾空白后 1–500 个字符（非字符串同样不合格）；每条规则以 239 的 `check_rule_params` 检查。都在访问数据库与计数之前。
- [x] 送出的内容（第 13 条四项）：
  - 系统提示 `SYSTEM_PROMPT` 与 `OUTPUT_SCHEMA` 只由模块常量组成：只回一个 JSON 对象、二十个字段名、`DECORATION_CHOICES`（SHOP-TASK-229）里八个装饰位的可选 ID、`COPY_MAX_LENGTHS`（239）的标题 40、副标题 80、角标短语 12、横条文字 60、英文标题不为空、不得含换行与控制字符、不得改动规则、时间、开关与金额。`OUTPUT_SCHEMA` 以 `enum`、`maxLength` 约束、`required` 为全部二十项、`additionalProperties: false`。
  - 用户内容为一个 JSON：`description`（去掉首尾空白）、`rules`（每条只有 `kind` 与不为空的参数，满额档只给各档不为空的 `threshold_sen`、`amount_sen`、`percent`）、`theme`、`accent`。主题与主色在每次调用时以 `store_design.read_store_design` 读出（没有单例行时为默认主题 `pandan`）；主色为空时送出 `THEME_ACCENTS[theme][0]`，不送 null。
  - `max_tokens` 为 2000（`MAX_TOKENS`）。不读订单、会员与收货资料。
- [x] 每日计数照 `app/services/sms_budget.py`：普通查询找当天（`sms_budget.malaysia_date`）的行 → 没有就在 `begin_nested` 里以 Core `insert` 插入 `call_count=0` 的行，主键冲突（MySQL 1062 且键为 `PRIMARY`，SQLite 为固定消息）即忽略，其他完整性错误原样抛出 → `SELECT … FOR UPDATE`（`populate_existing`）锁行 → `call_count >= ai_daily_call_limit` 即回滚并抛 `AiDailyLimit`，否则加一并提交 → 调用模型。加一不退回。
- [x] 回答的校验：`json.loads`（字段名重复也算不合格）→ 须是对象、字段集合恰为 `PROPOSAL_FIELDS`、值都是字符串 → `check_decoration`（海报模式取 `template`）与 `check_copy`（替代文字取空）都没有问题 → 英文标题去掉首尾空白后不为空。任何一项不合格即整份不用。
- [x] 记录：每次调用后写一条 `PromotionAiCall`（`called_at` 取 `now`、结果类别、两个 token 数）并提交，不记描述与回答原文。
- [x] 测试 `tests/test_promotion_ai.py`（SQLite 内存库，引擎设置照 `tests/test_sms_budget.py`；真实 `AiHubClient` 配替身传输函数）：合格的提案（不写活动与装饰表）与文案长度上限、emoji；清单外的 ID、空 ID、超长文案、换行（含首尾）、行分隔符、控制字符、英文标题为空白或缺少、多出字段（海报模式、规则参数）、值不是字符串、不是 JSON、代码块包裹、JSON 数组、两个对象、字段名重复时整份不用并记 invalid；上限 50 时第 51 次拒绝且不调用；按马来西亚日期切日；当天没有行时先插后锁、已有行时累加；两个会话各插一次不报错（替换普通查询模拟快照漏看）；超时、网络错误、402、500 与响应格式不对仍占用一次、记 timeout 或 error；记录表只有五列且不含描述与回答原文；送出的请求体只有四项、没有商品与分类 ID，系统提示与 schema 写明格式、清单与长度；主题与主色跟随店铺装修（没有行、主色为空、改主色、改主题）；描述与规则不合格、未配置时不计数。每条测试的文档字符串写明它守住的第 12 或 13 条哪一句。

## 偏离与落实细节，请审阅

- 规则检查的问题码沿用 `check_rule_params` 自己的：参数问题为 `rule`，满额档本身的问题为 `tiers`（与 249 保存时一致），没有把 `tiers` 改写成 `rule`。
- 只查规则参数，不查范围与规则条数（验收只点名 `check_rule_params`；范围不送出）。
- 描述只查长度，不查换行与控制字符（描述是给模型的自由文字，不在前台显示）。
- 超时与其余失败对调用方都是 `AiUnavailable`（客户端的 `AiTimeout` 不外抛），只在记录里分 `timeout` 与 `error`。
- 回答里的文案值须是字符串（`null` 不合格）；空串表示不要该项，返回时为 `None`。
- 客户端返回负的 token 数时记为空（表的约束要求非负；客户端只检查是整数）。
- 读店铺装修放在计数之前：读库出错时不占用当天的次数。
- 多出的公开常量：`SYSTEM_PROMPT`、`OUTPUT_SCHEMA`、`MAX_TOKENS`、`PROPOSAL_FIELDS`、`DECORATION_FIELDS`、`COPY_FIELDS`、`DESCRIPTION_MAX_LENGTH` 等，供测试与 251 使用。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.23 第 12、13 条与 239、246、247 的记录，核对检查函数、模型常量、客户端异常与 `store_design` 的读取口径。没有连真实的 Billing Hub。
- MySQL 上的行锁等待与主键冲突的错误文本只按 `sms_budget` 的同类写法推定，SQLite 测试不复现阻塞。
- 后端测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 服务——propose_decoration(db, client, settings, description, rules, now) 返回 DecorationProposal（落实写法所列的字段）：rules 为 SHOP-TASK-239 的 RuleInput 序列（范围字段忽略、不送出）；描述去掉首尾空白后为 1–500 个字符（否则 PromotionInvalid，问题码 description），规则参数不合格同样抛 PromotionInvalid，都不计数；未配置抛 AiNotConfigured 且不计数；当天已达上限抛新异常 AiDailyLimit 且不调用；调用超时或失败抛 AiUnavailable（记 timeout 或 error）；回答不是一个 JSON 对象、多出或缺少字段、任何一项不合格抛新异常 AiResultInvalid（记 invalid）；合格记 ok 并返回
      - 提示词——system 写明只回一个 JSON 对象、可选的 ID 清单与各文案的长度上限、文案不得含换行与控制字符、不得改动规则；user 内容为一个 JSON：描述、规则类别和参数，以及调用时从店铺装修单例行读出的当前 theme 与主色 ID（还没有那一行时取默认主题，照 app/services/store_design.py 的读取；accent 为空即该主题的默认主色，送出 app/models/store_design.py 的 THEME_ACCENTS 里该主题的第一项，不送 null）；以 output_schema 约束字段名；max_tokens 不超过 2000。测试断言送出的请求体里只有第 13 条所列四项，其中主题与主色等于当时店铺装修的取值（改过装修后送出新值；主色未选时送出该主题的默认主色 ID），没有商品 ID、订单与会员资料
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_promotion_ai.py（以替身客户端，SQLite 内存库）：合格的提案；清单外的 ID、超长文案、带换行、缺英文标题、多出字段、不是 JSON 时整份不用并记 invalid；上限 50 时第 51 次拒绝且不调用；当天没有行时先插后锁（两个会话各插一次不报错）；调用失败仍占用一次；记录表不含描述原文；送出的内容只有四项；描述与规则不合格不计数。每条测试的文档字符串写明它守住的第 12 或 13 条哪一句
      - docs/task-records/SHOP-TASK-248.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明函数签名、提案字段与异常，供 251 引用）；改动只落在 allowed_change_paths；不改活动的保存与计价、接口、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
