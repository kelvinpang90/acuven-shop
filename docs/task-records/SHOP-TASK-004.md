# SHOP-TASK-004 商品目录数据模型与迁移

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 Product / Variant 一行建立商品目录表：`app/db/base.py`、`app/models/catalog.py`、迁移 `alembic/versions/20260929_0002_catalog.py`（revision `0002`，down_revision `0001`），测试见 `tests/test_catalog_models.py`
- 做了什么：`app/db/base.py` 定义声明式基类 `Base`，其 MetaData 带命名约定（`pk_<表>`、`fk_<表>_<首列>_<被引用表>`、`uq_<表>_<全部列>`、`ck_<表>_<名>`、`ix_<表>_<全部列>`，生成的名字都在 MySQL 64 字符以内），以及每张表共用的 `mysql_engine=InnoDB`、`mysql_charset=utf8mb4`；`alembic/env.py` 的 `target_metadata` 改为 `Base.metadata`。七张表如下，金额与库存一律 `Integer`，不用浮点或 Decimal：
  - `categories`：slug（唯一 `uq_categories_slug`）、`name_en/zh/ms`（可空）、`is_active`。
  - `products`：`category_id` → `categories.id`（ON DELETE RESTRICT，分类下有商品不能删）、slug（唯一 `uq_products_slug`）、`name_en/zh/ms`、`description_en/zh/ms`（均可空）、`is_active`、`created_at`（应用写入不带时区的 UTC）。
  - `product_images`：`product_id` → `products.id`（CASCADE）、`storage_ref`、`sort_order`；`uq_product_images_product_id_sort_order`。不存图片内容。
  - `product_options`（规格名）：`product_id` → `products.id`（CASCADE）、`code`、`sort_order`、`name_en/zh/ms`；`uq_product_options_product_id_code`，以及复合外键目标 `uq_product_options_id_product_id`。
  - `product_option_values`（规格值）：`(option_id, product_id)` → `product_options(id, product_id)`（CASCADE）、`code`、`sort_order`、`name_en/zh/ms`；`uq_product_option_values_option_id_code`，以及复合外键目标 `uq_product_option_values_id_option_id_product_id`。
  - `product_variants`（SKU 规格）：`product_id` → `products.id`（CASCADE）、`sku`（全局唯一 `uq_product_variants_sku`）、`price_sen`、`daily_initial_stock`、`available_stock`、`is_active`；检查约束 `ck_product_variants_price_sen_non_negative`、`ck_product_variants_daily_initial_stock_non_negative`、`ck_product_variants_available_stock_non_negative`（均 `>= 0`）；复合外键目标 `uq_product_variants_id_product_id`。
  - `variant_option_values`（SKU 与规格值的关联）：主键 `(variant_id, option_value_id)`；`(variant_id, product_id)` → `product_variants(id, product_id)`（CASCADE）、`(option_value_id, option_id, product_id)` → `product_option_values(id, option_id, product_id)`（CASCADE），保证 SKU、规格名、规格值属于同一商品且规格值属于所填的规格名；`uq_variant_option_values_variant_id_option_id` 保证同一 SKU 在同一规格名下至多一个值。
  - 所有外键的删除行为都显式写出；七张表都显式 InnoDB、utf8mb4。迁移按同样的名字逐个写出约束，downgrade 按依赖倒序删除七张表。
- 偏离：无。未改设计。设计把「MYR 单价」与商品并列，验收标准把单价放在每个 SKU 规格上，按验收标准实现（同一商品内各 SKU 可不同价）。以下留给之后的任务：发布判断（缺少当前语言回退英文、再缺失不发布）由查询任务按三列文案判断，表里不另存；每个规格名都有值由后台维护任务校验；库存预留与每日重置、数据库会话、API 与种子数据都不在本任务。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并逐个核对迁移中的约束名、列类型、可空性、外键删除行为与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_catalog_models.py` 用 SQLite 内存库（连接时打开外键检查）覆盖：合法关联可写入且零库存可写入（作对照）、负单价与负库存被对应的检查约束拒绝、重复 SKU、重复商品 slug、重复分类 slug、同一 SKU 在同一规格名下两个值被唯一约束拒绝、SKU 关联其他商品的规格值及规格值与规格名不匹配被外键拒绝；这些测试与迁移在真 MySQL 上的升降级与 `alembic check` 只在 PR 的必需 CI 检查 backend 中执行，Worker 沙箱不跑。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.8「数据模型」Product / Variant 一行逐项一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/db/base.py 定义声明式基类，其 MetaData 带主键、外键、唯一、检查、索引的约束命名约定；app/models/catalog.py 定义商品目录模型，app/models/__init__.py 导出它们
      - 分类、商品、规格名、规格值各有英文、中文、马来文三列文案（可为空，是否发布由查询任务按「缺少当前语言回退英文，再缺失则不发布」判断）；商品另有三语描述；分类与商品有唯一 slug 与启用状态；商品有创建时间（UTC），供按最新排序
      - 商品图片只存存储引用与排列序号，同一商品内序号唯一，不存图片内容
      - 规格名与规格值属于商品、各有稳定代码与排列序号；每个 SKU 规格属于一个商品，有全局唯一的 SKU、MYR 单价（整数仙）、管理员设定的每日初始库存、当日可用库存与启用状态，并关联其规格值：用复合外键保证 SKU、规格名、规格值属于同一商品且规格值属于所关联的规格名，同一 SKU 在同一规格名下至多关联一个值（唯一约束）；每个规格名都有值由之后的后台维护任务校验
      - 单价、每日初始库存、当日可用库存都是整数，数据库层用显式命名的检查约束保证不小于零；不使用浮点或 Decimal 存金额；alembic check 不比较检查约束与表选项，PR 正文逐表列出迁移中的检查约束与 InnoDB、utf8mb4 选项，供审查核对与模型一致
      - 所有表显式指定 InnoDB 与 utf8mb4（三语文案含中文）；外键删除行为写明，不留默认
      - alembic/env.py 的 target_metadata 接上模型的 MetaData；新迁移文件 alembic/versions/20260929_0002_catalog.py，revision 为 0002、down_revision 为 0001，upgrade 建全部表，downgrade 删除全部表
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，且之后 alembic check 在它比较的范围内（表、列、类型、索引、唯一约束、外键）报告模型与迁移无差异，均由 PR 的必需 CI 检查 backend 执行
      - tests/test_catalog_models.py 用 SQLite 内存库按模型建表，验证负单价、负库存被检查约束拒绝，重复 SKU、重复 slug、同一 SKU 在同一规格名下关联两个值被唯一约束拒绝，SKU 关联其他商品的规格值、规格值与所关联的规格名不匹配被外键拒绝（SQLite 须开启外键检查）；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-004 记录段（列出表与关键约束、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加 API、会话或种子数据
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.8，提交 2b68ad0），不合并、不直推
```
