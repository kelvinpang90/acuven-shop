# SHOP-TASK-115 Stripe 事件与退款尝试的数据模型

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「数据模型」的 StripeCheckout / StripeEvent / StripeRefund 一行与「Stripe 测试环境支付」第 6、7、8 条（事件 ID 去重；每条退款尝试有自己的本站 ID，同一来源至多一条不是 failed 的尝试，由数据库唯一约束保证），在 SHOP-TASK-110 的会话记录表之上建事件表与退款尝试表：`app/models/stripe.py` 加 `StripeEvent` 与 `StripeRefund`（`app/models/__init__.py` 导出两者；`StripeCheckout` 未改，模块说明补上所依据的设计原句）、迁移 `alembic/versions/20261015_0021_stripe_refunds.py`（revision `0021`，down_revision `0020`）；测试见 `tests/test_stripe_refund_models.py`。由原 SHOP-TASK-110 于 2026-10-15 拆出。
- 两张表都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；时间一律不带时区的 UTC；金额一律整数仙；外键一律 ON DELETE RESTRICT；检查约束只用比较、IN、NOT IN、IS NULL / IS NOT NULL 与 AND / OR，SQLite 与 MySQL 都能执行。
- 事件表 `stripe_events`：每条是一个已在同一事务里处理过的 Stripe 事件，供 webhook 按事件 ID 去重（已提交过的事件直接回 200）。
  - 列：`event_id` String(255)（唯一）、`event_type` String(64)、`outcome` String(16)（processed：第 4、5 条的 Checkout 事件或退款状态事件已处理；ignored：其他类型回 200 不处理）、`processed_at`。全部非空。不存事件原文。
  - 唯一约束：`uq_stripe_events_event_id`。没有外键与普通索引。
  - 检查约束：`ck_stripe_events_outcome_valid`：`outcome IN ('processed', 'ignored')`
- 退款尝试表 `stripe_refunds`：每条是一次 Stripe 退款尝试，自增主键即本站 ID（规则任务用作 Stripe 幂等键并写进退款元数据）。
  - 列：`payment_intent_id` String(255)、`source_type` String(16)（refund_request：第 8 条批准的退款申请；checkout：第 6 条迟到或不符付款的会话记录）、`refund_request_id`（可空）→ `refund_requests.id`、`checkout_id`（可空）→ `stripe_checkouts.id`、`stripe_refund_id` String(255)（可空，唯一，不校验前缀）、`amount_sen`、`status` String(16)（pending / succeeded / failed）、`call_count`（服务端默认 0）、`last_error_type` String(64)（可空，只存 Stripe 错误的类别代码）、`created_at`、`succeeded_at`（可空）、`last_checked_at`（可空）、`active_refund_request_id`（可空）、`active_checkout_id`（可空）。其余列全部非空。不存客户资料与 Stripe 请求、响应原文。
  - 外键：`fk_stripe_refunds_refund_request_id_refund_requests`（`refund_request_id` → `refund_requests.id`，RESTRICT）、`fk_stripe_refunds_checkout_id_stripe_checkouts`（`checkout_id` → `stripe_checkouts.id`，RESTRICT）；两个活动列不建外键。
  - 唯一约束：`uq_stripe_refunds_stripe_refund_id`、`uq_stripe_refunds_active_refund_request_id`、`uq_stripe_refunds_active_checkout_id`。
  - 普通索引：`ix_stripe_refunds_payment_intent_id`（`payment_intent_id`）、`ix_stripe_refunds_status_created_at`（`status`, `created_at`）。
  - 检查约束：
    - `ck_stripe_refunds_source_type_valid`：`source_type IN ('refund_request', 'checkout')`
    - `ck_stripe_refunds_status_valid`：`status IN ('pending', 'succeeded', 'failed')`
    - `ck_stripe_refunds_amount_positive`：`amount_sen > 0`
    - `ck_stripe_refunds_call_count_non_negative`：`call_count >= 0`
    - `ck_stripe_refunds_refund_request_source`：`source_type <> 'refund_request' OR (refund_request_id IS NOT NULL AND checkout_id IS NULL)`
    - `ck_stripe_refunds_checkout_source`：`source_type <> 'checkout' OR (checkout_id IS NOT NULL AND refund_request_id IS NULL)`
    - `ck_stripe_refunds_failed_has_no_active_source`：`status <> 'failed' OR (active_refund_request_id IS NULL AND active_checkout_id IS NULL)`
    - `ck_stripe_refunds_active_refund_request_is_source`：`status NOT IN ('pending', 'succeeded') OR source_type <> 'refund_request' OR (active_refund_request_id IS NOT NULL AND active_refund_request_id = refund_request_id AND active_checkout_id IS NULL)`
    - `ck_stripe_refunds_active_checkout_is_source`：`status NOT IN ('pending', 'succeeded') OR source_type <> 'checkout' OR (active_checkout_id IS NOT NULL AND active_checkout_id = checkout_id AND active_refund_request_id IS NULL)`
    - `ck_stripe_refunds_succeeded_has_succeeded_at`：`status <> 'succeeded' OR succeeded_at IS NOT NULL`
  - 「同一来源至多一条不是 failed 的尝试」的表示：pending 与 succeeded 时，与来源类别对应的活动列等于来源（`active_refund_request_id` = `refund_request_id`，或 `active_checkout_id` = `checkout_id`）、另一列为空；failed 时两列都为空；由上面三条检查约束保证。两列各有唯一约束，空值不参与唯一比较（MySQL 与 SQLite 都如此），所以同一来源的 failed 尝试不限条数，而不是 failed 的至多一条。把尝试改为 failed（含 succeeded → failed）时须同时清空活动列，释放来源，手动重试才能新建尝试。
  - 0021 的 upgrade 依次建 `stripe_events`、`stripe_refunds` 及两个索引；downgrade 倒序：先删两个索引（都不以外键列开头，MySQL 不拿它们当外键的索引，可单独删），再删 `stripe_refunds`、`stripe_events`。
- 偏离：未改设计，未发现须停下的设计问题。说明几处取舍，请审阅：
  - 列名：来源类别为 `source_type`，来源会话为 `checkout_id`，Stripe 退款 ID 为 `stripe_refund_id`（区别于本站 ID），调用次数为 `call_count`，最后的错误类别为 `last_error_type`，上次复查时间为 `last_checked_at`；事件 ID 为 `event_id`、类型为 `event_type`、处理结果为 `outcome`、处理时间为 `processed_at`。状态与来源类别最长取 16（最长取值 refund_request 为 14 个字符），处理结果取 16。
  - 活动列的检查约束按来源类别分两条写，并要求另一活动列为空：只写「等于来源」时来源列为空会求值为未知而放行，另由来源两条约束拒绝来源列为空。
  - succeeded 改为 failed 后成功时间保留，库里不要求 failed 时成功时间为空；库也不约束状态迁移顺序（只进不退）、同一 PaymentIntent 的 pending 与 succeeded 合计不超过付款金额、PaymentIntent 与来源会话或订单的付款一致、来源的状态（退款申请已批准、会话已付款），这些由之后的规则任务保证。
  - MySQL 默认排序规则不区分大小写：各取值的 IN 在 MySQL 上也接受大写，取值由写入方用模型常量写小写；SQLite 上按区分大小写拒绝（与 SHOP-TASK-110 相同）。
  - 外键列 `refund_request_id`、`checkout_id` 不另建普通索引，MySQL 为外键自动建的索引由 `alembic check` 的 MySQL 比较忽略（与积分流水的 `refund_request_id` 相同）。
- 留给之后的任务：webhook（签名校验、事件去重与改动同一事务）、迟到与不符付款的全额退款（SHOP-TASK-117）、Stripe 退款规则（批准时建尝试并调用、状态映射与只进不退、手动重试、定时复查与 24 小时对账、同一 PaymentIntent 的合计核对）与后台退款详情的 Stripe 部分，都由之后的任务实现（规划块「已阻塞」所列）；本任务只建表与约束，未加接口，未改 `app/main.py`、`app/services/` 与前端。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.12 原句自查，并逐个核对 0021 的列类型、可空性、服务端默认值、约束名与表达式、外键删除行为、唯一约束、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束、服务端默认值与表选项，这几项只经人工核对）。`tests/test_stripe_refund_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，写法照 `tests/test_point_models.py`；先写入三条事件（两种处理结果）与六条退款尝试（两种来源、三种状态，各用不同的来源）作对照，并以不以 re_ 开头的 Stripe 退款 ID、succeeded 改为 failed 后成功时间保留并可新建尝试作对照；再覆盖：每个检查约束至少一个反例（先在独立连接上逐条求值该表全部检查约束，断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一，另断言用例覆盖退款尝试表上全部检查约束），含未知处理结果、来源类别取未知值、状态为 requires_action 或 canceled、金额为 0 与负、调用次数为负、来源类别与来源列不一致（缺来源或另带另一种来源）、failed 的尝试活动列非空、pending 与 succeeded 的尝试活动列为空、活动列指向另一来源或另占另一种活动列、succeeded 缺成功时间；两张表除自增主键外每个非空列写入 NULL 被非空约束拒绝；同一事件 ID 两次被唯一约束拒绝；同一退款申请或同一会话已有一条不是 failed 的尝试时再写一条被活动列的唯一约束拒绝，而两条 failed 加一条 pending 可以并存、pending 改为 failed 并清空活动列后又可写 pending，不同来源各有一条 pending 互不影响；重复的 Stripe 退款 ID 被唯一约束拒绝，空的 Stripe 退款 ID 不互相冲突，同一 PaymentIntent 可有多条尝试；尝试指向不存在的退款申请或会话、删除被尝试引用的退款申请或会话被外键拒绝（另以未被引用的可删作对照）；调用次数不给时为 0，金额与调用次数读回为 int；两张表的列正是所列的对账字段、只有指定的列可空、唯一约束与索引存在、外键是 RESTRICT 且活动列没有外键。每条测试写明它守住的设计原句，只出自本任务验收标准的约束注明「验收：」。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 在 app/models/stripe.py 增加 StripeEvent 与 StripeRefund 两个模型（写法与约束约定同 SHOP-TASK-110 的 StripeCheckout），app/models/__init__.py 导出它们；不改 StripeCheckout；不存任何客户资料与 Stripe 请求、响应或事件原文；模块说明补上所依据的设计原句；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - 事件表 stripe_events——事件 ID（最长 255，全表唯一）、类型（最长 64）、处理结果（processed 或 ignored）、处理时间，不存事件原文
      - 退款尝试表 stripe_refunds（每条是一次尝试）——PaymentIntent ID（最长 255）、来源类别（refund_request 或 checkout）、退款申请（外键 refund_requests，可空）、来源会话记录（外键 stripe_checkouts，可空）、Stripe 退款 ID（可空，最长 255，全表唯一；核验显示 FPX、GrabPay 的退款 ID 前缀不是 re_，不校验前缀）、金额（仙，大于 0）、状态（pending、succeeded、failed）、调用次数（非负整数，服务端默认 0）、最后的错误类别（可空，最长 64）、创建时间、成功时间（可空）、上次复查时间（可空）、活动来源列 active_refund_request_id 与 active_checkout_id（都可空）；检查约束逐条保证——来源类别只能取 refund_request 或 checkout；来源类别为 refund_request 时退款申请非空、来源会话为空，为 checkout 时反之；状态为 failed 时两个活动列都 IS NULL，否则与来源类别对应的活动列 IS NOT NULL 且等于该来源、另一个 IS NULL；状态为 succeeded 时成功时间非空；两个活动列各建唯一约束（同一来源至多一条不是 failed 的尝试），按 PaymentIntent ID 与（状态、创建时间）建索引
      - 新迁移文件 alembic/versions/20261015_0021_stripe_refunds.py，revision 为 0021、down_revision 为 0020；upgrade 依次建 stripe_events、stripe_refunds 及其索引，downgrade 倒序删除；约束名按命名约定逐个写出；迁移只在 MySQL 上执行；检查约束表达式须 SQLite 与 MySQL 都支持；PR 正文逐表列出检查约束、外键、唯一约束与索引
      - 新文件 tests/test_stripe_refund_models.py 用 SQLite 内存库（开启外键检查）按模型建表，写法照 tests/test_point_models.py——先写入合法的事件与退款尝试（两种来源与三种状态）作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（含除自增主键外每个非空列写入空值被拒、同一事件 ID 两次、未知处理结果、同一退款申请两条不是 failed 的尝试被拒而一条 failed 加一条 pending 可以、同一会话来源同理、failed 的尝试活动列非空、pending 的尝试活动列为空、来源类别取未知值、来源类别与来源列不一致、succeeded 缺成功时间、金额为 0、调用次数为负）；每条测试写明它守住的是设计的哪一句；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 无差异；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-115 记录段（两张表的含义与约束、活动来源列如何表示「至多一条」、偏离、验证到什么程度，并写明规则留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 StripeCheckout、其他已有模型与已有迁移、.platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py、app/services/ 与前端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e），不合并、不直推
```
