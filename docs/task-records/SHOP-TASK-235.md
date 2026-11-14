# SHOP-TASK-235 游客下单接入节日促销活动（订单与逐件快照）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 4、7、14 条：订单金额快照与逐件分摊，不改收货资料的处理。依赖 SHOP-TASK-230（订单列）、232（`price_order` 的 `promotion_discounts`）、233（`active_promotion`、`price_lines`）与 234（`quote_cart` 的 `promotion`）。不改接口、会员下单、计价服务、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。游客没有券与积分，不做第 6 条的比较。
- [x] `app/services/ordering.py` 的 `place_guest_order`：
  - 在短信降级判定之后、调用 `quote_cart` 之前以 `active_promotion(db, now, share_lock=True)` 读生效活动（共享锁，与后台撤回、保存的排他锁互相等待），以 `promotion=` 传入 `quote_cart`。
  - 本单参加活动（`quote.promotion` 不为 None 且 `participates` 为 True）时，在扣库存之后以 `price_lines` 对全部行重算每件的活动优惠：`PromoInputLine` 的行序为请求顺序，单价与件数取计价结果，商品 ID 与分类 ID 取 `purchasable` 查到的商品，运费为 `quote.shipping.fee_sen`。展开后每件的 `promotion_discount` 按同一展开顺序传给 `price_order` 的 `promotion_discounts`。
  - 订单：`shipping_fee_sen` = 运费报价 − 免去的运费，`shipping_waived_sen` = 免去的运费，`total_sen` = `pricing.cash_total` + 实际运费，`promotion_id` 为该活动，`promotion_item_discount_sen` 与 `promotion_order_discount_sen` 为 `PromotionPricing` 的两项合计。
  - 逐件：`promotion_discount_sen` 为该件的活动优惠（商品级折扣加分摊到的整单级优惠），`promotion_rule_id` 为该件所在行命中的商品级规则（不论这一件是否享受折扣），`cash_paid_sen` 为 `price_order` 给出的原价减活动优惠。
  - 不参加活动（没有生效活动，或所有规则都没带来优惠）时显式写 `promotion_id` 为空、三项活动金额与逐件活动优惠为 0、规则为空；运费与应付与之前相同（`cash_total` 此时等于小计）。
  - 按幂等键重放、同键冲突、不可下单与扣库存失败后的回滚重查都在读活动之前或不经过它，规则不变；请求指纹不变；`OrderSummary` 字段不变（运费与应付即订单的实际运费与应付）。
- [x] 测试：新文件 `tests/test_guest_order_promotion.py`（SQLite 内存库，打开外键检查），服务层直接调用 `place_guest_order`，以第 8 条算例的商品与规则下游客订单（西马运费 800）：
  - 订单商品级 1900、整单级 500、实际运费 0、免去 800、应付 5900，摘要同订单；
  - 五件活动优惠 656、656、94、94、900，现金实付 1844、1844、1106、1106、0，命中规则依次为 A 的打折规则 ×2、家居的买 2 享 1 ×3；
  - 没有活动、活动未开始、活动已结束三种情况下订单不记活动，运费 800、应付 9100，逐件活动优惠 0、规则为空；
  - 只买 B 一件（在买 2 享 1 范围内但没有任何优惠）时订单不记活动、这一件不记规则；
  - 访客计价时活动生效（合计 5900），之后活动提前结束，下单不记活动；
  - 下单后活动提前结束，订单与逐件快照不变；同键重放返回原订单摘要，不调用 `active_promotion` 与 `quote_cart`，不再扣库存。
  - 每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - 每件的活动优惠没有从 `quote_cart` 的结果取（`PricedLine` 只有行合计），而是按运营者写法以 `price_lines` 在同一事务、同一活动对象上重算；输入与 `quote_cart` 内的正常行相同（能下单时所有行都是正常行），结果一致。
  - `tests/test_guest_order.py` 未改：其中没有生效活动，下单结果与之前相同。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 4、7、8、14 条，手算核对测试里的数字（如 A：2500 − 500 − 156 = 1844；B：1200 − 94 = 1106）。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 订单——参加活动时 Order 的 promotion_id 为该活动、promotion_item_discount_sen 与 promotion_order_discount_sen 为两项合计、shipping_fee_sen 为实际运费、shipping_waived_sen 为免去的运费、total_sen 为现金实付合计加实际运费；不参加活动（没有生效活动，或所有规则都没带来优惠）时 promotion_id 为空、三项为 0，其余与现在相同
      - 逐件——参加活动时每件 OrderItemUnit 的 promotion_discount_sen 为该件的活动优惠（商品级折扣加分摊到的整单级优惠），promotion_rule_id 为该件所在行命中的商品级规则（不论这一件是否享受折扣，没有命中为空），cash_paid_sen 为原价减活动优惠；不参加活动时两列为 0 与空
      - 时点——活动以下单事务里读到的为准（第 14 条）：访客计价后活动结束或提前结束，下单按不参加活动；下单后活动结束不改已下单订单；按幂等键重放原订单不重新计价、不重读活动
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_guest_order_promotion.py 以第 8 条算例的商品与规则下游客订单（西马运费 800，免运费）：订单商品级 1900、整单级 500、实际运费 0、免去 800、应付 5900；五件的活动优惠 656、656、94、94、900，现金实付 1844、1844、1106、1106、0，命中规则依次为 A 的打折规则、A 的打折规则、家居的买 X 享 Y、同前、同前；没有生效活动、活动未开始与已结束、所有规则都没带来优惠时订单不记活动且金额与现在相同；计价后活动提前结束则下单不记活动；重放原订单返回原快照。每条测试的文档字符串写明它守住的第几条哪一句
      - docs/task-records/SHOP-TASK-235.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改接口、会员下单、计价服务、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
