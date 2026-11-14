# SHOP-TASK-246 AI 装饰提案的每日计数与调用记录表（模型与迁移 0029）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 13 条「限制」一段：只记调用次数与用量，不存描述与回答原文，不碰个人资料与金额。只建表与约束；计数与调用的服务见 SHOP-TASK-248。不改服务、接口、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。依赖 SHOP-TASK-230（迁移 0028）。
- [x] 模型 `app/models/promotion_ai.py`（供 SHOP-TASK-248 引用）：

  | 表 | 模型 | 列 | 类型 | 说明 |
  | --- | --- | --- | --- | --- |
  | `promotion_ai_daily_usage` | `PromotionAiDailyUsage` | `usage_date` | Date，主键（`pk_promotion_ai_daily_usage`） | 马来西亚日期；每天至多一行，不另设 id 列 |
  | `promotion_ai_daily_usage` | `PromotionAiDailyUsage` | `call_count` | 非空 Integer，Python 默认 0 | 当天已占用的调用次数 |
  | `promotion_ai_calls` | `PromotionAiCall` | `id` | Integer 主键（`pk_promotion_ai_calls`） | |
  | `promotion_ai_calls` | `PromotionAiCall` | `called_at` | 非空、不带时区的 DateTime（UTC），Python 默认写入时刻 | 调用时间 |
  | `promotion_ai_calls` | `PromotionAiCall` | `result` | 非空 String(16) | 结果类别，见下 |
  | `promotion_ai_calls` | `PromotionAiCall` | `input_tokens` | 可空 Integer | 输入 token 数；没拿到用量时为空 |
  | `promotion_ai_calls` | `PromotionAiCall` | `output_tokens` | 可空 Integer | 输出 token 数；没拿到用量时为空 |

- [x] 结果类别常量：`AI_RESULT_OK`（`ok`，合格的提案）、`AI_RESULT_INVALID`（`invalid`，模型回答不合格）、`AI_RESULT_TIMEOUT`（`timeout`，超时）、`AI_RESULT_ERROR`（`error`，其余失败，含 Billing Hub 拒绝），元组 `AI_RESULTS`。两个模型已加进 `app/models/__init__.py` 的 import 与 `__all__`。
- [x] 检查约束（全名按 `app/db/base.py` 的命名约定）：
  - `ck_promotion_ai_daily_usage_call_count_non_negative`：`call_count >= 0`
  - `ck_promotion_ai_calls_result_valid`：`result IN ('ok', 'invalid', 'timeout', 'error')`
  - `ck_promotion_ai_calls_input_tokens_non_negative`：`input_tokens IS NULL OR input_tokens >= 0`
  - `ck_promotion_ai_calls_output_tokens_non_negative`：`output_tokens IS NULL OR output_tokens >= 0`
- [x] 迁移 `alembic/versions/20261111_0029_promotion_ai.py`（revision 0029，down_revision 0028）：照 0027 不 import app，表选项与检查约束文本自带为 `TABLE_OPTIONS`、`CHECK_CONSTRAINTS`，与模型逐字相同。upgrade 建两张表；downgrade 倒序直接删两张表（只是计数与调用记录，不影响订单与活动）。两张表都不建外键。
- [x] 测试 `tests/test_promotion_ai_models.py`（照 `tests/test_promotion_models.py`，SQLite 内存库）：计数表主键只有日期、同一日期的第二行被拒绝、当天行次数默认 0、次数为负被拒绝；记录表只有五列且没有外键、四种结果类别都能写入、其他类别被拒绝、token 数可空与可为 0、为负被拒绝；经 importlib 载入迁移核对 revision、down_revision、表名与约束常量和模型逐字相同。每条测试的文档字符串写明它守住的第 13 条原句。
- 偏离与落实细节：
  - 落实写法只给出 `call_count_non_negative` 与 `result_valid` 两个约束名；token 数的「>= 0」按同一命名方式写为 `input_tokens_non_negative`、`output_tokens_non_negative`，表达式带 `IS NULL OR` 写明可空。
  - `call_count` 只有 Python 默认 0，没有服务端默认；SHOP-TASK-248 的「不存在则插入」须显式写 0 或经 ORM/Core 插入（Core 的 `insert()` 也会带上 Python 默认）。
  - `called_at` 不建索引（验收未要求；限额按计数表判断，不扫记录表）。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 13 条，人工核对迁移常量与模型文本一致并由测试逐字比对。迁移在 MySQL 8 上的往返与 alembic check、全部后端测试由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。
