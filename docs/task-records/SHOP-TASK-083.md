# SHOP-TASK-083 优惠券与用券记录的数据模型

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「数据模型」的 Coupon / CouponUse 一行、「计价、优惠、积分与库存」第 1 条与 `docs/HANDOFF.md` 0.46 记录的 Kelvin 2026-10-10 决定（两个上限可留空表示不限，预占与核销都计次数，退款不恢复）建两张表：`app/models/coupon.py`（`app/models/__init__.py` 导出 `Coupon`、`CouponUse`）、迁移 `alembic/versions/20261010_0018_coupons.py`（revision `0018`，down_revision `0017`）；测试见 `tests/test_coupon_models.py`
- 两张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；时间一律不带时区的 UTC；金额一律整数仙；外键一律 ON DELETE RESTRICT；检查约束只用比较、LENGTH、IN 与 IS NULL / IS NOT NULL 及 AND / OR）：
  - `coupons`：`code` String(20)（唯一 `uq_coupons_code`，库里存大写）、`coupon_type` String(10)、`amount_off_sen`（可空）、`percent_off`（可空）、`min_subtotal_sen`（服务端默认 0）、`starts_at`、`ends_at`（仍有效的最后一刻）、`total_use_limit`（可空即不限）、`per_member_use_limit`（可空即不限）、`is_active` Boolean、`created_at`。检查约束：
    - `ck_coupons_code_length`：`LENGTH(code) >= 4 AND LENGTH(code) <= 20`
    - `ck_coupons_type_valid`：`coupon_type IN ('fixed', 'percent')`
    - `ck_coupons_fixed_amount`：`coupon_type <> 'fixed' OR (amount_off_sen IS NOT NULL AND amount_off_sen > 0 AND percent_off IS NULL)`
    - `ck_coupons_percent_value`：`coupon_type <> 'percent' OR (percent_off IS NOT NULL AND percent_off >= 1 AND percent_off <= 100 AND amount_off_sen IS NULL)`
    - `ck_coupons_min_subtotal_sen_non_negative`：`min_subtotal_sen >= 0`
    - `ck_coupons_ends_after_starts`：`ends_at > starts_at`
    - `ck_coupons_total_use_limit_positive`：`total_use_limit IS NULL OR total_use_limit >= 1`
    - `ck_coupons_per_member_use_limit_positive`：`per_member_use_limit IS NULL OR per_member_use_limit >= 1`
  - `coupon_uses`：`coupon_id` → `coupons.id`（`fk_coupon_uses_coupon_id_coupons`）、`order_id` → `orders.id`（`fk_coupon_uses_order_id_orders`，唯一 `uq_coupon_uses_order_id`，一单至多一张券）、`member_id` → `members.id`（`fk_coupon_uses_member_id_members`，非空；会员行不物理删除，注销后记录随会员去标识化、照常保留）、`status` String(10)、`coupon_discount_sen`（订单快照 `coupon_discount_sen` 的副本）、`created_at`、`updated_at`。普通索引 `ix_coupon_uses_coupon_id_status`（`coupon_id`, `status`）与 `ix_coupon_uses_coupon_id_member_id_status`（`coupon_id`, `member_id`, `status`），供之后按计次状态（`reserved`、`used`，模型里的 `COUNTED_USE_STATUSES`）数总次数与每会员次数。检查约束：
    - `ck_coupon_uses_status_valid`：`status IN ('reserved', 'used', 'released')`
    - `ck_coupon_uses_coupon_discount_sen_non_negative`：`coupon_discount_sen >= 0`
  - 除券的固定金额、百分比与两个上限外，所有列非空。
  - 0018 的 upgrade 先建 `coupons`，再建 `coupon_uses` 及两个索引；downgrade 先删 `coupon_uses`（索引随表删除），再删 `coupons`。
- 偏离：未改设计，未发现须停下的设计问题。说明几处取舍，请审阅：
  - downgrade 不单独删两个索引：MySQL 建这两个以 `coupon_id` 开头的索引时会去掉外键自动建的索引、改用它们，逐个删到最后一个会因外键需要而报错（MySQL 1553），所以随表一起删。
  - 列名：类型为 `coupon_type`、固定金额为 `amount_off_sen`、百分比为 `percent_off`、最低商品消费为 `min_subtotal_sen`、两个上限为 `total_use_limit` 与 `per_member_use_limit`、启用状态为 `is_active`（与 `products.is_active` 相同写法）；本单券折扣沿用订单快照的列名 `coupon_discount_sen`。
  - 最低商品消费的默认 0 写成服务端默认值（与 `products.max_per_order` 相同），模型与迁移一致；写入方显式写 NULL 时由非空约束拒绝。
  - 截止时间是「仍有效的最后一刻」，有效期按闭区间判断由之后的查券规则实现；库里只保证截止晚于生效。
  - MySQL 默认排序规则不区分大小写、LENGTH 按字节计：代码大写与字符集由写入方保证（代码按 ASCII 写入时字节数即字符数），唯一约束也按不区分大小写判断。
  - 库里不约束用券记录的会员与订单上的会员一致、也不约束本单折扣不超过券面额：订单上的会员 ID 注销时会被清空，订单折扣按计价算出，二者都由之后的下单规则保证。
- 留给之后的任务：查券（启用、有效期、最低消费、两个上限按 `reserved` 与 `used` 计次）、下单时预占、取消或支付超时释放、支付成功核销（退款不改状态、不恢复次数），由 SHOP-TASK-085 与 088 起的会员结账链路实现；注销时未用券作废随 HANDOFF 2026-10-11 决定所列任务实现；后台优惠券维护（A05 创建、停用）与会员中心「我的优惠券」查询另行登记。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句与 HANDOFF 0.46 的 Kelvin 决定自查，并逐个核对 0018 的列类型、可空性、服务端默认值、约束名与表达式、外键删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_coupon_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，先写入固定金额券、百分比券（含 1% 与 100%）、两个上限留空的券、停用的券、长 20 的代码与截止只晚生效 1 秒的券，及预占、核销、释放三种状态的用券记录（含折扣 0）作对照；再覆盖：每个检查约束至少一个反例（先在独立连接上逐条求值该表全部检查约束，断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一），含未知类型与状态、fixed 缺金额或金额为 0 或负或带百分比、percent 缺百分比或为 0 或 101 或带金额、代码 0、3、21 个字符、截止等于或早于生效、两个上限为 0、负的最低消费与折扣；除自增主键外每个非空列写入 NULL 被非空约束拒绝；重复代码与同一订单第二条用券记录被唯一约束拒绝；用券记录指向不存在的券、订单或会员，删除被用券记录引用的券、订单或会员被外键拒绝（另以未被引用的券可删作对照）；不写最低消费时为 0；金额读回为 int；同一会员同一张券可在多张订单上各有记录；只有设计指定的列可空、两个计次索引存在、外键都是 RESTRICT。每条测试写明它守住的设计或决定原句。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/models/coupon.py 定义两个模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项（写法照 app/models/member.py）；app/models/__init__.py 导出它们；时间一律不带时区的 UTC；外键删除行为一律 RESTRICT；除下面写明可空的列外全部非空（检查约束遇到空值会放行，非空约束不能省）；金额一律整数仙；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - 优惠券表 coupons——代码（String(20)，全表唯一；库里存大写，检查约束保证长度 4 到 20，字符集与大小写由写入方保证）、类型（只允许 fixed、percent）、固定金额（仙，可空）、百分比（整数，可空）、最低商品消费（仙，默认 0）、生效时间、截止时间（截止时间是仍有效的最后一刻）、总使用上限（可空即不限）、每会员使用上限（可空即不限）、启用状态、创建时间；检查约束保证 fixed 时固定金额大于 0 且百分比为空、percent 时百分比在 1 到 100 之间且固定金额为空、最低商品消费不小于 0、截止时间晚于生效时间、两个上限非空时不小于 1
      - 用券记录表 coupon_uses——所属券（外键 coupons）、订单（外键 orders，全表唯一，一单至多一张券）、会员（外键 members，非空；会员注销后行保留，记录随之去标识化）、状态（只允许 reserved、used、released）、本单券折扣（仙，不小于 0，即订单快照 coupon_discount_sen）、创建时间、更新时间；按（券、状态）与（券、会员、状态）建索引，供之后按「预占与核销都计次数」计算总次数与每会员次数
      - 新迁移文件 alembic/versions/20261010_0018_coupons.py，revision 为 0018、down_revision 为 0017，upgrade 建两张表，downgrade 按依赖倒序删除；约束名按命名约定逐个写出；迁移只在 MySQL 上执行（部署与 CI）；模型里的检查约束表达式须 SQLite 与 MySQL 都支持（只用比较、LENGTH、IN、IS NULL 与 AND / OR），因为测试在 SQLite 上按模型建表；PR 正文逐表列出检查约束与索引
      - tests/test_coupon_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入合法的固定金额券、百分比券与上限留空的券及其用券记录作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（含除自增主键外每个非空列写入空值被拒、未知类型与状态、fixed 缺金额或带百分比、percent 为 0 或 101 或带金额、代码过短过长、重复代码、截止不晚于生效、上限为 0、负的最低消费与折扣、同一订单两条用券记录、指向不存在的券、订单或会员、删除被用券记录引用的券被拒）；每条测试写明它守住的是设计或 Kelvin 决定的哪一句
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异；以上测试与已有后端测试都由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-083 记录段（两张表的列与关键约束、偏离、验证到什么程度，并写明查券、预占、释放、核销与后台维护留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改已有模型与迁移、.platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py 与前端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```
