# SHOP-TASK-229 节日促销活动的数据表（模型与迁移 0027）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「数据模型」的 Promotion / PromotionRule / PromotionDecoration 一行与「节日促销活动」第 1–3、12 条；素材包与装饰位变体的 id 以 `docs/design/tokens/promotions.json`（HANDOFF 0.94）为准。只建结构，不涉及金额计算与个人资料。不改订单表（活动快照列见 SHOP-TASK-230），AI 每日计数表留给之后的 AI 任务；不写服务、接口与前端，不装依赖，不改设计、`.platform/`、CI 与部署配置。
- [x] 模型 `app/models/promotion.py`，七个模型已加进 `app/models/__init__.py` 的 import 与 `__all__`：

  | 表名 | 模型 | 要点 |
  | --- | --- | --- |
  | `promotions` | `Promotion` | name String(100) 非空；status String(16)；starts_at、ends_at、ended_early_at 可空 DateTime；coupon_combinable、points_combinable（Python 默认 False）；version（Python 默认 1）；created_at、updated_at。检查约束 `name_not_empty`、`status_valid`、`published_has_times`、`ends_after_starts`、`ended_early_within`、`version_positive`；索引 `ix_promotions_status_starts_at` |
  | `promotion_rules` | `PromotionRule` | promotion_id → promotions.id CASCADE；position（`position_positive`，与 promotion_id 联合唯一）；kind String(24)（`kind_valid`）；discount_mode String(8) 与 percent、amount_sen、buy_quantity、get_quantity、get_percent、threshold_sen 可空；每类一条参数约束 `item_discount_params`、`buy_x_get_y_params`、`spend_discount_params`、`free_shipping_params` |
  | `promotion_rule_products` | `PromotionRuleProduct` | rule_id → promotion_rules.id CASCADE，product_id → products.id RESTRICT，两列联合唯一 |
  | `promotion_rule_categories` | `PromotionRuleCategory` | rule_id → promotion_rules.id CASCADE，category_id → categories.id RESTRICT，两列联合唯一 |
  | `promotion_spend_tiers` | `PromotionSpendTier` | rule_id → promotion_rules.id CASCADE；position（`position_range` 1–5，与 rule_id 联合唯一）；threshold_sen（`threshold_range`）；amount_sen、percent 可空（`amount_or_percent`、`amount_below_threshold`、`percent_range`） |
  | `promotion_decorations` | `PromotionDecoration` | promotion_id → promotions.id CASCADE 且唯一；pack、palette、ambient、home_poster、list_strip、card_badge、detail_ribbon、cart_progress 为 String(16) 非空无默认，各有 `<列名>_known`；poster_mode（`poster_mode_valid`，Python 默认 template）；`poster_desktop_` 与 `poster_phone_` 各五列与三条约束（`*_all_or_none`、`*_source_format_valid`、`*_display_size_range`，照店铺标志）；poster_alt、title、subtitle、badge、strip 的 `_en`、`_zh`、`_ms` 可空；updated_at |
  | `promotion_publish_locks` | `PromotionPublishLock` | singleton_slot 唯一且恒为 1（`singleton_slot_one`），迁移写入唯一的一行 |

- [x] 常量（`app.models.promotion`）：`PROMOTION_PACKS`（7 个素材包 id，同 promotions.json 的 packs 顺序）、`PROMOTION_SLOTS`（装饰位 id → 变体 id 元组，同 slots 顺序）、`PALETTE_KEEP_THEME`（theme）、`AMBIENT_NONE`（none）、`DECORATION_CHOICES`（装饰表每个 ID 列允许的取值，`_known` 约束由它生成）；状态 `STATUS_DRAFT`、`STATUS_PUBLISHED`、`PROMOTION_STATUSES`；规则类别 `KIND_ITEM_DISCOUNT`、`KIND_BUY_X_GET_Y`、`KIND_SPEND_DISCOUNT`、`KIND_FREE_SHIPPING`、`RULE_KINDS`；优惠方式 `MODE_PERCENT`、`MODE_AMOUNT`、`DISCOUNT_MODES`；海报模式 `POSTER_MODE_TEMPLATE`、`POSTER_MODE_IMAGE`、`POSTER_MODES`；取值上限 `NAME_MAX_LENGTH`、`PERCENT_MIN`、`DISCOUNT_PERCENT_MAX`、`GET_PERCENT_MAX`、`ITEM_AMOUNT_MAX_SEN`、`QUANTITY_MIN`、`QUANTITY_MAX`、`THRESHOLD_MAX_SEN`、`SPEND_TIERS_MAX`、`POSTER_SOURCE_FORMATS`、`POSTER_DISPLAY_MAX`；`SINGLETON_SLOT`。检查约束里的取值都由这些常量生成。
- [x] 迁移 `alembic/versions/20261110_0027_promotions.py`（revision 0027，down_revision 0026）：照 0025 不 import app，检查约束按表自带为 `CHECK_CONSTRAINTS`（全名与文本，与模型逐字相同），主键、外键、唯一约束与索引的名字按 `app/db/base.py` 的命名约定逐个写出；upgrade 按外键依赖建七张表并写入发布锁那一行；downgrade 先读出 promotions 的行数，大于 0 即抛 RuntimeError、库保持原样，否则按 `TABLES` 的逆序删表。
- [x] 测试 `tests/test_promotion_models.py`（SQLite 内存库、打开外键检查，按模型建表）：每条检查约束的正例与只让该约束不成立的反例（断言报出该约束名）；规则、范围、满额档与装饰的唯一约束，以及发布锁的单行；删除活动时规则、范围、满额档与装饰随之删除，被范围引用的商品与分类不能删除；常量与 promotions.json 的 packs、slots、keepThemePalette、ambient 的 none 逐项相同，且每个素材包的变体与 slots 相同；海报两组约束就是店铺标志三条换前缀；经 importlib 载入迁移，核对 revision、down_revision、表集合与建表顺序、各表检查约束常量与模型逐字相同、装饰列常量。每条测试的文档字符串写明它守住的设计句子或本任务的落实写法。
- 偏离与落实细节：
  - 规则的 `item_discount_params` 与 `spend_discount_params` 先判 `discount_mode IS NOT NULL`：检查约束求得 NULL 时放行，不先判非空则方式为空的商品级打折会被放行。验收没有单列 discount_mode 的取值约束，未知方式由这两条参数约束拒绝（其余两类要求它为空）。
  - `ambient_known` 的取值为三个变体再加 none（顺序 garland、corners、fall、none）；`palette_known` 为 theme 再加七个素材包。
  - 测试另加了「每个素材包列出的变体与 slots 相同」：装饰表按列单独检查，这一条保证按列检查与按清单检查等价。
  - 新增模型常量 `DECORATION_CHOICES` 与若干上限常量，验收未点名，供之后的服务层与约束共用。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 的相关条文与 promotions.json；人工核对迁移的检查约束文本与模型生成的文本一致，并由上述测试逐字比对。迁移在 MySQL 8 上的往返与 alembic check、全部后端测试由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。
