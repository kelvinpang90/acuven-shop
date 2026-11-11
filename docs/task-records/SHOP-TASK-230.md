# SHOP-TASK-230 订单记录活动优惠的列与约束（迁移 0028）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「数据模型」的 Order / OrderItem 一行与「节日促销活动」第 7 条（第 4 条末句一并）。只改订单金额快照的结构与约束，不涉及个人资料；下单时怎样写这些列由之后的下单任务做。不改下单、计价、退款与展示的服务与接口、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。依赖 SHOP-TASK-229 建的 `promotions` 与 `promotion_rules`。
- [x] 模型 `app/models/order.py`，六个新列（供之后的下单任务引用）：

  | 表 | 列 | 类型 | 说明 |
  | --- | --- | --- | --- |
  | `orders` | `promotion_id` | 可空 Integer，外键 `promotions.id` ON DELETE RESTRICT（`fk_orders_promotion_id_promotions`），索引 `ix_orders_promotion_id` | 本单所用的活动；不参加活动或取了乙方案时为空 |
  | `orders` | `promotion_item_discount_sen` | 非空 Integer，`server_default` 0 | 商品级活动优惠合计 |
  | `orders` | `promotion_order_discount_sen` | 非空 Integer，`server_default` 0 | 整单级活动优惠合计 |
  | `orders` | `shipping_waived_sen` | 非空 Integer，`server_default` 0 | 免去的运费（未免为 0）；实际运费仍是 `shipping_fee_sen`，免运费时为 0 |
  | `order_item_units` | `promotion_discount_sen` | 非空 Integer，`server_default` 0 | 该件的活动优惠（商品级折扣 + 分摊到的整单级优惠），对应之后 `UnitAllocation` 的活动优惠 |
  | `order_item_units` | `promotion_rule_id` | 可空 Integer，外键 `promotion_rules.id` ON DELETE RESTRICT（`fk_order_item_units_promotion_rule_id_promotion_rules`） | 该件所在行命中的商品级规则，不论这一件是否享受折扣；不参加活动时为空 |

- [x] 新检查约束（全名按 `app/db/base.py` 的命名约定）：
  - `ck_orders_promotion_item_discount_sen_non_negative`：`promotion_item_discount_sen >= 0`
  - `ck_orders_promotion_order_discount_sen_non_negative`：`promotion_order_discount_sen >= 0`
  - `ck_orders_shipping_waived_sen_non_negative`：`shipping_waived_sen >= 0`
  - `ck_orders_promotion_amounts_match`：活动 ID 为空且三项活动金额都为 0，或活动 ID 不为空且三项之和大于 0（第 4 条末句「所有规则都没有带来优惠时不记活动」与第 7 条「不参加活动或取了乙方案时为空」合起来）
  - `ck_orders_shipping_waived_means_free`：`shipping_waived_sen = 0 OR shipping_fee_sen = 0`
  - `ck_order_item_units_promotion_discount_sen_non_negative`：`promotion_discount_sen >= 0`
- [x] 改写的约束（约束名不变）：
  - `ck_orders_discounts_within_subtotal`：`promotion_item_discount_sen + promotion_order_discount_sen + coupon_discount_sen + points_redeemed <= subtotal_sen`
  - `ck_orders_total_formula`：`total_sen = subtotal_sen - promotion_item_discount_sen - promotion_order_discount_sen - coupon_discount_sen - points_redeemed + shipping_fee_sen`
  - `ck_order_item_units_cash_paid_formula`：`cash_paid_sen = original_price_sen - promotion_discount_sen - coupon_discount_sen - points_discount`
  - `ck_orders_total_covers_shipping` 与其余约束不变；`Order` 文档字符串的应付公式已随之更新。
- [x] 迁移 `alembic/versions/20261110_0028_order_promotion.py`（revision 0028，down_revision 0027）：照 0024、0025 不 import app，新约束与改写约束的文本自带为 `NEW_CHECK_CONSTRAINTS`、`REWRITTEN_CHECK_CONSTRAINTS`（改写约束另存 0006 的旧文本），与模型逐字相同。upgrade 先加列（已有行金额列取 0、外键列为空），再建索引、外键与新约束，最后删掉三条旧约束、按新表达式重建同名约束；已有金额不改写。downgrade 在第一条 DDL 之前读出带活动的订单数与带活动优惠或规则的逐件行数，任一大于 0 即抛 RuntimeError、库保持原样；否则先把三条改写约束换回旧表达式，再删新约束、外键与索引，最后删列。
- [x] 测试：`tests/test_order_models.py` 的订单与逐件行值带上新列（取 0 与空），原有断言不变；新增第 8 条算例整单与五件的正例、各新约束与改写约束的正例与反例（含活动 ID 为空而有活动金额、活动 ID 不为空而三项都为 0、免去运费而实际运费不为 0）、两个外键拒绝不存在的活动与规则、被订单或逐件引用的活动与规则不能删除、不写新列时取 0 与空、列的可空性与服务端默认值与索引，以及经 importlib 载入迁移核对 revision、down_revision 与约束常量。`tests/test_member_models.py` 只为订单行值带上新列而改。每条新增测试的文档字符串写明它守住的第 7 条原句。
- 偏离与落实细节：
  - 改写的 `discounts_within_subtotal` 无法单独不成立：应付公式成立时，优惠合计超过商品小计必然使应付低于运费，所以它的反例同时违反 `total_covers_shipping`，测试按「目标约束加其推论」断言；反例取券与积分未超过商品小计、加上活动优惠后才超过，旧表达式会放行。
  - 正例另用了「计价、优惠、积分与库存」第 4 条算例的订单为底，分别只带满额免运费、只带整单级优惠、只带商品级折扣，以及优惠合计恰好等于商品小计的订单。
  - `promotion_rule_id` 不另建索引（验收未要求）；MySQL 会为外键自动建同名索引。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 4、7、8 条，人工核对迁移常量与模型文本一致并由测试逐字比对，算例的各项金额已逐项核算。迁移在 MySQL 8 上的往返与 alembic check、全部后端测试由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。
