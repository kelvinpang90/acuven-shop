# SHOP-TASK-209 店铺标志图的数据库结构（装修设置单例行加五列，迁移 0025）

- [x] 依据：`docs/DESIGN.md` 1.20（提交 `a870f78`）「计价、优惠、积分与库存」第 15 条「记录」一段；`docs/HANDOFF.md` 0.84 记录的落实写法（标志五列照 `ProductImage` 的五列加 `logo_` 前缀，三个检查约束照同名约束换列名，单例行不另加文件名唯一约束，迁移 0025 照 0024）。设计闸门：DESIGN 1.20（提交 `a870f78`）第 15 条与 HANDOFF 0.84，店铺标志图的数据结构。只改结构：不写读写这些列的服务层与接口，不改前端、已有迁移与设计，不装依赖；图片处理与存储的参数化、服务层、接口与前端另由 SHOP-TASK-210 至 216 实现。
- [x] 模型（`app/models/store_design.py`）：`StoreDesignSetting` 在 `updated_at` 之后依次加五个可空列 `logo_file_name`（`String(32)`）、`logo_source_format`（`String(4)`）、`logo_width`、`logo_height`、`logo_byte_size`（整数）；检查约束（按命名约定为 `ck_store_design_settings_<名>`）：
  - `logo_all_or_none`：`(logo_file_name IS NULL AND logo_source_format IS NULL AND logo_width IS NULL AND logo_height IS NULL AND logo_byte_size IS NULL) OR (logo_file_name IS NOT NULL AND logo_source_format IS NOT NULL AND logo_width IS NOT NULL AND logo_height IS NOT NULL AND logo_byte_size IS NOT NULL)`
  - `logo_source_format_valid`：`logo_source_format IS NULL OR logo_source_format IN ('jpeg', 'png', 'webp')`
  - `logo_display_size_range`：`logo_width IS NULL OR (logo_width >= 1 AND logo_width <= 1600 AND logo_height >= 1 AND logo_height <= 1600 AND logo_byte_size >= 1)`

  文本与验收标准逐字相同。单例行只有一行，文件名不另加唯一约束；文件名的十六进制格式由之后的服务层保证，数据库不查。其余列与约束不变。模块说明里「存储首版不含标志图」改为引用 DESIGN 1.20 第 15 条与 HANDOFF 0.84，类的说明加一段标志五列的说明。
- [x] 迁移 `alembic/versions/20261105_0025_store_logo.py`（revision `0025`，down_revision `0024`），照 0024 的写法：不 import app、自带常量，约束名用 `op.f`；三个约束文本写成模块常量（`LOGO_ALL_OR_NONE_CONSTRAINT`、`LOGO_SOURCE_FORMAT_CONSTRAINT`、`LOGO_DISPLAY_SIZE_CONSTRAINT`，汇总为 `CHECK_CONSTRAINTS`；列名为 `LOGO_COLUMNS`），与模型逐字一致。只在 MySQL 上执行，不要求能在 SQLite 上执行；模块说明写明执行顺序的原因。
  - upgrade：加五个可空列（已有的那一行五列全为空，约束建立时即可通过）→ 建三个检查约束。
  - downgrade：先读出 `logo_file_name IS NOT NULL` 的行数，大于 0 即抛 `RuntimeError`（此时还没有改动；加删约束与列在 MySQL 上是 DDL，不随事务回滚；删掉文件名会让存储桶里的标志成为孤立对象、前台失去标志）→ 倒序删去三个检查约束（MySQL 8.0 不许删除被检查约束引用的列）→ 倒序删去五列。
- [x] 测试：
  - `tests/test_store_design_models.py` 只为加列而改：`test_columns_nullability_and_lengths` 列出新增的五列、可空列为 `accent` 与这五列，并核对 `logo_file_name` 长 32、`logo_source_format` 长 4，文档字符串里「设置表没有标志图的列」改为引用 DESIGN 1.20 第 15 条；`_setting_values` 的默认值加上五个 `logo_` 列为 `None`（检查约束按整行求值，缺列会让新约束的求值出错）；`test_migration_schema_matches_models` 只比较迁移 0016 建出的部分——从按模型建出的结构里去掉五列与三个新约束（列名与约束名在测试里逐个写出，并先断言它们确实在按模型建出的结构里）再比较。其余测试未改。
  - 新增 `tests/test_store_logo_schema.py`（SQLite 内存库按模型建表，打开外键检查，写法照 `tests/test_admin_catalog_schema.py`），每条写明守住的 DESIGN 1.20 第 15 条或 HANDOFF 0.84 的句子：五列全空与全有值（jpeg、png、webp）的设置行能写入并读回；只填部分列（只有文件名、各缺一列）被 `logo_all_or_none` 拒绝；格式 gif 被 `logo_source_format_valid` 拒绝（不用大小写变体：MySQL 默认排序规则比较时不分大小写）；宽或高为 0 或 1601、字节数为 0 被 `logo_display_size_range` 拒绝，宽高 1 与 1600、字节数 1 能写入并读回；经 importlib 载入迁移 0025，核对 revision 与 down_revision、三个约束常量与模型的同名约束逐字一致。
- 偏离与说明：
  - 单例槽全表唯一，一个库里只能有一行设置，所以边界值的对照分作 1×1600 与 1600×1 两个用例，各在新库里写入（0024 的测试是同一商品写两张图片）。
  - 迁移常量的核对比验收标准多两项：`CHECK_CONSTRAINTS` 的约束名与顺序逐个写出，`LOGO_COLUMNS` 与测试里五列的顺序相同。
  - 检查约束的反例每次只让目标约束不成立（例如缺 `logo_byte_size` 时 `logo_display_size_range` 的结果为 NULL，按 SQL 规则视为通过），断言报出的是目标约束的名称。
  - `tests/test_store_design_models.py` 的模块文档字符串与 `test_default_constants` 里引用 Kelvin 2026-10-06「首版不含标志图」的句子未改（验收标准只要求改 `test_columns_nullability_and_lengths` 的那一句；默认值里确实仍没有标志）。
- 验证到什么程度：上面列出的是本次新增或修改的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。pytest 用 SQLite，不执行迁移 0025，只载入它核对常量；迁移的 upgrade、downgrade 再 upgrade 的往返与 `alembic check` 由 CI 的 `backend` 在 MySQL 8 上做。`alembic check` 不比较检查约束，两边一致靠上面的常量核对测试与人工逐字对照。downgrade 遇到已有标志时抛错的路径没有自动测试覆盖（CI 往返时库里没有这样的行）。
