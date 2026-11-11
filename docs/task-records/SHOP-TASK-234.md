# SHOP-TASK-234 游客计价接入节日促销活动（计价服务与计价接口）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 4、10 条与「计价、优惠、积分与库存」第 1、8 条：计价结果的金额与凑单提示，不碰个人资料。依赖 SHOP-TASK-233（`LoadedPromotion`、`active_promotion`、`price_lines`）。不改下单、会员计价、展示的服务与接口、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。游客不能用券与积分，所以没有第 6 条的方案比较。
- [x] `app/services/checkout.py`：`quote_cart` 新增只限关键字参数 `promotion: LoadedPromotion | None = None`，由调用方读出后传入（计价接口传生效活动，之后的管理员预览传要预览的活动）；不传即没有活动，结果与之前逐字段相同（新字段取默认值）。下单（`app/services/ordering.py`）与会员计价（`app/services/member_ordering.py`）的现有调用不传，未改。新增与改动的数据类（都是 `@dataclass(frozen=True)`，JSON 字段名与属性名相同，接口照原样输出）：

  ```python
  PricedLine(..., unit_price_sen: int, line_subtotal_sen: int,
             promotion_rule_id: int | None = None,         # 新增，末尾
             promotion_item_discount_sen: int = 0)         # 新增，末尾
  BuyXGetYQuote(rule_id: int, buy_quantity: int, get_quantity: int, get_percent: int,
                units_in_scope: int, units_to_next: int)
  PromotionQuote(promotion_id: int, item_discount_sen: int, order_discount_sen: int,
                 after_item_subtotal_sen: int, spend_mode: str | None,
                 spend_tier_reached: int | None, spend_next_threshold_sen: int | None,
                 spend_next_value: int | None, spend_next_gap_sen: int | None,
                 free_shipping_threshold_sen: int | None, free_shipping_reached: bool,
                 free_shipping_gap_sen: int | None, shipping_waived_sen: int | None,
                 participates: bool | None, buy_x_get_y: list[BuyXGetYQuote])
  CheckoutQuote(..., amount_over_limit: bool,
                promotion: PromotionQuote | None = None)   # 新增，末尾
  ```

- [x] 计算：
  - 只有 `status == "ok"` 的行进入 `price_lines`（第 4 条），`PromoInputLine.line_index` 为该行在请求里的序号，商品 ID 与分类 ID 取自 `quote_cart` 内已查到的商品（`product.category_id` 即商品当时所属的分类）；运费报价有时以 `shipping.fee_sen` 传入，否则传 None。
  - 行：正常行的 `promotion_rule_id` 为该行命中的商品级规则（没有命中为 None），`promotion_item_discount_sen` 为该行各件 `item_discount` 之和；不可购买、超出限购、库存不足的行与没有活动时取默认值。
  - `PromotionQuote` 各字段取 `PromotionPricing`：`item_discount_sen` = `item_discount_total`，`order_discount_sen` = `order_discount_total`，`after_item_subtotal_sen` = S1，`spend_tier_reached`、`spend_next_gap_sen`、`free_shipping_reached`、`free_shipping_gap_sen`、`shipping_waived_sen`、`participates` 原样。`spend_mode` 为满额规则的 `mode`（`"amount"` 或 `"percent"`，没有满额规则为 None）；`spend_next_threshold_sen` 与 `spend_next_value` 为下一档的门槛与优惠（固定金额为仙、百分比为整数；已到最高档或没有满额规则为 None）；`free_shipping_threshold_sen` 为免运费门槛（没有免运费规则为 None）；`buy_x_get_y` 为每条买 X 享 Y 规则的参数与 `units_in_scope`（n）、`units_to_next`（(X + Y) − n mod (X + Y)），按 `rule_id` 升序。传了活动就总是给出：没有正常行时各金额为 0、差额为门槛本身。
  - 合计：有运费报价且 `participates` 为 True 时，`total_sen = subtotal_sen − item_discount_sen − order_discount_sen + shipping.fee_sen − shipping_waived_sen`，参考外币按这个合计换算；否则照旧（小计加运费，或没有运费报价时为 None）。`shipping` 仍为未免的运费报价。整单金额上限照计价第 8 条按原价小计加未免的运费判断；`can_place_order` 的判定不变。
- [x] `app/api/checkout.py`：新增模块级 `_utcnow()`（不带时区的 UTC，照 `app/api/member_orders.py` 所用的 `app/api/pay.py` 的 `_utcnow` 写法）；`POST /api/checkout/quote` 每次请求以 `active_promotion(session, _utcnow())` 读生效活动传入 `quote_cart`，不缓存。没有生效活动时 `promotion` 为 null、各行 `promotion_rule_id` 为 null、`promotion_item_discount_sen` 为 0。其余校验、错误、限流与 no-store 不变。
- [x] 测试：
  - 新文件 `tests/test_checkout_promotion_quote.py`（SQLite 内存库）：第 8 条算例的商品与规则（游客，不用券）各行的规则与商品级折扣（A 1000、B 0、C 900），`PromotionQuote` 逐字段（商品级 1900、整单级 500、S1 6400、距下一档 3600、下一档 RM100 减 RM12、免运费门槛 6000 已达），西马运费 800 时 `total_sen` 5900；没有运费报价时 `participates` 与 `shipping_waived_sen` 为 None、`total_sen` 为 None；新加坡收货时参考外币按活动后的合计；库存不足与超出限购的行不计入活动；所有规则都没带来优惠时 `participates` 为 False 且合计照旧；没有正常行时差额为门槛本身；达到最高档时下一档为 None；金额上限按原价小计与未免的运费判断；不传 `promotion` 时新字段为默认值、合计照旧。接口层替换 `app.api.checkout._utcnow` 固定时钟：活动期间、未开始与已结束三种情况下 `promotion` 分别为有值、null、null。每条测试的文档字符串写明它守住的第几条哪一句。
  - `tests/test_checkout_quote.py`：`test_ok_line_has_exactly_the_public_fields` 的期望加 `"promotion": None`，`_red_line` 加 `"promotion_rule_id": None` 与 `"promotion_item_discount_sen": 0`；其余断言不动。
- 偏离与落实细节：
  - `tests/test_member_orders_api.py` 未改：其中没有按响应逐字段比较各行的断言，`GUEST_QUOTE_FIELDS` 按验收不改。会员计价的响应各行同样是 `PricedLine`，所以会带两个行字段的默认值（会员计价不传活动）。
  - 有非正常行且本单参加活动时，`total_sen` 照验收以 `subtotal_sen`（含超出限购与库存不足的行）减活动优惠计算；这时本来就不可下单。
  - `buy_x_get_y` 用 list（与 `lines` 相同），JSON 为数组。
- 供后续任务引用：会员计价接入活动与第 6 条方案时沿用 `PromotionQuote` 与两个行字段；前台 P04、P05 按上面的 JSON 字段名读取。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 4、8、10 条及计价第 1、8 条，手算核对测试里的数字。全部后端测试、ruff check 与 ruff format 由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。
