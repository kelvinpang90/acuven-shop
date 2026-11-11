# SHOP-TASK-239 后台促销活动的输入数据类与取值检查（不碰数据库）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 1–3、12 条：活动规则的取值范围与状态判定，决定之后的计价；不碰个人资料。依据 UX 0.20 的 A12。依赖 SHOP-TASK-229（模型常量）。查库的检查、列表、读取、新建与保存见 SHOP-TASK-249；海报图见 240 与 250；状态操作见 241；接口见 242；AI 提案的校验（248）复用这里的检查函数。不改查库与写库的逻辑、接口、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新文件 `app/services/admin_promotions.py`（本任务只写第一部分，不 import Session、不读写数据库）。
- [x] 输入数据类（都是 frozen dataclass）：

  | 类 | 字段 | 说明 |
  | --- | --- | --- |
  | `TierInput` | `threshold_sen`, `amount_sen`, `percent` | 满额减或满额折的一档；`amount_sen` 与 `percent` 按规则的方式二选一 |
  | `RuleInput` | `kind`, `discount_mode`, `percent`, `amount_sen`, `buy_quantity`, `get_quantity`, `get_percent`, `threshold_sen`, `product_ids`, `category_ids`, `tiers` | 除 `kind` 外都有默认值（参数 None，范围与满额档为空元组） |
  | `DecorationInput` | `pack`, `palette`, `ambient`, `home_poster`, `list_strip`, `card_badge`, `detail_ribbon`, `cart_progress`, `poster_mode` | 无默认值 |
  | `CopyInput` | `title_*`, `subtitle_*`, `badge_*`, `strip_*`, `poster_alt_*`（`*` 为 `en`、`zh`、`ms`，共 15 项） | 各项默认 None |
  | `PromotionInput` | `name`, `starts_at`, `ends_at`, `coupon_combinable`, `points_combinable`, `rules`, `decoration`, `copy` | 时间为马来西亚本地时间 `YYYY-MM-DDTHH:MM` 或 None；`rules` 的顺序即规则的排序（从 1 起） |

- [x] 问题与异常：
  - `PromotionProblem(code, field, product_id=None, product_name=None)`（frozen）；后两项只在 249 的 `scope_overlap` 时有值，`product_name` 为英文名称。
  - `PromotionInvalid(problems)`：`problems` 存为元组。
  - 问题码常量：`PROBLEM_NAME`（name）、`PROBLEM_TIME`（time）、`PROBLEM_DURATION`（duration）、`PROBLEM_RULE`（rule）、`PROBLEM_SCOPE`（scope）、`PROBLEM_TIERS`（tiers）、`PROBLEM_DECORATION`（decoration）、`PROBLEM_COPY`（copy）。`scope_overlap`、`publish_missing`、`rules_locked`、`ended_readonly` 由 249 再加。
  - `field` 用点分隔、序号从 1 起：顶层为 `name`、`starts_at`、`ends_at`、`rules`（条数超限）；规则内为 `kind`、各参数名、`tiers`、`tiers.<档序号>.<threshold_sen|amount_sen|percent>`、`product_ids`、`category_ids`；装饰与文案为各自的字段名。四个检查函数返回的 `field` 相对于各自的输入，`check_promotion_input` 加上前缀 `rules.<规则序号>.`、`decoration.`、`copy.`，如 `rules.2.tiers.3.amount_sen`、`copy.title_en`。
- [x] 检查函数（各自返回 `list[PromotionProblem]`，不抛异常）：
  - `check_rule_params(rule: RuleInput)`：类别在 `RULE_KINDS` 里（否则只报 `rule`/`kind`）；第 3 条各类参数范围（商品级打折百分比 1–90 或每件直降 1–50,000,000，X、Y 1–10，享受折扣 1–100，免运费门槛 0–99,999,999）；不属于该类别的参数非空或非满额类规则带满额档，问题码 `rule`。满额档：1–5 档、门槛 1–99,999,999、每档优惠与规则方式一致（另一项须为空）、百分比 1–90、固定金额 1 到小于门槛、门槛与优惠逐档严格递增，问题码 `tiers`。不查范围。
  - `check_rule_scope(rule: RuleInput)`：商品级规则的商品与分类 ID 为正整数、各至多 200 件与 30 个、同一规则里不重复、至少选一项（两项都空时报在 `product_ids`）；整单级规则两项须为空；问题码 `scope`。类别未知时不报（由参数检查报）。
  - `check_decoration(decoration: DecorationInput)`：八个 ID 字段在 `DECORATION_CHOICES` 里、`poster_mode` 在 `POSTER_MODES` 里，问题码 `decoration`。
  - `check_copy(copy: CopyInput)`：None 跳过；须为字符串、不含 Cc 类字符与 U+2028、U+2029（Cf 类如零宽连接符允许），去掉首尾空白后的长度在上限内（标题 40、副标题 80、角标短语 12、横条文字 60、替代文字 150，常量 `COPY_MAX_LENGTHS`），问题码 `copy`。
  - `check_promotion_input(data: PromotionInput) -> PromotionInput`：依次查内部名称（去掉首尾空白后 1–100 个字符、同一字符规则，`name`）、时间（`time`；两者都有时结束晚于开始，时长超过 92 天为 `duration`，两者都报在 `ends_at`）、每条规则的参数与范围、规则条数（商品级至多 20 条、满额减或满额折与免运费各至多一条，`rule`/`rules`）、装饰、文案；有问题即抛 `PromotionInvalid`（列出全部问题）。没有问题时返回名称去掉首尾空白、文案经 `clean_copy` 处理的输入。
- [x] 辅助函数：`clean_text(value)`（去掉首尾空白，空串为 None）、`clean_copy(copy)`（文案各项照 `clean_text`，供 249 保存时「空串存为空」）、`has_forbidden_char(text)`（上面的字符规则，供 248 复用）。常量 `MAX_DURATION`（92 天）、`ITEM_RULES_MAX`、`SCOPE_PRODUCTS_MAX`、`SCOPE_CATEGORIES_MAX`、`ITEM_KINDS`、`ORDER_KINDS`、`LOCAL_TIME_FORMAT`。
- [x] 时间与状态：
  - `to_utc(local: str | None) -> datetime | None`：`YYYY-MM-DDTHH:MM`（ASCII 数字、补零）减 `sms_budget.MALAYSIA_UTC_OFFSET`，得不带时区的 UTC；格式不对、日期时刻不存在或越界抛 ValueError；None 原样返回。
  - `to_local(utc: datetime | None) -> str | None`：加 8 小时，用 `isoformat(timespec="minutes")` 截去秒与微秒；带时区或不是 datetime 抛 ValueError；None 原样返回。
  - `promotion_state(status, starts_at, effective_end, now) -> str`：返回 `STATE_DRAFT`（draft）、`STATE_SCHEDULED`（scheduled）、`STATE_RUNNING`（running）、`STATE_ENDED`（ended）之一（`PROMOTION_STATES`）。草稿不看时间；已发布时 now < 开始为 scheduled，开始 ≤ now < 实际结束为 running，否则 ended。已发布却缺时间、状态未知、时间带时区抛 ValueError。
- [x] 测试 `tests/test_admin_promotion_input.py`（纯函数，不建库）：内部名称 1/100/101 个字符与含换行；92 天整与多一分钟、结束等于或早于开始、各种格式错误、草稿不填时间；百分比 0、1、90、91，每件直降 0、1、50,000,000、50,000,001，X 与 Y 的 0、1、10、11，享受折扣 0、1、100、101，免运费门槛 −1、0、99,999,999、100,000,000；不属于该类别的参数与满额档；未知类别；满额档 0/5/6 档、固定金额等于门槛、门槛与优惠不递增、方式混用；`check_rule_params` 不因缺范围报错；200/201 件商品、30/31 个分类、重复与非正整数 ID、整单级规则带范围；规则条数上限；promotions.json 清单里的每个 ID 合格、清单外的 ID 不合格；15 项文案的长度边界、41 个字符的标题、各种换行与控制字符、带零宽连接符的 emoji；跨日换算与带秒的 UTC 反向换算截秒；四种状态的边界（开始时刻为 running、实际结束时刻为 ended，提前结束同理）。每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - 结构错误（`PromotionInput` 以外的对象、`rules` 不是 `RuleInput` 序列、装饰与文案的类型不对、满额档不是 `TierInput`、两个开关不是 bool）抛 TypeError，不记为问题：这些由接口层（242）或 248 构造数据类时保证，不是管理员能填出来的取值。
  - 换行与控制字符查原文而不是去掉首尾空白之后：`str.strip` 会去掉 `\x1c`–`\x1f` 与 `\x85` 这些 Cc 字符，先去空白再查就会悄悄收下它们；所以首尾带换行的文案也不合格（UX A12「不能输入换行」）。
  - 两个时间都在时才比较先后与时长；单独一个时间合格即可（草稿可以不填，发布条件由 249 查）。
  - 满额档的方式不合格时只报 `rule`/`discount_mode`，各档只查门槛（优惠无从对照）。
  - 装饰按字段单独查：SHOP-TASK-229 的测试已守住每个素材包的变体与 slots 相同，按字段查即等于按清单查。
  - 新增验收未点名的 `clean_text`、`clean_copy`、`has_forbidden_char`、`STATE_*` 与若干上限常量，供 249、241、248 共用。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 1–3、12 条、UX A12 与 `app/models/promotion.py` 的常量，手算核对测试里的边界（2027-01-01 加 92 天为 2027-04-03；带零宽连接符的一家三口 emoji 为 5 个码位）。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。
