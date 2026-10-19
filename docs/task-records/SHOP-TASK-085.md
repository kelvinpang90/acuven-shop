# SHOP-TASK-085 优惠券的查券、预占、释放与核销规则

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 1、2、7 条（优惠券仅会员可用，先抵商品额、不抵运费；使用额度在下单时预占，取消或支付超时释放；后台修改只影响新订单）、`docs/UX.md` 0.10 P05 与 P13（结账输入代码；「我的优惠券」列出所有启用中且在有效期内的券，对全部会员相同，Q19）与 `docs/HANDOFF.md` 0.46 记录的 Kelvin 2026-10-10 决定（两个上限可留空表示不限，预占与核销都计次数，退款不恢复，每会员按会员账号计数），在 SHOP-TASK-083 的表上实现优惠券规则函数：`app/services/coupons.py`；测试见 `tests/test_coupons.py`。不做接口，不接入下单与支付；未改 `app/services/pricing.py`、模型与迁移，未加表。
- 代码规范化（`normalize_coupon_code`）：去掉首尾空白后只由 ASCII 字母与数字组成、长 4 到 20 时转大写返回，否则（含非字符串）为 `None`，调用方按查无此券处理。有效（`is_coupon_valid`）：启用中且 `starts_at` ≤ 当前时间 ≤ `ends_at`，两端都含。时间一律不带时区的 UTC，带时区的当前时间抛 `ValueError`。
- 查券顺序与原因（`check_coupon(db, code, member_id, subtotal_sen, now)`）：依次判定 `not_found`（规范化不通过或无此代码）→ `inactive` → `not_started` → `expired` → `below_min_spend`（商品小计低于最低商品消费；恰好等于可用）→ `total_exhausted` → `member_exhausted`，报首个不成立的一条。返回 `CouponCheck`：`reason` 为空即可用，此时给出 `coupon_id` 与 `price_order` 的券参数（fixed 为 `fixed_off` 的仙数，percent 为 `percent_off` 的 `Decimal` 百分比，`price_kwargs()` 直接给出关键字参数），不另算折扣；不可用时不给参数。只读，不写库、不加锁。
- 计数口径：状态为 `reserved` 或 `used` 的用券记录计次（模型的 `COUNTED_USE_STATUSES`），`released` 不计；总次数按该券全部计次记录，每会员次数按用券记录上的会员 ID（订单上的会员 ID 注销时会被清空，不用它）；上限为空即不限，计数达到上限即用满。`used` 不因退款改变，次数不恢复。
- 预占的行锁（`reserve_coupon(db, coupon_id, order_id, member_id, coupon_discount_sen, subtotal_sen, now)`）：依次以 SELECT … FOR UPDATE 锁住会员行、订单行与券行（会员 → 订单 → 券），三行都以 `populate_existing` 重新载入，不沿用 ORM 缓存；锁下会员不存在或不是 active、订单不存在或其 `member_id` 不等于该会员时抛 `ValueError`；该订单已有用券记录（不论状态）时抛 `ValueError`；再按查券的全部条件重新判定，两个上限的计数以加锁读取用券记录的当前状态（不沿用 MySQL 可重复读的事务快照）。通过时写一条 `reserved` 记录（本单券折扣照存调用方给的 `price_order` 券折扣合计，创建与更新时间为当前时间）并返回与查券相同形状的结果；不通过时返回原因、不写记录。只 flush、不提交。同一张券的并发预占在券行锁上排队，两个上限都不会超发——这只在 MySQL 的行锁下成立；SQLite 不支持行锁、SQLAlchemy 在 SQLite 上不生成 FOR UPDATE，SQLite 测试不能复现并发，只验证锁下的判定与写入。
- 核销与释放（`use_coupon(db, order_id, now)` 支付成功时调用、`release_coupon(db, order_id, now)` 取消或支付超时时调用）：以一条条件更新（仅当该订单的记录为 `reserved`）改为 `used` 或 `released` 并写更新时间，返回是否改到；没有记录或状态不是 `reserved` 时不改、返回 `False`，重复调用不出错；`used` 与 `released` 不再改变（已核销的不能释放，退款不恢复次数）。只 flush、不提交。
- 会员中心的两个查询（只读，不写日志）：`list_available_coupons(db, now)` 列出所有在当前时间有效的券，不按两个上限与该会员是否用过过滤（Q19），按截止时间升序、同时刻按 ID，每项给出代码、类型、固定金额或百分比、最低商品消费与截止时间（`AvailableCoupon`）；`list_member_used_coupons(db, member_id)` 列出该会员 `used` 的记录，按创建时间降序、同时刻按 ID 降序，每项给出代码、订单号与本单券折扣（`UsedCoupon`）。
- 偏离：未改设计，未发现须停下的设计问题。说明几处取舍，请审阅：
  - 规范化先核对字符再转大写：`str.upper` 会把个别非 ASCII 字母变成 ASCII（如「ß」变「SS」），先转再核对会放行这类输入；对 ASCII 输入两种写法结果相同。
  - 查券不核对会员是否存在或仍为 active：它只读、会员 ID 只用于每会员计数；会员与订单的核对在预占的锁下做。
  - 预占时券不存在返回 `not_found` 而不抛错（未被引用的券库里可删，查券与下单之间可能消失）；会员或订单不存在按验收抛 `ValueError`。
  - 预占不核对本单券折扣与券面额的关系、也不核对订单状态，照存调用方给的值；二者由之后的下单规则（SHOP-TASK-088）保证。同一订单已释放的记录同样使再次预占抛 `ValueError`，与 `uq_coupon_uses_order_id`（一单至多一张券）一致。
  - 两个上限的计数取该券计次记录的会员 ID 加锁读出后在 Python 里数，不用 `COUNT(*) … FOR UPDATE`，一次加锁读同时得到总次数与每会员次数；两个上限都为空时不读。
  - 核销与释放只按订单 ID 找记录、不核对订单状态，调用时机由 SHOP-TASK-090 接入支付、取消与超时时保证。
  - 可用券列表里的百分比照模型给整数（1 到 100）；只有给 `price_order` 的参数转成 `Decimal`。
- 留给之后的任务：接入会员下单（SHOP-TASK-088、089：计价时查券、下单时在同一事务里按会员 → 订单 → 券的顺序预占）与模拟支付成功、取消及超时（SHOP-TASK-090：核销与释放）；会员中心的优惠券查询接口（SHOP-TASK-095）；后台优惠券维护（SHOP-TASK-098 起）。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句、UX P05 与 P13（Q19）与 HANDOFF 0.46 的 Kelvin 决定自查，并核对与 `price_order` 的参数约定（金额只收 int、百分比只收 `Decimal`）。`tests/test_coupons.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，覆盖：代码规范化（大小写、首尾空白、连字符、中间空格、下划线、「ß」与全角字符、长 3、4、20、21、空串与非字符串）；有效期两端都含（恰在生效与截止时可用，早一秒或晚一秒不可用）与停用；七种原因各一例与几条同时不成立时报前面的一条；最低商品消费恰好达到时可用；查券不写库；总上限与每会员上限各自在计数为上限减一时允许最后一次预占、达到后查券与预占都拒绝且不写记录；`released` 不计入、`reserved` 与 `used` 都计入、上限为空不限、另一会员与另一张券的使用不计入本会员；fixed 与 percent 给出的参数交给 `price_order` 与直接计价结果相同（含 DESIGN 第 4 条算例的 10% 券：券折扣 250、逐件 100、100、50，现金 780、780、390，获得 19 积分）；预占写出的记录与之后查券计数加一、预占时重新判定停用、过期与最低消费、券被另一条语句停用后预占读到停用（不沿用会话缓存）、同单重复预占（含换券与已释放）抛 `ValueError`、会员已注销或不存在、订单属于另一会员或游客或不存在时抛 `ValueError`；核销与释放的状态转换、更新时间、重复调用与互不可逆，以及没有记录时为假；可用券列表含已用满的券与截止恰为当前时间的券、不含停用、过期与未生效的券且顺序正确；使用记录只含该会员的 `used` 且顺序正确。每条测试写明它守住的设计、UX 或决定原句。并发预占不超发依赖 MySQL 行锁，SQLite 测试不能复现，未在真 MySQL 上做并发验证。这些测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/coupons.py——代码规范化：去掉首尾空白后转大写，只由 A 到 Z 与 0 到 9 组成且长 4 到 20 时返回规范化结果，否则返回 None（调用方按查无此券处理）；有效的判定为启用中且生效时间不晚于当前时间、当前时间不晚于截止时间（两端都含）；时间一律不带时区的 UTC
      - 查券函数——入参为数据库会话、代码、会员 ID、商品小计（仙）与当前时间；按顺序判定并返回结果与原因（not_found、inactive、not_started、expired、below_min_spend、total_exhausted、member_exhausted 之一，或可用）：商品小计低于最低商品消费即 below_min_spend；总次数与每会员次数都按状态为 reserved 或 used 的用券记录计算（released 不计），上限为空即不限；可用时同时给出供 app/services/pricing.py 的 price_order 使用的参数（fixed 为 fixed_off 的仙数，percent 为 percent_off 的 Decimal 百分比），不另算折扣；只读，不写库
      - 预占函数——入参为数据库会话、券 ID、订单 ID、会员 ID、本单券折扣（仙，即 price_order 结果里的券折扣合计）、商品小计与当前时间；依次以 SELECT … FOR UPDATE 锁住该会员的行、该订单的行与该券的行（锁顺序与会员注销及积分规则一致：会员 → 订单 → 券），在锁下核对会员为 active 且订单的 member_id 等于该会员，否则抛 ValueError；再按查券的全部条件重新判定，其中两个上限的计数用加锁读（SELECT … FOR UPDATE）取用券记录的当前状态，券本身也以加锁读重新载入（不沿用 MySQL 可重复读的事务快照或 ORM 缓存，并发下单时两个上限都不会超发），通过时写一条 reserved 的用券记录并返回成功，不通过时返回原因且不写记录；同一订单已有用券记录时抛 ValueError；只 flush、不提交
      - 核销函数（支付成功时调用）把该订单 reserved 的用券记录以条件更新改为 used；释放函数（取消或支付超时时调用）把它改为 released；两者都返回是否改到，记录不存在或状态不是 reserved 时不改并返回 False（重复调用不出错）；used 与 released 不再改变（退款不恢复次数）；都写更新时间、只 flush 不提交
      - 会员中心用的两个只读函数——可用券列表：所有在当前时间有效的券（不按两个上限与该会员是否用过过滤，Q19），按截止时间升序、同时刻按 ID，每项给出代码、类型、固定金额或百分比、最低商品消费与截止时间；某会员的使用记录：该会员状态为 used 的用券记录，按创建时间降序、同时刻按 ID 降序，每项给出代码、订单号与本单券折扣；不写日志
      - tests/test_coupons.py 用 SQLite 内存库按模型建表验证意图——代码规范化（大小写、首尾空白、非法字符、长度）；有效期两端都含、启停；七种原因各一例；最低商品消费恰好达到时可用；总上限与每会员上限各自在当前计数为上限减一时允许最后一次预占、达到上限后拒绝，released 不计入、used 与 reserved 都计入、上限为空不限，另一会员的使用不计入本会员；fixed 与 percent 给出的 price_order 参数与 SHOP-TASK-002 的计价结果一致（含 DESIGN 第 4 条算例的 10% 券）；预占后再查券计数增加、同单重复预占抛 ValueError、会员已注销或订单不属于该会员时抛 ValueError；核销与释放的状态转换与重复调用；可用券列表含已用满的券、不含停用与过期的券且顺序正确；使用记录只含 used；每条测试写明它守住的是设计、UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-085 记录段（代码规范化、查券顺序与原因、计数口径、预占的行锁、核销与释放、会员中心的两个查询、偏离、验证到什么程度，并写明并发预占只在 MySQL 的行锁下成立、SQLite 测试不能复现，接入下单与支付留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/pricing.py、模型、迁移、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```
