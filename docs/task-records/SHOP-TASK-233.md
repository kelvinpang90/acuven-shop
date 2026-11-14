# SHOP-TASK-233 读取生效的节日促销活动与规则换算（服务层）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 1、3、4 条。只读活动与规则、算金额，不碰个人资料。依赖 SHOP-TASK-229（活动表）与 SHOP-TASK-231（计价纯函数）。不改计价、下单与展示的服务与接口、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新建 `app/services/promotions.py`，公开的数据类（都是 `@dataclass(frozen=True)`）与函数：

  ```python
  LoadedItemRule(rule_id: int, position: int, rule: ItemDiscountRule | BuyXGetYRule,
                 product_ids: frozenset[int], category_ids: frozenset[int])
  LoadedPromotion(promotion_id: int, version: int, status: str,
                  starts_at: datetime | None, effective_end: datetime | None,
                  coupon_combinable: bool, points_combinable: bool,
                  item_rules: tuple[LoadedItemRule, ...],
                  spend_rule: SpendRule | None,
                  free_shipping_rule: FreeShippingRule | None)
  PromoInputLine(line_index: int, unit_price: int, quantity: int,
                 product_id: int, category_id: int)

  def active_promotion(
      db: Session, now: datetime, *, share_lock: bool = False
  ) -> LoadedPromotion | None
  def load_promotion(db: Session, promotion_id: int) -> LoadedPromotion | None
  def rule_for_product(
      promotion: LoadedPromotion, product_id: int, category_id: int
  ) -> int | None
  def price_lines(
      promotion: LoadedPromotion,
      lines: Sequence[PromoInputLine],
      *,
      shipping_fee_sen: int | None,
  ) -> PromotionPricing
  ```

  `ItemDiscountRule`、`BuyXGetYRule`、`SpendRule`、`FreeShippingRule` 与 `PromotionPricing` 是 `app/services/promotion_pricing.py`（SHOP-TASK-231）的数据类。
- [x] 读取：
  - `active_promotion` 取 `status = 'published'`、`starts_at <= now`、`COALESCE(ended_early_at, ends_at) > now` 的活动，按（`starts_at`, `id`）升序取第一个；`now` 带时区抛 ValueError。读到多于一个时记 `logger.warning("promotion_overlap promotion_ids=<id>,<id>,...")`（logger 为 `app.services.promotions`，ID 按上面的顺序，不带名称或其他内容）。`share_lock=True` 时查询加 `with_for_update(read=True)`（MySQL 上为共享锁，锁住全部符合条件的活动行），供下单事务（SHOP-TASK-235、237、241）使用。
  - `load_promotion` 按 ID 读任一状态的活动，不存在返回 None，不加锁。
  - 两者读完活动后各用一次查询取规则（按 `position`）、商品范围、分类范围与满额档（按规则、`position`）；活动没有规则时后三次查询不发。活动、规则与满额档的查询带 `populate_existing`，同一 Session 里先前载入的对象也按库里的当前值重读。不写库、不 flush。
- [x] 换算：商品级打折为 `ItemDiscountRule(rule_id, percent, amount_sen)`，买 X 享 Y 为 `BuyXGetYRule(rule_id, buy_quantity, get_quantity, get_percent)`，`item_rules` 按 `position` 升序；满额减或满额折为 `SpendRule(mode=discount_mode, tiers=((threshold_sen, value), ...))`，各档按 `position` 升序，`value` 在 `amount` 方式取 `amount_sen`、`percent` 方式取 `percent`；满额免运费为 `FreeShippingRule(threshold_sen)`。`effective_end` 为 `ended_early_at`（有时）或 `ends_at`。
- [x] 命中规则：`rule_for_product` 按 `position` 升序找第一条商品 ID 集合包含 `product_id` 或分类 ID 集合包含 `category_id` 的商品级规则，返回其 `rule_id`，没有返回 None。`category_id` 由调用方给出商品当时所属的分类（第 3 条）。
- [x] 计价：`price_lines` 把每条 `PromoInputLine` 换成 `PromoLine(line_index, unit_price, quantity, rule_for_product(...))`，以 `{rule_id: rule}`（本活动全部商品级规则）、活动的满额与免运费规则和 `shipping_fee_sen` 调用 `price_promotion` 并原样返回；参数取值与 line_index 递增的校验沿用 `price_promotion`。
- [x] 测试 `tests/test_promotions_active.py`（SQLite 内存库）：生效边界（开始前一秒、开始时刻、结束前一秒、结束时刻、提前结束时刻与前一秒、草稿、没有活动）、不缓存、带时区的 `now`、`share_lock` 读出同一活动；三个已发布活动重叠时取开始最早且 ID 最小者并记一条日志、只有一个时不记；`load_promotion` 读出没有时间的草稿与不存在的 ID；商品级规则按 position 排序与换算、满额档按 position 排序（固定金额与百分比两种）；读取的 SELECT 条数；按商品与按分类命中、两条都命中取排序小者、商品改分类后按新分类；第 8 条算例的每件活动优惠 656、656、94、94、900 仙（同 SHOP-TASK-231 的测试）、没有运费报价时不判定参加、行的类型与顺序校验。每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - 库里一个活动有多于一条满额减或满额折、或多于一条满额免运费（数据异常，第 3 条由写入方保证）时，取排序最小的一条，不记日志；验收没有单列。
  - 满额规则的档原样换算，不在这里校验；草稿的不合格数据（如没有档）在 `price_lines` 里由 `price_promotion` 抛错，预览任务须先按保存条件检查或接住错误。
  - `price_lines` 收到不是 `PromoInputLine` 的元素抛 TypeError；验收没有单列，照 `price_promotion` 对 `PromoLine` 的写法。
  - SQLite 不支持 `FOR UPDATE`，测试只核对 `share_lock=True` 读出的结果与不加锁时相同；锁的效果留给在 MySQL 上跑的下单任务。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 1、3、4、8 条，手算核对第 8 条算例的逐件数字。全部后端测试、ruff check 与 ruff format 由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 数据类——frozen 的 LoadedItemRule(rule_id, position, rule, product_ids, category_ids)（rule 为 ItemDiscountRule 或 BuyXGetYRule，两个 ID 集合为 frozenset）与 LoadedPromotion(promotion_id, version, status, starts_at, effective_end, coupon_combinable, points_combinable, item_rules, spend_rule, free_shipping_rule)：item_rules 按 position 升序；spend_rule 为 SpendRule（各档按 position 升序，模式取规则的 discount_mode）或 None；free_shipping_rule 为 FreeShippingRule 或 None；effective_end 为提前结束时间（有时）或结束时间，草稿没有时间时 starts_at 与 effective_end 为 None
      - 读取——active_promotion(db, now) 返回 status 为 published、starts_at <= now 且 now < effective_end 的活动（now 为不带时区的 UTC，带时区抛 ValueError），没有返回 None；多于一个时照上面的落实写法取一个并记日志。load_promotion(db, promotion_id) 按 ID 读出任一状态的活动（不存在返回 None）。两者都只读，除 share_lock 外不加锁，规则、范围与满额档各用一次查询取出（不按规则逐条查询）
      - 命中规则——rule_for_product(promotion, product_id, category_id) 返回范围（商品 ID 集合或分类 ID 集合）包含该商品的商品级规则中 position 最小者的 rule_id，没有返回 None
      - 计价——price_lines(promotion, lines, *, shipping_fee_sen) 接收 PromoInputLine(line_index, unit_price, quantity, product_id, category_id) 的序列（frozen 数据类，只含正常行，line_index 严格递增），按 rule_for_product 定每行的 rule_id，调用 price_promotion（item_rules 为本活动全部商品级规则的 ID 到规则对象的映射）并原样返回 PromotionPricing；shipping_fee_sen 为 None 时照 price_promotion 不判定是否参加
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_promotions_active.py 用 SQLite 内存库建活动：生效判定的边界（开始时刻生效、结束时刻与提前结束时刻不再生效、草稿与未开始的不生效）；读到两个已发布且重叠的活动时取开始最早者并记日志；按商品命中与按分类命中、两条规则都命中时取排序小者、商品改分类后按新分类判断；满额档按 position 排序；price_lines 以第 8 条算例的商品与规则得到 SHOP-TASK-231 测试里同样的每件活动优惠；load_promotion 读得到草稿。每条测试的文档字符串写明它守住的第几条哪一句
      - docs/task-records/SHOP-TASK-233.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明公开的数据类与函数签名，供之后的任务引用）；改动只落在 allowed_change_paths；不改计价、下单与展示的服务与接口、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
