# SHOP-TASK-044 店铺装修设置的数据模型与默认值（不含标志图）

- [x] 按 `docs/REQUIREMENTS.md`「店铺装修」、`docs/UX.md` 0.8 的 A08 与 P01 以及 `docs/HANDOFF.md` 0.34 记录的 Kelvin 2026-10-06 决定（存储首版不含标志图），建立店铺装修设置的三张表与迁移并写入默认值：`app/models/store_design.py`（`app/models/__init__.py` 导出 `StoreDesignSetting`、`StoreHomeBlock`、`StoreFeaturedProduct`）、迁移 `alembic/versions/20261006_0016_store_design.py`（revision `0016`，down_revision `0015`）；测试见 `tests/test_store_design_models.py`。主题与主色取值以 `docs/design/tokens/themes.json` 为准。未做读取、接口或页面，未改 `app/main.py`、其他模型、已有迁移、前端、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：不适用（店铺装修只改变前台外观，不涉及金额计算与个人资料，见 `docs/HANDOFF.md` 2026-09-30 关于 REQUIREMENTS 1.9 的记录）。拆分：数据层，读取与公开接口由 SHOP-TASK-045 交付。
- 三张表（沿用 `app/db/base.py` 的基类、命名约定与 InnoDB、utf8mb4；时间是不带时区的 UTC；除主色外全部非空）：
  - `store_design_settings`：`id`、`singleton_slot`（非空整数，全表唯一 `uq_store_design_settings_singleton_slot`）、`theme` String(20)、`accent` String(20) 可空（空即该主题的默认主色，即 themes.json 该主题 accentOptions 的第一项）、`updated_at`。全表只有一行。
  - `store_home_blocks`：`id`、`block` String(20)（全表唯一 `uq_store_home_blocks_block`）、`position`（全表唯一 `uq_store_home_blocks_position`）、`is_visible`。四行齐全由迁移初始化、由之后的写入在同一事务里保持（模型 docstring 写明），库里不加行数约束。
  - `store_featured_products`：`id`、`position`（全表唯一 `uq_store_featured_products_position`）、`product_id` → `products.id`（`fk_store_featured_products_product_id_products`，ON DELETE RESTRICT；唯一约束 `uq_store_featured_products_product_id` 同时充当外键索引）。位置 1 到 4 且唯一，故至多四行。
- 检查约束（只用比较、LENGTH、IN 与 IS NULL，SQLite 与 MySQL 都能执行）：
  - `ck_store_design_settings_singleton_slot_one`：`singleton_slot = 1`
  - `ck_store_design_settings_theme_known`：`theme IN ('pandan', 'pasar', 'receipt', 'kopitiam', 'batik', 'malam', 'gula', 'galeri', 'songket', 'litar')`
  - `ck_store_design_settings_accent_length`：`accent IS NULL OR (LENGTH(accent) >= 1 AND LENGTH(accent) <= 20)`
  - `ck_store_home_blocks_block_known`：`block IN ('hero', 'how', 'categories', 'featured')`
  - `ck_store_home_blocks_position_range`：`position >= 1 AND position <= 4`
  - `ck_store_featured_products_position_range`：`position >= 1 AND position <= 4`
- 常量（`app/models/store_design.py`）：`THEME_ACCENTS`（10 款主题按 themes.json 顺序，各自可选主色 id 按 accentOptions 顺序）、`THEMES`、`HOME_BLOCKS`、`FEATURED_MAX = 4`，默认值 `DEFAULT_THEME = "pandan"`、`DEFAULT_ACCENT = None`、`DEFAULT_HOME_BLOCKS`（hero、how、categories、featured 依次位置 1 到 4，全部显示）。
- 迁移 0016：upgrade 建三张表，写入设置一行（槽 1、主题 pandan、主色为空、更新时间为执行迁移时的 UTC）与四个区块（按上面的顺序全部显示），不写精选商品；downgrade 按依赖倒序删三张表。约束名按命名约定逐个写出。`alembic check` 不比较检查约束与表选项，模型与迁移两边已人工核对一致，测试另在 SQLite 上比较两边建出的表。
- 偏离与取舍，请审阅（未改需求与设计，未发现须停下的需求或设计问题）：
  - 迁移 0016 从 `app.models.store_design` 导入主题 id、区块、长度上限、单例槽与默认值常量，与模型、之后的读取与写入共用同一份（验收要求「供迁移、之后的读取与写入共用」），不同于已有迁移不 import app 的做法（`alembic/env.py` 本就导入 `app.models`）。代价：以后改这些常量会连带改变 0016 重放时建出的约束与写入的默认值，因此改常量时须另写新迁移改库里的约束与数据。
  - 主色是否属于所选主题、精选时商品须已上架由之后的写入方校验；之后下架的精选商品仍留在表里，前台按目录可见判定不显示（UX A08、P01，Kelvin 2026-10-06「复用目录的 published() 判定」由 SHOP-TASK-045 实现）。
  - 区块与精选的位置全表唯一，逐行就地交换位置会在中途撞上唯一约束；写入方须在同一事务里另行处理（如先删后写），已写在模型 docstring 里。
  - 被精选的商品不能物理删除（RESTRICT）；现有代码只有示例目录迁移 0003 的 downgrade 删商品，按迁移顺序那时 0016 已删表。
- 留给之后的任务：标志图的上传、移除与存储（随图片的格式、大小与存放规则与商品图一起做，Kelvin 2026-10-06 决定）；设置的读取与公开接口（SHOP-TASK-045）；后台修改（含审计记录）与 A08 页面；前台按设置显示主题、主色、区块与精选商品。
- 验证到什么程度：人工逐条对照验收标准自查，人工核对模型与迁移的列、约束名、约束表达式一致。`tests/test_store_design_models.py` 用 SQLite 内存库（每个连接打开外键检查并断言已打开）按模型建表，每条测试（参数化的是每个用例）写明它守住的需求原句或 Kelvin 2026-10-06 的决定，覆盖：合法的设置（含换主题与主色）、调过顺序且有隐藏的四个区块、满 4 件的精选商品写得进去且求值辅助函数不报任何约束不成立；只有主色可空、设置表没有标志图的列；第二行设置（槽 2 被检查约束拒绝，槽 1 被唯一约束拒绝）、未知主题与大小写不同的主题、主色为空串与长 21、未知区块、区块位置 0 与 5、精选位置 0 与 5 被对应检查约束拒绝；重复区块、重复区块位置、重复精选商品、重复精选位置被唯一约束拒绝；精选指向不存在的商品与删除被精选引用的商品被外键拒绝；常量表的主题与主色（含顺序）与 themes.json 一致、默认主题与 themes.json 的 `default` 相同；默认值常量；在空 SQLite 连接上执行迁移 0016 的 upgrade 后查回的设置与区块等于默认值常量、没有精选商品，迁移用的主题 id、区块与默认值就是模型常量本身（同一对象）；迁移建出的三张表与按模型建出的逐列、逐个检查约束、唯一约束与外键相同，downgrade 后三张表都不在。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；迁移的 upgrade head、downgrade base、再 upgrade head 与 `alembic check` 也由该检查在真 MySQL 上执行。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.8 的 A08 与 P01 以及 docs/HANDOFF.md 0.34 记录的 Kelvin 2026-10-06 决定一致；主题与主色的取值以 docs/design/tokens/themes.json 为准（10 款主题的 id 与各自 accentOptions 的 id）；发现需求或设计本身有问题时停下并在 PR 里说明，不自行改
      - app/models/store_design.py 定义三张表，沿用 app/db/base.py 的基类、约束命名约定与表选项，时间一律不带时区的 UTC，外键一律 RESTRICT，app/models/__init__.py 导出它们；检查约束只用比较、LENGTH、IN 与 IS NULL，SQLite 与 MySQL 都能执行
      - 装修设置表只有一行（单例槽，非空整数，检查约束只允许 1，全表唯一），列为主题（检查约束只允许 10 款主题的 id）、主色（可空，空即该主题的默认主色；是否属于该主题的可选主色由写入方校验，库里只检查长度 1 到 20）与更新时间；首页区块表列为区块（只允许 hero、how、categories、featured，全表唯一）、位置（1 到 4，全表唯一）与是否显示，四行齐全由迁移初始化、由之后的写入在同一事务里保持（模型 docstring 写明），库里不另加行数约束；精选商品表最多四行，列为位置（1 到 4，全表唯一）与商品（外键指向商品表，全表唯一）
      - 新迁移文件 alembic/versions/20261006_0016_store_design.py，revision 为 0016、down_revision 为 0015；upgrade 建三张表并写入默认值（主题 pandan、主色为空，四个区块按 hero、how、categories、featured 的顺序全部显示，没有精选商品），downgrade 删表；约束名按命名约定逐个写出；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行；PR 正文列出检查约束
      - app/models/store_design.py 同时定义主题与主色的常量表（每款主题的可选主色 id）与默认值常量，供迁移、之后的读取与写入共用；常量表与 docs/design/tokens/themes.json 一致由测试守住
      - tests/test_store_design_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入合法的设置、四个区块与精选商品作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（第二行设置、未知主题、主色为空串或过长、未知区块、重复区块或位置、位置越界、重复的精选商品或位置、精选指向不存在的商品、删除被精选引用的商品被拒）；常量表的主题与主色和 themes.json 一致；默认值常量与迁移写入的相同；每条测试写明它守住的是需求里哪一句或 Kelvin 的哪一项决定；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；docs/TODO.md 新增 SHOP-TASK-044 记录段（三张表与约束、默认值、偏离、验证到什么程度，并写明标志图、读取与接口、后台修改与前台显示留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不改已有迁移；不加接口、不改 app/main.py、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```
