# SHOP-TASK-123 Stripe 退款尝试的告警时间列

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「数据模型」的 StripeCheckout / StripeEvent / StripeRefund 一行（错误类别与时间）与「Stripe 测试环境支付」第 8 条（24 小时后对账找不到或无法列完时告警、成功后又失败时告警、请求被明确拒绝时告警），以及 `docs/HANDOFF.md` 0.54 记录的 Kelvin 2026-10-19 决定（退款尝试表补告警时间列，给会反复出现的告警去重），在 SHOP-TASK-115 的退款尝试表上加告警时间列：`app/models/stripe.py` 的 `StripeRefund` 加 `alerted_at`（模块说明补上所依据的设计原句与 HANDOFF 0.54）、迁移 `alembic/versions/20261019_0022_stripe_refund_alerts.py`（revision `0022`，down_revision `0021`）；测试见 `tests/test_stripe_refund_models.py`。
- 新列 `stripe_refunds.alerted_at`：DateTime，不带时区的 UTC，可空，与 `StripeCheckout.alerted_at` 同样写法。含义：会反复出现的告警（24 小时后对账找不到或列不完、Stripe 退款对象与尝试不符）的首次告警时间；为空表示这条尝试还没发过这类告警，这类告警每条尝试至多一次。状态改动引起的告警（Stripe 以请求错误明确拒绝创建、成功后又变为 failed）随改动只发生一次，不看也不写这一列，所以先因对账告警过的尝试之后成功又失败时照样告警（HANDOFF 0.54 所说登记预审指出的漏告警）。
  - 不在任何检查约束、唯一约束、外键或索引里；其他列、约束与索引，以及 `StripeCheckout`、`StripeEvent` 都未改。
- 迁移 0022：upgrade 只 `add_column("stripe_refunds", alerted_at DateTime NULL)`，已有行为空（即都还没发过这类告警）；downgrade 只 `drop_column`（列不被约束或索引引用，可直接删）。不 import app；迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。MySQL 上新列追加在表末，模型里排在 `succeeded_at` 之后，列序不影响 `alembic check`。
- 测试改动：`test_tables_store_only_reconciliation_fields` 的退款尝试列集合与 `test_only_designated_refund_columns_are_nullable` 的可空列集合各加入 `alerted_at`，旁边注释写明原因（HANDOFF 0.54）；其余已有断言未改。新增 `test_alerted_at_is_nullable_writable_and_outside_constraints`，守住 HANDOFF 0.54「退款尝试表补告警时间列……这类告警每条尝试至多一次」：不给时读回为空；两种来源、三种状态的尝试都能写入并原样读回告警时间，求值辅助函数不报约束不成立；没有检查约束、唯一约束或索引引用这一列；带告警时间的第二条不是 failed 的尝试仍被同一来源的活动列唯一约束拒绝。
- 偏离：未改设计，未发现须停下的设计问题。库只提供这一列，不约束「至多一次」：只在为空时告警并写入、写入后不再改，由之后的规则任务保证（与 `StripeCheckout.alerted_at` 相同）。
- 留给之后的任务：告警的判断与写入（对账找不到或列不完、退款对象与尝试不符时在列为空时告警并写入时间；明确拒绝与成功后又失败的告警随状态改动发出，不读写此列），以及运营告警邮件接入前先记 error 级日志，都由之后的退款规则任务（SHOP-TASK-124、126 等）实现；本任务只加列与迁移，未改 `app/services/`、`app/api/` 与前端。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 8 条与 HANDOFF 0.54 原句自查，并核对 0022 的列类型与可空性同模型、同 0020 的 `stripe_checkouts.alerted_at` 写法一致。上述测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/models/stripe.py 的 StripeRefund 增加可空的 alerted_at（DateTime，不带时区，与 StripeCheckout.alerted_at 同样写法），说明它记会反复出现的告警（24 小时后对账找不到或列不完、Stripe 退款对象与尝试不符）的首次告警时间，这类告警每条尝试至多一次；状态改动引起的告警（请求被明确拒绝、成功后又失败）随改动只发生一次，不看也不写它；不改其他列、约束与 StripeCheckout、StripeEvent；模块说明补上所依据的设计原句与 HANDOFF 0.54；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - 新迁移文件 alembic/versions/20261019_0022_stripe_refund_alerts.py，revision 为 0022、down_revision 为 0021；upgrade 只给 stripe_refunds 加 alerted_at 列（可空，已有行为空），downgrade 只删除它；迁移只在 MySQL 上执行
      - tests/test_stripe_refund_models.py 只做两处改动并写明原因——test_tables_store_only_reconciliation_fields 的退款尝试列集合加入 alerted_at，test_only_designated_refund_columns_are_nullable 的可空列集合加入 alerted_at；另加一条测试验证 alerted_at 可空、可写入时间且不影响现有约束；其余断言一字不改
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 无差异；每条新测试写明它守住的是设计或 HANDOFF 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/task-records/SHOP-TASK-123.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；新列的含义、迁移、偏离、验证到什么程度，并写明告警的写入留给之后的退款规则任务），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改其他模型与已有迁移、app/services/、app/api/、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.54），不合并、不直推
```
