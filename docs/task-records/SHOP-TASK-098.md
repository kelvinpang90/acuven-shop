# SHOP-TASK-098 后台优惠券的列表、新建与停用规则

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）Coupon / CouponUse 与「计价、优惠、积分与库存」第 1、7 条、`docs/UX.md` 0.10 A05，以及 `docs/HANDOFF.md` 0.48 的 Kelvin 决定，实现 `app/services/admin_coupons.py` 的后台业务规则。只新增、停用和列表，不做接口或页面，不改模型、迁移、`app/services/coupons.py`、`app/services/admin_auth.py`、设计或部署配置；未加表或依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.48（优惠券）。拆分：业务规则层（3 个允许路径）。
- 列表：每页 30 张，按创建时间和 ID 倒序；使用单个按券分组的查询统计 `reserved` 与 `used`，不把 `released` 计入。页外返回空页但保留总数；把保存的 UTC 起止时刻按固定 UTC+8 显示为马来西亚日期，只读且不写审计。
- 新建：代码沿用 SHOP-TASK-085 的去首尾空白、ASCII 字母数字与大写规范化；校验 fixed / percent 的互斥金额、最低商品消费、日期和可空次数上限。日期保存为马来西亚日的首秒与末秒换算后的无时区 UTC；唯一代码冲突回滚保存点并报 `CouponCodeTaken`。成功时只 flush，写 `admin_coupon_created` 审计及规范化代码。
- 停用：条件更新仍启用的券，写 `admin_coupon_disabled` 审计；重复停用幂等且不另写审计，不存在报 `CouponNotFound`。不编辑、重新启用或删除，也不改既有用券记录；返回值的已用数照 `COUNTED_USE_STATUSES` 计，故后台修改只影响之后的新订单。
- 偏离：未改设计；服务层的请求、列表页和行值对象为后续接口提供显式边界，未增加接口字段或路由。
- 留给之后的任务：SHOP-TASK-099 负责后台优惠券的列表、新建与停用接口。
- 验证到什么程度：`tests/test_admin_coupons.py` 以 SQLite 内存库按模型建表、每条连接打开外键检查，自建管理员；覆盖代码与日期换算和创建审计、回滚、全部校验拒绝与边界、规范化重复、30 张分页和排序、reserved / used 计数、停用审计与幂等、以及停用后预占记录不变和 rollback 恢复启用。每条测试说明守住的设计、UX、Kelvin 决定或本任务契约；由 PR 必需 CI 的 backend 执行。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/admin_coupons.py 的列表函数——入参为数据库会话与页码（从 1 起）；每页 30 张，按创建时间从新到旧、同时刻按 ID 从大到小；返回总数与该页各券的 ID、代码、类型、固定金额（仙）或百分比、最低商品消费（仙）、起始日与截止日（把 starts_at、ends_at 按固定 UTC+8 换算成马来西亚日期，写法同 app/services/sms_budget.py 的 MALAYSIA_UTC_OFFSET）、总使用上限、每会员使用上限（为空即不限）、已用次数（该券状态为 reserved 或 used 的用券记录数，即 app/models/coupon.py 的 COUNTED_USE_STATUSES，用一条分组查询取得，不逐张查询）与是否启用；页码超出时该页为空、总数照常；只读，不写审计
      - 新建函数——入参为数据库会话、管理员 ID、新建请求（代码原文、类型、固定金额或百分比、最低商品消费、起始日与截止日两个日期、两个可空的上限）与当前时间（不带时区的 UTC）；代码经 SHOP-TASK-085 的代码规范化（去掉首尾空白、转大写、只由 A 到 Z 与 0 到 9 组成且长 4 到 20），不成立即抛 CouponInvalid；类型只能是 fixed 或 percent，fixed 时固定金额为 1 到 10000000 的整数仙且百分比为空，percent 时百分比为 1 到 100 的整数且固定金额为空；最低商品消费为 0 到 10000000 的整数仙；起始日不晚于截止日，截止日不早于当前时间的马来西亚日期；两个上限为空或 1 到 1000000 的整数；任一不满足抛 CouponInvalid（异常不带输入值）
      - 新建通过时——starts_at 为起始日 00:00:00 马来西亚时间、ends_at 为截止日 23:59:59 马来西亚时间，都换成不带时区的 UTC 写入（同一天起止也满足 ends_at 晚于 starts_at）；启用状态为启用；在保存点里插入，撞上代码的唯一约束即回滚该保存点并抛 CouponCodeTaken（代码库里存大写，所以大小写不同的同一代码也算重复）；成功后用 SHOP-TASK-035 的 record_audit 写一条 admin_coupon_created（对象类别 coupon、对象 ID 为新券 ID、new_value 为规范化后的代码，不写其他字段）；只 flush、不提交；返回新券（字段同列表，已用为 0）
      - 停用函数——入参为数据库会话、管理员 ID、券 ID 与当前时间；以条件更新（该券仍为启用）改为停用，改到时写一条 admin_coupon_disabled（对象类别 coupon、old_value 为 active、new_value 为 disabled）并返回该券（字段同列表，已用次数按该券 COUNTED_USE_STATUSES 的用券记录数计，不得固定为 0）；没改到时该券不存在抛 CouponNotFound，已停用则返回当前状态、不写审计（重复停用不出错）；只 flush、不提交；停用不改任何用券记录——已预占的订单照常支付与核销（后台修改只影响新订单），查券与预占由 SHOP-TASK-085 按启用状态拒绝之后的订单；不提供编辑、重新启用与删除
      - tests/test_admin_coupons.py 用 SQLite 内存库按模型建表（连接时执行 PRAGMA foreign_keys=ON）、自建管理员，照下面已实跑通过的写法——固定当前时间 NOW 为 2026-10-14 15:59:59（UTC，即马来西亚 10 月 14 日最后一秒）；用券记录由辅助函数逐条建一名 active 会员与一张属于该会员、claim_status 为 CLAIM_NOT_CLAIMABLE 的待支付订单。测试：其一，新建时小写与首尾空白的代码存为大写，starts_at 为 2026-10-13 16:00:00、ends_at 为 2026-10-14 15:59:59（起止日都为 10-14），审计为 admin_coupon_created、对象类别 coupon、new_value 为代码、old_value 为空；其二，新建后 rollback 查不到券；其三，各一例被拒（用 pytest.mark.parametrize 时参数名不得取 request，那是 pytest 的保留名，收集阶段即报错；可取 changes 或 payload）——代码含非法字符、长 3、长 21，fixed 金额 0 与 10000001，percent 百分比 0 与 101，percent 带固定金额、fixed 带百分比，截止早于起始（起始 10-15、截止 10-14）与截止早于今天（起止都为 10-13）分开两例，最低商品消费 -1 与 10000001，两个上限各为 0 与 1000001；其四，接受的边界——代码长 4 与 20，fixed 1 与 10000000，percent 1 与 100，最低商品消费 0 与 10000000，两个上限为空、1 与 1000000，同一天起止可以，截止 10-14 在 NOW 可以、在 2026-10-14 16:00:00 被拒；其五，大小写不同的重复代码抛 CouponCodeTaken，之后审计恰为 1 条、券恰为 1 张；其六，建 31 张起止日都为 10-15 的券，第 0、1 张创建于 NOW 加 300 秒（同一时刻），第 n 张（n 从 2 起）创建于 NOW 加 200 减 n 秒，第 1 页依次为第 1、0、2 至 29 张的 ID，第 2 页只有第 30 张且起止日显示为 10-15（starts_at 为前一天 16:00 UTC），第 3 页为空、总数照常为 31；其七，一张券 reserved、used、released 各一条、另一张券 reserved 一条，列表的已用分别为 2 与 1；其八，停用写一条审计（对象类别 coupon、old_value active、new_value disabled），重复停用返回停用状态且不再写，不存在的 ID 抛 CouponNotFound；其九（单独一条测试，不与其八合并），建券并加一条 reserved 用券记录后先调用 db.commit()、再停用，该用券记录在停用后不变、返回的已用为 1，rollback 后券仍为启用。每条测试的说明写明它守住的是设计、UX 或 Kelvin 决定的哪一句；每页 30 张与只 flush 不提交来自本任务契约，说明照实写任务契约，不冒称设计；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-098 记录段（列表、新建的校验与换算、停用、审计、偏离、验证到什么程度，并写明接口留给 SHOP-TASK-099），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/coupons.py、app/services/admin_auth.py、模型、迁移、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.48），不合并、不直推
```
