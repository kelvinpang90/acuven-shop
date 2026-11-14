# SHOP-TASK-236 会员计价接入节日促销活动（参加与不参加活动取更优）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 4、6、10 条与「计价、优惠、积分与库存」第 1、2 条：券、积分与活动叠加的金额与取更优，不碰个人资料。依赖 SHOP-TASK-232（`choose_promotion_plan`、`CouponTerms`、`price_order` 的 `promotion_discounts`）、233（`active_promotion`、`price_lines`）、234（`quote_cart` 的 `promotion` 与 `PromotionQuote`）与 235（取每件活动优惠的写法）。不改下单、游客计价、展示、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `app/services/coupons.py`：`CouponCheck` 末尾加 `min_subtotal_sen: int | None = None`；`_judge` 在券可用时填上该券的 `min_subtotal_sen`，所以 `check_coupon` 与 `reserve_coupon` 的可用结果都带最低消费，不可用时为 None。券的其他判定不变。
- [x] `app/services/member_ordering.py`：
  - 方案判定函数（SHOP-TASK-237 下单时复用）：

    ```python
    def decide_member_promotion(
        promotion: LoadedPromotion | None,
        lines: Sequence[PromoInputLine],
        *,
        shipping_fee_sen: int | None,
        coupon: CouponCheck | None,
        points_to_redeem: int,
    ) -> MemberPromotionPlan
    ```

    `lines` 为全部正常行（行序、单价、件数、商品与分类 ID）；`shipping_fee_sen` 为未免的运费报价（None 即没有运费报价）；`coupon` 为以原价小计调用一次 `check_coupon` 的结果（没给券代码时为 None）；`points_to_redeem` 为会员所填积分。没有运费报价、`promotion` 为 None 或 `price_lines` 判定不参加时 `plan` 为 None；参加而没选券也没填积分时取甲、不比较；否则以 `choose_promotion_plan` 比较（`coupon` 为可用券组成的 `CouponTerms`，`min_spend_sen` 取 `CouponCheck.min_subtotal_sen`，不可用为 None；两个开关取活动的设置）。
  - 结果 `MemberPromotionPlan`（frozen）：`plan`（`PLAN_PROMOTION` = `"promotion"`、`PLAN_NO_PROMOTION` = `"no_promotion"` 或 None）、`promotion_pricing`（取甲时为 `price_lines` 的 `PromotionPricing`，否则 None）、`shipping_waived_sen`（取甲时为免去的运费，否则 0）、`use_coupon`、`points_allowed`、`promotion_pre_points_total_sen`、`other_pre_points_total_sen`（比较过才有）；属性 `promotion_discounts` 为传给 `price_order` 的每件活动优惠（不取甲为 None）。不取甲时 `use_coupon` 即券可用、`points_allowed` 为真，与没有活动时相同。
  - `quote_member_cart`：以 `active_promotion(db, now)` 读生效活动传给 `quote_cart`。有不正常的行或超出整单上限时照旧提前返回，`promotion` 照转，其余新字段为 None 与 False。否则按原价小计查一次券；本单参加活动（`quote.promotion.participates is True`）时照 SHOP-TASK-235 以 `purchasable` 取各 SKU 的商品与分类 ID 组成 `PromoInputLine`，调用 `decide_member_promotion`。之后 `price_order` 带 `promotion_discounts`（取甲时），券只在 `use_coupon` 时用；`points_allowed` 时积分上限为可用余额与（活动与券之后的）商品额较小者、规则照旧，否则上限 0、计入 0、`points_accepted` 为真、会员填了积分时 `points_not_combinable` 为真。合计为现金实付合计加运费报价减免去的运费（取乙与不参加时免去的为 0）。取甲且券可用而未用时 `coupon_note` 为 `COUPON_NOT_COMBINABLE`（活动不与券同用）或 `COUPON_BELOW_MIN_AFTER_SALE`。
  - `MemberCheckoutQuote` 末尾新增：`promotion: PromotionQuote | None`、`promotion_plan: str | None`、`promotion_pre_points_total_sen: int | None`、`other_pre_points_total_sen: int | None`、`coupon_note: str | None`（`not_combinable`、`below_min_after_sale` 或 None）、`points_not_combinable: bool`。
- [x] `app/api/member_orders.py`：`MemberQuoteOut` 末尾加同名字段（`promotion` 以 `PromotionQuote` 数据类为字段类型），路由逐字段传入。JSON 字段名：`promotion`、`promotion_plan`、`promotion_pre_points_total_sen`、`other_pre_points_total_sen`、`coupon_note`、`points_not_combinable`。服务层的时钟仍取本模块的 `_utcnow`；其余校验、错误与 no-store 不变。
- [x] 测试：新文件 `tests/test_member_promotion_quote.py`（SQLite 内存库）：第 8 条首例取甲（5310 与 8270，券 590、积分 1000、合计 4310、获得 43）；关闭券同用取甲、`coupon_note` 为 `not_combinable`、合计 4900；D 例取乙（18800 与 17800，合计 17800）；第三例取乙（10700 与 5800，合计 5800）；只关闭积分同用时 `points_not_combinable` 为真、上限与计入为 0、合计 5310；没有运费报价时 `promotion_plan` 为 None 而 `promotion` 为暂算值；参加而不选券不填积分时取甲、不比较、合计 5900（积分同用与否两种上限）；不正常的行提前返回；`check_coupon` 与 `reserve_coupon` 在券可用时给出 `min_subtotal_sen`。接口层替换 `app.api.member_orders._utcnow`，首例与两个开关都关闭的响应带上新字段。`tests/test_member_orders_api.py` 只在 `MEMBER_QUOTE_FIELDS` 加上新字段。每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - 每件的活动优惠没有从 `quote_cart` 的结果取（`PromotionQuote` 只有合计），而是照 SHOP-TASK-235 在同一会话、同一活动对象上以 `price_lines` 重算；只在本单参加活动时多查一次 `purchasable`。
  - 「会员选了券」按给了券代码判断（不论券是否可用）：券不可用时以 `coupon=None` 比较，参加活动的甲总不高于乙，结果为取甲。
  - 取甲而活动不与积分同用、会员没填积分（只选了券）时，`points_cap` 为 0、`points_accepted` 为真、`points_not_combinable` 为假。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 4、6、8、10 条，手算核对测试里的数字（如首例：甲 8300 − 2400 − 590 + 800 − 800 = 5310，乙 8300 − 830 + 800 = 8270；合计 5310 − 1000 = 4310）。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新字段——MemberCheckoutQuote 末尾加 promotion（quote_cart 给出的 PromotionQuote 或 None，原样转出）、promotion_plan（promotion、no_promotion 或 None）、promotion_pre_points_total_sen 与 other_pre_points_total_sen（int 或 None）、coupon_note（not_combinable、below_min_after_sale 或 None）、points_not_combinable（bool）。有不正常的行或超出整单上限时照现在提前返回，promotion 照转，其余新字段为 None 与 False
      - 不比较的情形——没有运费报价、没有生效活动或本单不参加活动时，券与积分照现在按原价计算，promotion_plan 为 None；参加活动而会员没选券也没填积分时，promotion_plan 为 promotion，按活动后的金额与实际运费给出合计，两个积分前应付为 None，points_cap 在活动与积分同用时为可用余额与活动后商品额较小者、不同用时为 0
      - 比较——参加活动且会员选了券或填了积分时调用 choose_promotion_plan（coupon 为可用券组成的 CouponTerms 或 None，两个开关取活动的设置）：promotion_plan 照所取方案；两个积分前应付分别为甲与乙的；取甲时 price_order 带每件活动优惠、券只在 use_coupon 时用、积分只在 points_allowed 时按现在的上限规则接受，合计为现金实付合计加实际运费；取乙时与没有活动时相同（含未免的运费）
      - 说明字段——取甲且券可用而未用时，coupon_note 在活动不与券同用时为 not_combinable，否则为 below_min_after_sale；取甲、会员填了积分而 points_allowed 为 False 时 points_not_combinable 为 True、points_cap 为 0、points_redeemed 为 0、points_accepted 为 True（这是活动不同用积分，不是积分输入错误）；coupon_applicable 仍表示券本身是否可用
      - 接口——MemberQuoteOut 加同名字段（promotion 的结构同 PromotionQuote，可直接以该数据类为字段类型），照服务层结果输出；其余校验、错误与 no-store 不变
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_member_promotion_quote.py 照第 8 条：首例（两个开关都打开，10% 券最低消费 RM50，1000 积分，西马）取甲，两个积分前应付为 5310 与 8270，券 590、积分 1000、合计 4310；关闭券同用时取甲、coupon_note 为 not_combinable、合计 4900；D 例取乙；第三例取乙且合计 5800；只关闭积分同用时 points_not_combinable 为 True；没有运费报价时 promotion_plan 为 None 而 promotion 有暂算值；check_coupon 与 reserve_coupon 在券可用时给出 min_subtotal_sen。接口层固定 _utcnow 后响应带上这些字段（MemberQuoteOut 在路由里逐字段构造，新字段同样逐个传入）；tests/test_member_orders_api.py 的 MEMBER_QUOTE_FIELDS 只为加上新字段而改。每条测试的文档字符串写明它守住的第几条哪一句
      - docs/task-records/SHOP-TASK-236.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明方案判定函数的名称与签名、新字段与 JSON 字段名）；改动只落在 allowed_change_paths；不改下单、游客计价、展示、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
