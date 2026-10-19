# SHOP-TASK-103 订单视图补券与积分，确认收货与退款申请可由会员操作

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「订单与退款状态」、「权限与资料保护」第 6 条与「数据模型」Order 一行，以及 `docs/UX.md` 0.10 的 P09、P13，扩展查单服务视图：新增 `placed_by_member`、`coupon_discount_sen`、`points_redeemed`。`placed_by_member` 只由 `claim_status == not_claimable` 判定，因此会员注销后仍保留其下单身份；游客订单及后来认领的游客订单为假。查单接口通过既有视图序列化自动带出字段，未改接口文件。
- `confirm_receipt` 与 `submit_refund` 新增默认值为 `guest` 的关键字 `actor`，只接受 `guest` 与 `member`；确认收货记录、订单事件和退款申请均记录该操作者。新增 `app/services/account_orders.py` 的只读会员订单列表（每页 20 张、本人下单与已认领订单、创建时间和 ID 倒排、总数与越界空页）及本人订单号定位。
- 偏离与取舍：无设计偏离；不做会员接口，会员的身份授权仍留给接口层。查单页 P09 对会员下单订单显示券与积分两行留给之后的前端任务。
- 验证到什么程度：`tests/test_account_orders.py` 以内存 SQLite 按模型建表，覆盖本人订单隔离、排序、分页、定位、会员操作者与非法操作者、以及会员下单/游客/已认领游客订单的视图快照；既有查单与退款测试只为新增视图字段更新，退款视图仍断言不含积分返还或追回字段。由 PR 的必需 CI 检查 backend 执行。会员接口留给 SHOP-TASK-104、105。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/order_lookup.py 的查单视图 LookupOrder 另给 placed_by_member（订单的 claim_status 为 not_claimable 即下单时是会员，会员注销后 member_id 清空也仍为真）、coupon_discount_sen 与 points_redeemed（取订单快照）；查单接口 GET /api/orders/lookup 的响应随之多出这三个字段，其余字段与行为不变；模块说明里「不含优惠券与积分两项」的说法随之改写
      - app/services/order_lookup.py 的 confirm_receipt 与 app/services/refunds.py 的 submit_refund 各增加关键字参数 actor（默认 guest，只接受 guest 与 member，其他值抛 ValueError），确认收货记录、事件与退款申请的操作者都用它；游客接口不传，行为不变
      - 新文件 app/services/account_orders.py——我的订单列表函数（入参为数据库会话、会员 ID 与页码，每页 20 张，只含 member_id 等于该会员的订单，即本人下单与已认领的订单，按创建时间从新到旧、同时刻按 ID 从大到小；每项为订单号、创建时间、状态与合计；返回总数，页码超出时该页为空）与本人订单定位函数（入参为数据库会话、会员 ID 与订单号原文，订单号格式不合法、不存在或 member_id 不等于该会员都返回 None，否则返回订单 ID）；只读，不写日志
      - 测试验证意图——tests/test_account_orders.py 用 SQLite 内存库按模型建表：列表只含本人下单与已认领的订单（不含他人、游客与已注销会员的订单）、排序、每页 20 张与页码超出；定位函数对他人订单、游客订单与不合法订单号返回 None；以 member 为操作者确认收货与申请退款时记录、事件与申请的操作者为 member，actor 取其他值抛错；查单视图对会员下单的订单 placed_by_member 为真并给出券与积分快照，对游客订单与已认领的游客订单为假；tests/test_order_lookup.py 与 tests/test_refunds.py 只在现有断言因视图多出三个字段或新增参数而失效时随之修改；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-103 记录段（视图字段、操作者参数、我的订单查询、偏离、验证到什么程度，并写明会员接口留给 SHOP-TASK-104、105，查单页 P09 显示券与积分两行留给之后的前端任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/api/ 下的文件、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250），不合并、不直推
```
