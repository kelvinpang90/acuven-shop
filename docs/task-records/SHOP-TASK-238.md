# SHOP-TASK-238 订单详情与后台订单的活动优惠字段（P09、A02 的数据）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 7 条：只显示订单金额快照，不新增个人资料的输出。依据 UX 0.20 的 P09 与 A02「金额明细」（活动优惠合计大于 0 时加 `[checkout.summary_promo]` 一行，活动免运费时运费行显示 `[checkout.shipping_free]`；A02 另显示 `[admin.order_promotion]`，即活动的内部名称）。依赖 SHOP-TASK-230（订单列）。不改前端、下单、计价、退款、数据库、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `app/services/order_lookup.py` 的 `LookupOrder`（游客订单查询 `GET /api/orders/lookup` 的 `orders[]` 与会员订单详情 `POST /api/member/orders/view` 共用）新增两个字段，`lookup_orders` 直接取订单快照，不重算：
  - `promotion_discount_sen`（int）= `Order.promotion_item_discount_sen` + `Order.promotion_order_discount_sen`；没有活动为 0。
  - `shipping_waived_sen`（int）= `Order.shipping_waived_sen`；未免为 0。`shipping_fee_sen` 照旧是实际运费（活动免运费时为 0）。
  - 游客订单与会员订单都给出（P05 的活动优惠一行「游客与会员都显示」，P09 同）；不给活动名称或活动 ID。
- [x] `app/services/admin_orders.py` 的 `AdminOrderDetail`（后台订单详情 `GET /api/admin/orders/{order_id}`）新增三个字段：
  - `promotion_discount_sen`、`shipping_waived_sen`：取自查单视图的同名字段（即订单快照）。
  - `promotion_name`（str 或 null）：按订单的 `promotion_id` 外连接 `promotions` 取 `name`（与券折扣、积分抵扣同一条查询）；订单没有活动时为 null。
  - 后台订单列表（`AdminOrderRow`）不变。
- [x] JSON 照模型原样输出，字段名即上面的名字；现有字段与接口路径不变。前台任务据此：P09 在 `promotion_discount_sen > 0` 时加 `[checkout.summary_promo]` 一行、`shipping_waived_sen > 0` 时运费行显示 `[checkout.shipping_free]`；A02 另在 `promotion_name` 不为 null 时显示 `[admin.order_promotion]`。
- [x] 测试：
  - 新文件 `tests/test_order_promotion_display.py`：直接写入第 8 条首例的订单快照（商品级 1900、整单级 500、实际运费 0、免去 800；会员订单另带券 590、积分 1000，应付 4310）。服务层断言 `lookup_orders` 与 `order_detail` 给出 2400 与 800、后台给出活动内部名称，没有活动的订单为 0、0 与 None；接口层断言游客订单查询、会员订单详情与后台订单详情的响应带上这些字段，P09 两处响应不含活动内部名称，后台列表的行不变。
  - `tests/test_order_lookup.py` 的 `ORDER_FIELDS` 加上两个新字段；`tests/test_admin_orders.py` 对后台详情的整体比较加上三个新字段（该单没有活动：0、0、null）。两个文件只为新字段而改。
  - 每条新测试的文档字符串写明它守住的设计或 UX 句子。
- 偏离与落实细节：
  - 测试里的活动直接写 `promotions` 一行（已发布、已开始），不建装饰行与规则；详情只读订单上的活动 ID 与活动名称，不读规则。
  - 游客订单的活动快照不带券与积分（游客本来不能用），应付 8300 − 2400 + 0 = 5900。
  - `tests/test_account_orders_api.py` 未改：它以 `lookup_orders` 的结果整体比较会员详情，新字段两边同时出现。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 7、8 条与 UX 0.20 P09、A02，手算核对测试数字（A：2500 − 656 − 184 − 313 = 1347；B：1200 − 94 − 111 − 187 = 808；应付 8300 − 2400 − 590 − 1000 + 0 = 4310），并查过其余以整体比较订单视图的测试（`ORDER_FIELDS` 在 `tests/test_demo_payment.py`、`tests/test_member_pay_api.py`、`tests/test_member_orders_api.py` 里是别的视图）。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - P09——LookupOrder 加 promotion_discount_sen 与 shipping_waived_sen，取订单快照的 promotion_item_discount_sen 加 promotion_order_discount_sen 与 shipping_waived_sen；游客订单查询与会员订单详情的接口响应随之带上两个字段；没有活动的订单两项为 0
      - A02——AdminOrderDetail 加 promotion_discount_sen、shipping_waived_sen 与 promotion_name（按订单的 promotion_id 取该活动的 name，没有为 None），后台订单详情接口随之带上；后台订单列表不变
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_order_promotion_display.py 用直接写入的订单快照（第 8 条首例：商品级 1900、整单级 500、免去 800）断言两处详情给出 2400 与 800、后台给出活动内部名称，没有活动的订单为 0、0 与 None；接口层断言游客订单查询、会员订单详情与后台订单详情的响应带上这些字段。tests/test_order_lookup.py 的 ORDER_FIELDS 与 tests/test_admin_orders.py 对后台详情的整体比较会因新字段失败，这两个文件只为加上新字段而改。每条测试的文档字符串写明它守住的设计或 UX 句子
      - docs/task-records/SHOP-TASK-238.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明新字段与 JSON 字段名，供前台任务引用）；改动只落在 allowed_change_paths；不改下单、计价、退款、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
