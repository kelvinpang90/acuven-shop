# SHOP-TASK-237 会员下单接入节日促销活动（按取更优的方案写订单）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 6、7、14 条：订单金额快照、券与积分的预占，不改收货资料的处理。依赖 SHOP-TASK-230（订单列）、233（`active_promotion`、`price_lines`）、235（游客下单的写法）与 236（`decide_member_promotion`）。不改接口、计价接口、退款、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `app/services/member_ordering.py` 的 `_create_order`（`place_member_order` 的下单事务）：
  - 锁会员行之后、调用 `quote_cart` 之前以 `active_promotion(db, now, share_lock=True)` 读生效活动（共享锁，同 SHOP-TASK-235），以 `promotion=` 传入 `quote_cart`。锁顺序为会员 → 活动（共享）→ 库存 → 订单 → 券 → 积分。
  - 券代码照旧先规范化、以原价小计 `check_coupon`；不成立即抛 `CouponUnavailable`（不论方案是否用券，同计价「以原价小计查一次」）。
  - 本单参加活动（`quote.promotion.participates` 为 True）时照 SHOP-TASK-235 以请求行序、计价单价与件数、`purchasable` 查到的商品与分类组成 `PromoInputLine`，与查券结果、所填积分一起交给 `decide_member_promotion`（运费为未免的运费报价），得到与计价相同的方案。不参加时传入 None，方案为空、券与积分照没有活动时使用。
  - 券只在 `use_coupon` 为 True 时计入 `price_order` 并预占；`reserve_coupon` 的 `subtotal_sen` 传活动后商品额（`subtotal − promotion_discount`），按它判断最低消费；不取甲时活动优惠为 0，即原价小计，与之前相同。
  - 积分只在 `points_allowed` 为 True 时校验（超过可用余额或活动与券之后的商品额即 `PointsUnavailable`）并预占；为 False 时按 0 积分计价、不校验、不预占，照常下单。
  - 订单：`shipping_fee_sen` = 运费报价 − 免去的运费，`shipping_waived_sen` = 免去的运费，`total_sen` = `cash_total` + 实际运费；取甲时 `promotion_id` 为该活动、两项合计取 `PromotionPricing`，取乙或不参加活动时为空与 0。逐件：`promotion_discount_sen` 为该件活动优惠，`promotion_rule_id` 为该行命中的商品级规则（不论这一件是否享受折扣），不取甲时为 0 与空。
  - 请求指纹、`MemberOrderSummary` 字段、幂等重放（在读活动之前返回，不重新计价）、同键冲突与回滚重查都不变。
- [x] 测试：新文件 `tests/test_member_order_promotion.py`（SQLite 内存库，打开外键检查，引擎设置同 `tests/test_refund_review_points.py` 以支持退款审核的保存点），服务层直接调用 `place_member_order`：
  - 第 8 条首例：商品级 1900、整单级 500、券 590、积分 1000、实际运费 0、免去 800、应付 4310、获得 43；用券记录预占 590；
  - 五件（原价、活动优惠、券、积分、现金实付、获得积分、规则）与 SHOP-TASK-232 的数字相同：656/184/313/1347/14、656/184/313/1347/13、94/111/187/808/8 ×2、900/0/0/0/0；
  - 模拟支付成功后本单批次 43 积分，用券记录核销；
  - 第 9 条只退第一件 B：经现有 `submit_refund` 申请金额 808，经现有 `approve_refund` 批准返还 187、追回 8；
  - 关闭券同用：订单记活动、券 0、应付 4900，没有用券记录，每人限用一次的券仍可再用；
  - 只关闭积分同用：券 590 照常预占、积分 0、应付 5310、获得 53，可用余额不变；所填积分超过余额也照常下单；
  - D 例与第三例：订单不记活动、运费 800，券 3000 与 5000 照常预占，应付 17800 与 5800；
  - 券代码查不到时即使甲不用券也抛 `CouponUnavailable`；取甲且同用积分时所填积分超过余额抛 `PointsUnavailable`，都不建订单；
  - 计价后活动提前结束：订单不记活动，应付 7270；计价后关闭券同用：订单取甲而不用券；
  - 下单后活动提前结束，同键重放返回原订单，不调用 `active_promotion` 与 `quote_cart`，快照、用券记录与积分预占不变；
  - 每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - 验收「券或积分本身不成立时照现在抛」与「积分按方案不用时照常下单（不抛 PointsUnavailable）」：按方案不用积分时不校验余额（与计价在活动不与积分同用时「照样接受、不计入」一致）；券本身不成立（代码不合规、查不到、不可用）不论方案都拒绝。
  - 「开关被改」的测试直接改已开始活动的开关行，只用来模拟计价与下单之间读到的活动不同；后台本身不允许改已开始活动的开关（第 2 条）。
  - `tests/test_member_ordering.py` 未改：其中没有生效活动，下单结果与之前相同。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 6、7、8、9、14 条，手算核对测试里的数字（如 B：1200 − 94 − 111 − 187 = 808；只关闭积分同用时 5900 − 590 = 5310、获得 53）。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 方案——下单事务里以 active_promotion(db, now, share_lock=True) 读活动（共享锁，同 SHOP-TASK-235），按 236 的方案判定函数得到与计价相同的结果；取甲时订单记活动与两项合计、实际运费与免去的运费，逐件记活动优惠与命中规则，券与积分按方案使用；取乙或不参加活动时订单不记活动，金额与现在相同
      - 券与积分——取甲且 use_coupon 为 True 时以活动后商品额调用 reserve_coupon、券折扣按活动后的金额计算；按方案不用的券不写用券记录、不占用次数；按方案不用的积分不预占；券或积分本身不成立时照现在抛 CouponUnavailable 或 PointsUnavailable、不建订单
      - 时点——计价后活动结束、提前结束或开关被改都以下单事务读到的为准；按幂等键重放原订单不重新计价
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_member_order_promotion.py 照第 8 条：首例下单后订单商品级 1900、整单级 500、券 590、积分 1000、实际运费 0、免去 800、应付 4310，五件的活动优惠、券、积分、现金实付与 SHOP-TASK-232 测试的数字相同，支付成功后获得 43 积分按 14、13、8、8、0 分摊；关闭券同用时不写用券记录、券仍可再用；D 例与第三例订单不记活动、券照常预占；只关闭积分同用时积分不预占；第 9 条只退第一件 B 时模拟退现金 808、返还 187 积分、追回 8 积分（沿用现有退款服务，不改它）。每条测试的文档字符串写明它守住的第几条哪一句
      - docs/task-records/SHOP-TASK-237.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改接口、计价接口、退款、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
