# SHOP-TASK-010 订单数据模型、迁移与状态迁移规则

- [x] 按 `docs/DESIGN.md` 1.9（提交 `361d8bf`）「数据模型」的 Order / OrderItem、OrderRecipient、PaymentAttempt / OrderEvent 三行及「订单与退款状态」「失败、并发与重试」「权限与资料保护」「资料保留」建立订单相关六张表：`app/models/order.py`（`app/models/__init__.py` 导出）、迁移 `alembic/versions/20260930_0006_orders.py`（revision `0006`，down_revision `0005`）、纯函数 `app/services/order_rules.py`；测试见 `tests/test_order_models.py` 与 `tests/test_order_rules.py`
- 六张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；金额一律 Integer 仙，积分一律 Integer 积分，1 积分抵 1 仙；时间一律不带时区的 UTC；检查约束只用比较、算术、LENGTH、LIKE、IN / NOT IN 与 IS NULL；订单及其子表的外键一律 ON DELETE RESTRICT）：
  - `orders`：`order_number` String(16)（唯一 `uq_orders_order_number`）、`status` String(32)、`subtotal_sen`、`coupon_discount_sen`、`points_redeemed`（积分数）、`shipping_fee_sen`、`total_sen`、`points_earned`、`shipping_zone_code` String(5)、`shipping_rate_version`、`idempotency_key` String(64)（唯一 `uq_orders_idempotency_key`）、`request_fingerprint` String(64)、`created_at`、`payment_expires_at`（非空）、`paid_at`（可空），除 `paid_at` 外全部非空。检查约束：
    - `ck_orders_order_number_length`：`LENGTH(order_number) = 16`
    - `ck_orders_status_valid`：`status IN ('awaiting_demo_payment', 'demo_paid', 'demo_packed', 'demo_shipped', 'demo_completed', 'demo_cancelled')`
    - `ck_orders_subtotal_sen_non_negative`、`ck_orders_coupon_discount_sen_non_negative`、`ck_orders_points_redeemed_non_negative`、`ck_orders_shipping_fee_sen_non_negative`、`ck_orders_total_sen_non_negative`、`ck_orders_points_earned_non_negative`：各列 `>= 0`
    - `ck_orders_discounts_within_subtotal`：`coupon_discount_sen + points_redeemed <= subtotal_sen`
    - `ck_orders_total_formula`：`total_sen = subtotal_sen - coupon_discount_sen - points_redeemed + shipping_fee_sen`
    - `ck_orders_total_covers_shipping`：`total_sen >= shipping_fee_sen`
    - `ck_orders_paid_status_has_paid_at`：`status NOT IN ('demo_paid', 'demo_packed', 'demo_shipped', 'demo_completed') OR paid_at IS NOT NULL`
    - `ck_orders_unpaid_status_no_paid_at`：`status NOT IN ('awaiting_demo_payment', 'demo_cancelled') OR paid_at IS NULL`
    - `ck_orders_idempotency_key_not_empty`：`LENGTH(idempotency_key) >= 1`
    - `ck_orders_request_fingerprint_length`：`LENGTH(request_fingerprint) = 64`
  - `order_items`：`order_id` → `orders.id`（RESTRICT）、`line_index`（同一订单内唯一 `uq_order_items_order_id_line_index`）、`variant_id` → `product_variants.id`（可空，ON DELETE SET NULL）、`sku` String(64)、`product_name_en/zh/ms` String(200)、`variant_label_en/zh/ms` String(300)（均非空，下单时已按回退英文取好，无规格商品存空串）、`unit_price_sen`、`quantity`、`line_subtotal_sen`。检查约束：`ck_order_items_line_index_non_negative`（`line_index >= 0`）、`ck_order_items_unit_price_sen_non_negative`（`unit_price_sen >= 0`）、`ck_order_items_quantity_positive`（`quantity >= 1`）、`ck_order_items_line_subtotal_formula`（`line_subtotal_sen = unit_price_sen * quantity`）。
  - `order_item_units`（逐件分摊快照，对应 `UnitAllocation`：行序由所属订单行给出，`unit_index`、`original_price` → `original_price_sen`、`coupon_discount` → `coupon_discount_sen`、`points_discount`、`cash_paid` → `cash_paid_sen`、`points_earned`）：`order_item_id` → `order_items.id`（RESTRICT），`uq_order_item_units_order_item_id_unit_index`。检查约束：`ck_order_item_units_unit_index_non_negative`、`ck_order_item_units_original_price_sen_non_negative`、`ck_order_item_units_coupon_discount_sen_non_negative`、`ck_order_item_units_points_discount_non_negative`、`ck_order_item_units_cash_paid_sen_non_negative`、`ck_order_item_units_points_earned_non_negative`（各列 `>= 0`）、`ck_order_item_units_cash_paid_formula`（`cash_paid_sen = original_price_sen - coupon_discount_sen - points_discount`）。
  - `order_recipients`：`order_id` → `orders.id`（RESTRICT，唯一 `uq_order_recipients_order_id`，每张订单恰好一条）、`name` String(200)、`phone` String(16)（普通索引 `ix_order_recipients_phone`）、`country_code` String(2)、`region` String(100)（可空）、`address` String(500)、`postal_code` String(20)；除 `region` 外全部非空，不加其他个人资料字段，不加密、不设到期删除或匿名化。检查约束：`ck_order_recipients_phone_format`（`phone LIKE '+%' AND LENGTH(phone) <= 16`）、`ck_order_recipients_country_code_length`（`LENGTH(country_code) = 2`）、`ck_order_recipients_region_matches_country`（`country_code <> 'MY' OR (region IS NOT NULL AND LENGTH(region) = 5 AND region LIKE 'MY-%')`）。
  - `payment_attempts`：`order_id` → `orders.id`（RESTRICT）、`method` String(20)、`result` String(10)、`idempotency_key` String(64)（唯一 `uq_payment_attempts_idempotency_key`）、`request_fingerprint` String(64)、`created_at`，全部非空；不存卡号或账户资料。检查约束：`ck_payment_attempts_method_valid`（`method IN ('demo_card', 'demo_bank', 'demo_ewallet')`，对应 `pay.method_card`、`pay.method_bank`、`pay.method_ewallet`）、`ck_payment_attempts_result_valid`（`result IN ('succeeded', 'failed')`）、`ck_payment_attempts_idempotency_key_not_empty`、`ck_payment_attempts_request_fingerprint_length`（同订单表）。
  - `order_events`：`order_id` → `orders.id`（RESTRICT）、`from_status`（可空）、`to_status`、`actor_type` String(10)、`created_at`，没有任何自由文本列。检查约束：`ck_order_events_from_status_valid`（`from_status IS NULL OR from_status IN (六种状态)`）、`ck_order_events_to_status_valid`（`to_status IN (六种状态)`）、`ck_order_events_placement_has_no_from_status`（`(from_status IS NULL AND to_status = 'awaiting_demo_payment') OR (from_status IS NOT NULL AND to_status <> 'awaiting_demo_payment')`）、`ck_order_events_actor_type_valid`（`actor_type IN ('guest', 'member', 'admin', 'system')`）。
  - 0006 的 upgrade 依次建 `orders`、`order_items`、`order_item_units`、`order_recipients`（及电话索引）、`payment_attempts`、`order_events`；downgrade 按依赖倒序删除。
- 订单号格式：`secrets.randbits(80)` 编码为 16 位 Crockford Base32 大写字符（字母表 `0123456789ABCDEFGHJKMNPQRSTVWXYZ`，不含 I、L、O、U），不含分隔符；`generate_order_number()` 生成，`is_valid_order_number(value)` 只接受恰好 16 位且每位都在字母表里的字符串，不做任何规范化。显示分组与查单输入规范化不在本任务。
- 状态迁移表（`is_transition_allowed(current, target, actor)`，其余一律拒绝，包括原地迁移、倒退、跨级、已支付订单取消、未知状态或操作者）：
  - `awaiting_demo_payment` → `demo_paid`：guest、member
  - `awaiting_demo_payment` → `demo_cancelled`：guest、member、system
  - `demo_paid` → `demo_packed`：admin
  - `demo_packed` → `demo_shipped`：admin
  - `demo_shipped` → `demo_completed`：guest、member、system
  - 模拟支付失败不是状态迁移，只记一条 `result = 'failed'` 的支付尝试；全部退款后冻结履约由之后的退款任务扩展，本任务不预留参数。
- 偏离：未改设计。说明几处取舍，请审阅：
  - 设计闸门写的是 DESIGN 1.9（提交 `361d8bf`）；仓库里现为 1.11 候批稿（1.10 已批准）。1.10、1.11 只改了 `Product` / `Variant` 一行、新增 `SiteSetting` 一行及短信验证开关相关段落，本任务用到的 Order / OrderItem、OrderRecipient、PaymentAttempt / OrderEvent 三行与「订单与退款状态」「资料保留」各版相同，未发现冲突。
  - 设计的 Order 写有「可选会员 ID」，按验收标准本任务不加，由会员任务另加；券 ID 与参考外币快照同样不加。
  - 验收标准之外另加的约束：订单号长度 16、收货国家代码长 2、行序与件序不小于 0、事件前后状态限于六种、下单事件（且只有下单事件）的迁移前状态为空、支付结果限于两种。它们都由设计原句直接推出，不限制设计允许的数据。
  - 幂等键在库里只保证非空（`LENGTH >= 1`），不超过 64 个字符由 String(64) 列长保证：MySQL 的 LENGTH 按字节计，写 `<= 64` 会把 64 个多字节字符误拒。SQLite 不检查列长。请求指纹库里只保证长 64，是否全为十六进制（SHA-256）由之后的下单与支付任务写入时校验；指纹怎样从请求算出、同键同请求返回原结果、同键不同内容报冲突，也由它们在事务内按键查询、比对指纹实现，唯一约束处理并发。
  - 应付不低于运费、券折扣加积分抵扣不超过商品小计、商品小计与应付不小于零这几条是其他约束的推论：违反它们的数据必然同时违反另一条约束。按验收标准仍逐条写出。
  - MySQL 默认排序规则不区分大小写：状态、操作者类别、支付方式与 `MY` 的比较在 MySQL 上不区分大小写（如 `'DEMO_PAID'` 能通过检查约束），大小写由写入方保证；状态迁移判定函数区分大小写。
  - 整单金额与逐件分摊合计一致（各件原价之和等于商品小计、分摊券额与积分额之和等于整单券折扣与积分抵扣、逐件获得积分之和等于整单获得积分）跨行，不能用单行检查约束表达，由之后的下单任务写入时保证。
  - 邮编非空，没有邮编的国家由写入方存空串；规格说明对无规格商品存空串。电话是否为合法 E.164 由之后的下单接口按 DESIGN 1.9 校验，本任务不固化任何电话解析规则。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并逐个核对 0006 的列类型、可空性、约束名与表达式、外键删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_order_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，先写入设计第 4 条算例的整张订单及其各子表作对照，再覆盖：每个检查约束至少一个反例（先在独立连接上对该行逐条求值全部检查约束、断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一），含应付不等于公式、应付低于运费、折扣超过小计、各金额与积分为负、未知状态、待支付或已取消却有支付时间、已支付或已发货却无支付时间、订单号长度不对、幂等键与请求指纹为空、行小计不等于单价乘件数、件数 0、现金实付不等于公式、马来西亚的地区为空或不是州属代码、电话不以加号开头或超过 16 个字符、未知支付方式与结果、未知操作者类别与状态、下单事件带迁移前状态；支付到期时间、幂等键与请求指纹为 NULL 被拒；重复订单号、订单与支付的重复幂等键、同一订单两条收货资料、同一订单内重复行序、同一订单行内重复件序被唯一约束拒绝；其他国家的地区为空或自由文本可写入；删除有订单行、收货资料、支付尝试或事件的订单与删除有逐件分摊的订单行被外键拒绝，子行引用不存在的父行被拒；删除被订单行引用的 SKU 规格后订单行保留、规格外键为空、快照不变。`tests/test_order_rules.py` 覆盖字母表、订单号长度与字符集、不含 I、L、O、U 与分隔符、20000 个不重复、每一位取遍字母表、格式校验接受合法与拒绝小写、分隔符、长度不对、禁用字符、全角与非字符串；五条迁移逐一允许其操作者，六种状态 × 六种状态 × 四种操作者的其余组合穷举被拒，另逐一点名原地、倒退、跨级、已支付取消与错误操作者，以及未知状态与大小写不同的值。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.9「数据模型」的 Order / OrderItem、OrderRecipient、PaymentAttempt / OrderEvent 三行及「订单与退款状态」「失败、并发与重试」「权限与资料保护」「资料保留」一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/order.py 定义六个模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项；app/models/__init__.py 导出它们；所有外键的删除行为写明，订单及其子表的外键一律 RESTRICT（订单与收货资料长期保存，不级联删除）；金额一律整数仙，积分一律整数积分，不用浮点或 Decimal；时间一律不带时区的 UTC
      - 订单表：订单号（16 位、全表唯一）、状态（只允许 awaiting_demo_payment、demo_paid、demo_packed、demo_shipped、demo_completed、demo_cancelled 六种）、商品小计、券折扣、积分抵扣（积分数，1 积分抵 1 仙）、运费、应付、获得积分、所用运费区代码与运费版本号、幂等键（1 到 64 个字符，非空，全表唯一）与请求指纹（非空，64 位十六进制的 SHA-256）、创建时间、支付到期时间（非空，由之后的下单任务按设计写为创建时间加 15 分钟，超时取消与支付页倒计时都以它为准）与支付时间；检查约束保证各金额与积分不小于零、券折扣加积分抵扣不超过商品小计、应付等于商品小计减券折扣减积分抵扣加运费、应付不低于运费、支付时间在 demo_paid、demo_packed、demo_shipped、demo_completed 时非空而在 awaiting_demo_payment、demo_cancelled 时为空；不加会员 ID、券 ID 或参考外币快照列（参考外币只在结账页显示，见 docs/UX.md Q9）
      - 订单行表：所属订单、行序（同一订单内唯一）、SKU 规格外键（可空，规格被删除时置空）与 SKU 快照、商品名称与所选规格说明的英文、中文、马来文快照（下单时已按回退英文的规则取好，非空）、单价、件数（不小于 1）与行小计；检查约束保证单价不小于零、行小计等于单价乘件数
      - 逐件分摊快照表：所属订单行、件序（同一订单行内唯一）、原价、分摊券额、分摊积分额、现金实付与获得积分，字段与 app/services/pricing.py 的 UnitAllocation 一一对应；检查约束保证各值不小于零且现金实付等于原价减分摊券额减分摊积分额；整单与逐件合计一致由之后的下单任务写入时保证，本任务在 PR 里说明
      - 收货资料表：每张订单恰好一条（订单外键唯一），字段只有设计「边界与原则」列出的姓名、规范化 E.164 电话、国家代码、地区、地址与邮编，不加任何其他个人资料字段；地区只有一列：国家为马来西亚时必须是长 5 位、以 MY- 开头的州属代码，其他国家时为可空的自由文本；电话建普通索引供查单；检查约束只保证电话以加号开头且不超过 16 个字符，是否为合法 E.164（只含数字、区号与长度）由之后的下单接口按获批的 DESIGN 1.9 校验，本任务不固化任何电话解析规则；不做应用层加密，不设到期删除或匿名化
      - 模拟支付尝试表：所属订单、支付方式（只允许演示信用卡/借记卡、演示网上银行、演示电子钱包三种，对应 docs/UX-COPY.md 的 pay.method_card、pay.method_bank、pay.method_ewallet）、结果（成功或失败）、幂等键（1 到 64 个字符，非空，全表唯一）与请求指纹（非空，64 位十六进制）、创建时间；不存任何卡号或账户资料。请求指纹怎样从请求算出、同键同请求返回原结果、同键不同内容报冲突，由之后的下单与支付任务在事务内按键查询、比对指纹实现，并以唯一约束处理并发，本任务只建列
      - 订单事件表：所属订单、迁移前状态（下单事件为空）、迁移后状态、操作者类别（guest、member、admin、system 四种）、创建时间；不设任何自由文本列，事件不写收货资料原文
      - 新迁移文件 alembic/versions/20260930_0006_orders.py，revision 为 0006、down_revision 为 0005，upgrade 建六张表，downgrade 按依赖倒序删除；迁移按命名约定逐个写出约束名；检查约束只用 SQLite 与 MySQL 都支持的表达式（比较、算术、LENGTH、LIKE、IN、IS NULL），PR 正文逐表列出检查约束与表选项，供审查核对与模型一致
      - app/services/order_rules.py 提供订单号生成：用 secrets 取 80 位随机数，编码为 16 位 Crockford Base32 大写字符（不含 I、L、O、U），不含分隔符；另提供校验一个字符串是否为合法订单号格式的函数；显示时的分组与查单时的输入规范化不在本任务
      - 同一文件提供状态迁移判定纯函数：接收当前状态、目标状态与操作者类别，只允许：awaiting_demo_payment 到 demo_paid（guest、member）、awaiting_demo_payment 到 demo_cancelled（guest、member、system）、demo_paid 到 demo_packed（admin）、demo_packed 到 demo_shipped（admin）、demo_shipped 到 demo_completed（guest、member、system）；其余一律拒绝，包括原地迁移、倒退、跨级与已支付订单取消；模拟支付失败不是状态迁移；全部退款后冻结履约由之后的退款任务扩展，本任务不预留参数
      - tests/test_order_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入一张合法订单及其各子表作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（含应付不等于公式、应付低于运费、折扣超过小计、未支付却有支付时间、已支付却无支付时间、未知状态、行小计不等于单价乘件数、件数 0、现金实付不等于公式、同一订单两条收货资料、马来西亚的地区为空或不是州属代码、电话不以加号开头、电话超过 16 个字符、幂等键或请求指纹为空、支付到期时间为空、未知支付方式、未知操作者类别、重复订单号、重复幂等键），并验证其他国家的地区为空或为自由文本都可写入、删除有子行的订单被外键拒绝、删除被订单行引用的 SKU 规格后订单行保留且规格外键为空；每条测试写明它守住的是设计里哪一句
      - tests/test_order_rules.py 验证订单号长度、字符集、不含 I、L、O、U 与分隔符、大量生成不重复、格式校验接受合法与拒绝非法（小写、含分隔符、长度不对、含禁用字符）；状态迁移逐一验证五条允许的迁移与其允许的操作者，并穷举其余状态、目标与操作者组合全部被拒；每条测试写明它守住的是设计里哪一句
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异；以上测试与已有后端测试都由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-010 记录段（六张表与关键约束、订单号格式、状态迁移表、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.9，提交 361d8bf），不合并、不直推
```
