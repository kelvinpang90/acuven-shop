# SHOP-TASK-249 后台促销活动的列表、读取、新建与保存（服务层，查库与写库）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 1–3、12 条：活动的规则、时间、开关与状态，决定之后的计价；只写后台审计，不碰个人资料。依据 UX 0.20 的 A12。依赖 SHOP-TASK-229（模型）与 239（输入数据类与取值检查）。不改计价、下单、接口、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 在 `app/services/admin_promotions.py` 239 的部分之后加上查库与写库的部分（模块文档字符串随之更新）；新测试 `tests/test_admin_promotions.py`。
- [x] 新问题码：`PROBLEM_SCOPE_OVERLAP`（scope_overlap）、`PROBLEM_PUBLISH_MISSING`（publish_missing）、`PROBLEM_RULES_LOCKED`（rules_locked）、`PROBLEM_ENDED_READONLY`（ended_readonly）。各问题的 `field`：
  - `scope`（查库）：`rules.<序号>.product_ids` 或 `rules.<序号>.category_ids`，该规则引用了不存在的商品或分类，每条规则每项至多一个。
  - `scope_overlap`：`rules.<序号>`，为该商品第二次出现的那条规则；`product_id` 为商品 ID，`product_name` 为英文名称（为空时用 slug）。每件重叠的商品一个，按序号与商品 ID 排列。
  - `publish_missing`：`rules`、`title_en`、`starts_at`、`ends_at`、`ends_at_past`、`poster_desktop`、`poster_phone`、`poster_alt_en` 之一，按这个顺序（`ends_at` 为空时不再报 `ends_at_past`）。
  - `rules_locked`：`starts_at`、`ends_at`、`coupon_combinable`、`points_combinable`、`rules` 之一，按这个顺序。
  - `ended_readonly`：`state`（常量 `STATE_FIELD`）。
- [x] 异常：
  - `PromotionInvalid(problems)`（239）：取值或查库检查不合格。
  - `PromotionNotFound`：`read_promotion` 的活动不存在。
  - `PromotionChanged`：保存时活动不存在或版本号不同，或替换规则时撞上订单逐件快照的外键（IntegrityError）。
  - `PromotionOverlap(other_name)`：与其他已发布活动的时间重叠，`other_name` 为对方的内部名称。
  - 结构错误（含 `version` 不是 int，布尔值不算）抛 TypeError；`now` 不是不带时区的 datetime 抛 ValueError。
- [x] 读出的数据类（都是 frozen dataclass）：

  | 类 | 字段 | 说明 |
  | --- | --- | --- |
  | `PromotionSummary` | `id`, `name`, `state`, `starts_at`, `ends_at`, `rule_count`, `updated_at` | 时间为马来西亚本地 `YYYY-MM-DDTHH:MM` 或 None，`ends_at` 为实际结束（提前结束的时刻或结束时间）；`updated_at` 为不带时区的 UTC |
  | `ScopeProduct` | `id`, `slug`, `name_en` | 规则显式选的一件商品，`name_en` 为空时 None |
  | `RuleDetail` | `rule_id` 加 `RuleInput` 的全部字段，再加 `products` | `product_ids` 与 `category_ids` 按 ID 从小到大，`products` 与 `product_ids` 一一对应；`as_input()` 返回 `RuleInput` |
  | `PromotionDetail` | `id`, `version`, `state`, `ended_early_at`, `PromotionInput` 的全部字段（`rules` 为 `RuleDetail` 元组）, `poster_desktop_url`, `poster_phone_url` | `ended_early_at` 为马来西亚本地时刻或 None；海报路径为 `/api/promotion-posters/<文件名>.webp`（SHOP-TASK-250 提供）或 None；`as_input()` 返回原样提交即「没有改动」的 `PromotionInput` |

- [x] 公开函数：
  - `list_promotions(db, now) -> list[PromotionSummary]`：按 running、scheduled（开始从早到晚）、draft（`updated_at` 从晚到早）、ended（实际结束从晚到早）排列，同序按 ID 从大到小。只发 SELECT。
  - `read_promotion(db, promotion_id, now) -> PromotionDetail`：不存在抛 `PromotionNotFound`。只发 SELECT。
  - `create_promotion(db, admin_account_id, data, now) -> PromotionDetail`：`check_promotion_input` 之后回滚并查库：商品与分类须存在、商品级规则不重叠；草稿不查发布条件与时间重叠，不锁 `promotion_publish_locks`。写入活动（状态 draft、版本 1、创建与修改时间为 `now`）、一行装饰与规则，写一条审计后提交。
  - `save_promotion(db, admin_account_id, promotion_id, version, data, now) -> PromotionDetail`：顺序见下面的落实写法。
  - 供 241 复用：`publish_problems(db, promotion, now) -> list[PromotionProblem]`（按已保存的内容返回缺的发布条件，不查时间重叠，不加锁）；`scope_overlap_problems(db, promotion_id) -> list[PromotionProblem]`（按当时的商品与分类返回已保存的商品级规则之间的重叠）；`ensure_no_overlap(db, promotion_id, starts_at, effective_end) -> None`（与该活动以外的已发布活动比较，相接可以，重叠抛 `PromotionOverlap`；对方取开始最早、同时 ID 最小的一个；调用方须已锁发布锁那一行）；`lock_publish_row(db) -> None`（`SELECT … FOR UPDATE` 锁住 `promotion_publish_locks` 那一行，表里没有那一行抛 RuntimeError）。
  - 常量：`ADMIN_PROMOTION_CREATED`（admin_promotion_created）、`ADMIN_PROMOTION_CHANGED`（admin_promotion_changed）、`AUDIT_TARGET_PROMOTION`（promotion）、`NEW_PROMOTION_VERSION`、`AUDIT_TEXT_LENGTH`（100）、`POSTER_PATH_PREFIX`、`POSTER_PATH_SUFFIX`。
- [x] `save_promotion` 的落实写法：
  1. 先不碰数据库：`check_promotion_input`，`version` 须为 int。
  2. 回滚，以 `lock_publish_row` 为新事务的第一条语句，再以 `FOR UPDATE` 锁活动行（不存在或版本不同抛 `PromotionChanged`），再锁装饰行；状态在加锁后按 `now` 算出。
  3. ended 抛 `PromotionInvalid`（ended_readonly）。scheduled 时以 `with_for_update(read=True)` 查 `orders.promotion_id`，已有订单引用即按 running 处理。running：请求里的时间、两个开关与规则须与当前相同，否则 rules_locked。draft 与 scheduled：引用存在与商品级规则不重叠。已发布（scheduled 与 running）的保存后内容须满足发布条件；按 running 处理时规则条数与时间取当前值（它们不能改，只可能缺英文标题或上传图海报的图与替代文字）。全部问题一起抛出。
  4. scheduled（未按 running 处理时）调用 `ensure_no_overlap`。
  5. 按内部名称、开始、结束、两个开关、规则、装饰九项、文案十五项的顺序与当前值比较。有改动的写入，规则有改动时整批替换（先删范围、满额档与规则，再按新顺序写入）。版本加一，活动的 `updated_at` 取 `now`（装饰有改动时装饰的 `updated_at` 也取 `now`），每项一条 `admin_promotion_changed` 审计后提交；都没有改动时回滚，返回当前内容。
  任何异常都先回滚再抛出。
- [x] 规则比较口径（`_rule_key`）：条数与顺序相同，每条的类别与七个参数相同，商品 ID 与分类 ID 作为集合相同，满额档按顺序逐档（门槛、固定金额、百分比）相同。相同即不替换规则行，规则 ID 不变，也不算改动。
- [x] 重叠口径（`_overlap_problems`）：每条商品级规则的商品为显式选的商品（不论是否上架），加上所选分类当前下属且满足 `app.services.catalog.published()` 的商品。只在新建、草稿与未开始活动的保存时检查（241 的发布另调 `scope_overlap_problems`）。
- [x] 审计：`target_type` 为 promotion，`target_id` 为活动 ID。新建写 `admin_promotion_created`，新值为内部名称。保存时每个改动的字段写一条 `admin_promotion_changed`，旧值与新值为「字段 值」：时间为马来西亚本地字符串，布尔为 true 或 false，空值只写字段名，文字取前 100 个字符，文案的字段名自带语言（如 `title_zh`）。规则只写一条，值为 `rules <条数>`。审计不含个人资料。
- [x] 测试 `tests/test_admin_promotions.py`（SQLite 内存库，StaticPool，打开外键检查），覆盖：引用不存在的商品与分类；按显式商品与按分类的重叠（带商品 ID 与名称），未上架商品只在显式选时算重叠；进行中的活动在商品改分类后仍能保存文案（`scope_overlap_problems` 同时报出这一重叠）；只调换范围顺序不算改动；running 改规则、时间或开关报 rules_locked，清空英文标题报 publish_missing，改名称、装饰与文案成功；ended 报 ended_readonly；已被订单引用的 scheduled 按 running 处理，没有订单的可以改规则；scheduled 保存时缺发布条件的六种情形与时间重叠（相接可以）；草稿不查发布条件与时间重叠；版本不符与不存在；没有改动不加版本；审计的条数与写法；列表的排序与字段；读取的规则商品、满额档、海报路径与提前结束时刻。每条测试的文档字符串写明守住的设计、UX 或落实写法原句。
- 偏离与落实细节：
  - 在验收之外另导出 `lock_publish_row`、`ScopeProduct`、`RuleDetail` 与两个 `as_input()`，供 241、242 与测试共用。
  - 活动的发布状态由 241 提供，测试里直接改状态列来造出 scheduled、running 与 ended 的活动。
  - 订单引用只在 scheduled 时查：草稿不会被订单引用（生效判定只读已发布的活动，241 不撤回已开始的活动）；running 本来就按 running 处理；ended 只读。
  - 保存时也锁装饰行（在活动行之后），这样发布条件读到的海报文件名不会过时。
  - 规则替换用 `synchronize_session="fetch"` 的批量删除：会话里可能还留着已过期的旧规则对象，新行重用同一 ID（SQLite 会重用）时不会撞上它们。
  - `updated_at` 与新建的 `created_at` 取传入的 `now`（不取模型默认的墙钟时间），列表「最近修改在前」因此与审计时间一致，测试也不依赖时钟。
  - 重叠问题的 `field` 取该商品第二次出现的那条规则 `rules.<序号>`；`product_name` 在英文名称为空时用 slug，免得页面没有可显示的名称。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 1–3、12 条、UX A12 的状态表，以及 `app/models/promotion.py`、`app/models/order.py` 的约束；手算核对测试里的时间换算，例如 UTC 2026-11-08 00:00 为马来西亚 08:00，UTC 2026-11-12 01:30:45 截到分钟为马来西亚 09:30。MySQL 上的加锁顺序与并发没有在 SQLite 里验证（SQLite 不生成 FOR UPDATE）。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。
