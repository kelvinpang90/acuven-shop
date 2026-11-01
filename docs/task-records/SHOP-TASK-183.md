# SHOP-TASK-183 后台商品的读取与新建规则（含代码生成）

- [x] 依据：`docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 11 条（后台维护商品）；`docs/HANDOFF.md` 0.77 的落实写法。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 11 条与 HANDOFF 0.77，商品与价格。版本号列已由 SHOP-TASK-177 加好；本任务不改模型与迁移、`app/services/catalog.py`、`app/services/admin_codes.py`、现有测试、前端与设计。已有行的保存由 SHOP-TASK-184、保存时的新增由 185、接口由 186 接上，页面 A04 另行登记。
- [x] 请求结构（新增 `app/services/admin_products.py`，五个冻结的 dataclass，新建与保存共用，184 至 186 照用）：
  - `ProductInput(category_id, name_en, name_zh, name_ms, description_en, description_zh, description_ms, is_active, max_per_order, options, variants)`
  - `OptionInput(id, name_en, name_zh, name_ms, values)`：`id` 为 None 表示新建。
  - `ValueInput(id, temp_id, name_en, name_zh, name_ms)`：已有的只带 `id`，新增的只带 `temp_id`。
  - `VariantInput(id, temp_id, option_values, price_sen, daily_initial_stock, is_active)`：已有的只带 `id` 且 `option_values` 为 None；新增的只带 `temp_id`，`option_values` 按规格名顺序列出 `ValueRef`（没有规格名时为空列表）。
  - `ValueRef(id, temp_id)`：两者恰有一个。
  - 列表字段为 list 或 tuple；字段都没有默认值。
- [x] 第一步（共用检查）：`check_product_request(product: ProductInput, *, creating: bool) -> CheckedProduct`，不碰数据库，不通过抛 `ProductInvalid`。返回去掉首尾空白、空串为 None、列表转为 tuple 的 `CheckedProduct` / `CheckedOption` / `CheckedValue` / `CheckedVariant`。检查文案为 str 与长度（名称 200、描述 5,000、规格名与规格值的名称 100）；分类 ID（1 到 2³¹−1）、价格（0–50,000,000）、每日初始库存（0–9,999）、限购（1–1000）为整数（布尔值不算）；上架与启用状态为 bool；0–3 个规格名、每个 1–20 个值、1–50 个 SKU、没有规格名时恰一个 SKU；行 ID 为整数（或 None）且同一类不重复；`temp_id` 为 1–32 个 ASCII 字母、数字、连字符或下划线，规格值之间、SKU 之间各自不重复；每行 `id` 与 `temp_id` 恰有一个；已有 SKU 不带组合；新增 SKU 在每个规格名下恰引用一个请求里该规格名下列出的值（行 ID 引用已有的、`temp_id` 引用新增的），新增 SKU 之间组合不重复；规格名之间、同一规格名下规格值之间英文名称（去掉首尾空白并转小写，为空不比）不重复；新增的规格名（`id` 为 None）与新增的规格值英文名称必填；`creating=True` 时商品英文名称必填、任何行 ID（含 `ValueRef.id`）都不允许；上架时英文名称与英文描述、全部规格名与规格值的英文名称齐全、至少一个启用的 SKU。
- [x] 读取：
  - `load_admin_products(db) -> list[AdminProductListItem]`：全部商品（含未上架的），按 `created_at` 从新到旧、同时间按 id 从大到小，不分页；每项 `id`、`slug`、`name_en/zh/ms`、`is_active`、`category_id`、`category_name_en/zh/ms`。
  - `load_admin_product(db, product_id) -> AdminProduct | None`：`id`、`slug`、`version`、`category_id`、三语名称与描述、`is_active`、`max_per_order`、`options`（`AdminProductOption`：`id`、`code`、三语名称、`values`，按 sort_order 再按 id）、`variants`（`AdminProductVariant`：`id`、`sku`、`price_sen`、`daily_initial_stock`、`available_stock`、`is_active`、`option_value_ids`，按 id，规格值 ID 按规格名顺序）、`images`（`AdminProductImage`：`id`、`storage_ref`，按 sort_order）。
  - 两者只查列值、只发 SELECT；文案为 NULL 或空串都返回 None。
- [x] 代码生成（保存任务复用，各自查库后在应用层比较）：
  - `generate_slug(db, name_en) -> str`：`unique_code(code_base(英文名称, "product"), 100, 全站已有 slug)`。
  - `generate_option_codes(db, names_en, taken) -> list[str]`：按顺序；基本部分先沿用全站英文名称相同的规格名中 id 最小者的代码（取回全部规格名后比对），没有才 `code_base(英文名称, "option")`；再 `unique_code(…, 50, taken 加上先生成的)`。
  - `generate_value_codes(db, option_code, names_en, taken) -> list[str]`：同上，沿用范围为全站代码等于 `option_code` 的规格名之下的规格值（取回后照字面再比一次代码），fallback 为 `value`。
  - `generate_skus(db, slug, combinations) -> list[str]`：slug 按规格名顺序以连字符接各规格值代码，没有规格名时接 `-std`；`unique_code(…, 64, 全站已有 SKU 加上先生成的)`。
  - 已有代码按小写放进 `taken`（同 SHOP-TASK-180）。
- [x] 新建：`create_product(db, admin_account_id, product, now) -> AdminProduct`。第一步通过后回滚此前的事务；新事务里先以 `check_category_for_product(db, category_id)`（`with_for_update(read=True)`）读所选分类，不存在、停用或缺英文名称（与前台发布规则同口径，只去空格）即 `ProductInvalid`；再按请求顺序生成全部代码，插入商品（版本号 1、`created_at` 为 `now`）、规格名与规格值（sort_order 各从 0 起）、SKU（`available_stock` 等于每日初始库存）与 SKU 的规格值关联；flush 撞上唯一约束即 `ProductChanged`；之后按商品、规格名、规格值、SKU 的顺序写审计，读出 `load_admin_product` 的结果后提交。任何异常都先回滚再抛出。
- [x] 审计（经 `record_audit`，旧值为空）：`admin_product_created`（`product`，新值为 slug）、`admin_product_option_created`（`product_option`）、`admin_product_option_value_created`（`product_option_value`）、`admin_product_variant_created`（`product_variant`），新值为代码，对象 ID 为各自的行 ID。
- [x] 异常：`ProductInvalid`（取值不合格）与 `ProductChanged`（商品已被改动）只带固定的英文说明，不回显提交的值。
- [x] 测试：新增 `tests/test_admin_products.py`（SQLite 内存库按模型建表，示例商品照迁移 0003 的写法造，只经 `create_product` 与两个 load 函数调用，每条测试的文档字符串写明它守住的第 11 条原句）。覆盖列表排序与未上架商品、同时间按 id 从大到小；单件读取的全部字段、规格名与规格值按 sort_order（同序按 id）、SKU 的规格值按规格名顺序、图片按 sort_order；不存在为 None；读取只发 SELECT；第一步各类不合格（各上限外一端、布尔值与浮点数当整数、组合重复、英文名称大小写与空白不同也算重复、缺英文名称、上架条件、没有规格名却有两个 SKU、temp_id 格式、重复与引用无效、新建请求带行 ID）都不发 SQL、不写库；上限与下限两端可以新建；分类停用、缺英文名称或不存在被拒；Colour 沿用 color、1 m 沿用 1m、大小写不同也沿用、别的规格名下不沿用；同一商品先 Color 后 Colour 得 color 与 color-2；同一规格名下基本写法相同的值去重；没有规格名的 SKU 为 slug-std；slug 与 SKU 与示例数据相同时加 -2，同一次保存里截短后相同的 SKU 加 -2；撞上唯一约束为 `ProductChanged`；审计被拒时整次回滚；新 SKU 的当日可用库存等于每日初始库存；审计条数与写法；新建的已上架商品出现在前台商品列表、未上架的不出现；回滚后第一条语句是以共享锁读分类（按 MySQL 方言编译检查），之后不再加锁。
- 偏离与取舍：
  - 第一步写成一个函数、以 `creating` 区分新建与保存，并已按「已有行带 id、新增行带 temp_id」的一般形状检查（`ValueRef` 以行 ID 引用请求里列出的已有规格值、同一类行 ID 不重复等）；新建请求只是其中不带任何行 ID 的情形。184、185 若要另加保存专用的检查（如规格名都带 id、version 为整数），在调用处加，不改它对新建请求的判定。
  - 分类 ID 除了是整数，还须在 1 到 2³¹−1 之间，否则即 `ProductInvalid`（与「分类不存在」同一回答）：超出 INT 范围的值在 SQLite 驱动上会在比较之前就报溢出。行 ID 只查类型，不查范围，留给保存的第二步判为「商品已被改动」。
  - 新建时商品英文名称必填写在第一步里，不论是否上架（第 11 条「新建商品与新增规格值时英文名称必填」）；新增的规格名只在新建时出现，所以「id 为 None 的规格名英文名称必填」等同于新建时必填。
  - 规格值代码的沿用以去重之后的规格名代码为准：同一商品先 Color 后 Colour 时，后者为 `color-2`，它下面的值在全站 `color-2` 规格名之下找可沿用的代码。
  - 分类「有英文名称」按前台发布规则的 `TRIM` 口径判断（只去空格），与 `app/services/catalog.py` 的 `published()` 一致，不会存下「上架但前台看不见」的商品。
  - 审计在全部行插入之后一并写，顺序为商品、全部规格名、全部规格值（按规格名再按值的顺序）、全部 SKU。
  - 返回值在提交之前、于同一事务里读出（与 `create_category` 一致）。
- 验证范围：上面列出的是本次新增的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。SQLite 不复现 MySQL 的共享锁，所以与分类保存同时进行时的等待行为不在这里覆盖，只检查锁定查询的写法与它在事务里的位置。

## 登记时的验收标准（原文）

2026-11-01 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 请求结构（写在 app/services/admin_products.py，新建与保存共用，SHOP-TASK-184 至 186 照用、不改）——ProductInput(category_id, name_en, name_zh, name_ms, description_en, description_zh, description_ms, is_active, max_per_order, options, variants)；OptionInput(id, name_en, name_zh, name_ms, values)，id 为 None 表示新建（只在新建商品时出现）；ValueInput(id, temp_id, name_en, name_zh, name_ms)，已有的值只带 id、新增的值只带 temp_id；VariantInput(id, temp_id, option_values, price_sen, daily_initial_stock, is_active)，已有的 SKU 只带 id 且 option_values 为 None，新增的 SKU 只带 temp_id 且 option_values 按规格名顺序列出 ValueRef；ValueRef(id, temp_id) 两者恰有一个，以行 ID 引用已有规格值、以 temp_id 引用同一请求里新增的规格值；temp_id 为 1–32 个 ASCII 字母、数字、连字符或下划线，规格值之间、SKU 之间各自不重复；五个类型都是不可变的数据类，列表字段为 list 或 tuple
      - 读取——load_admin_products(db) 返回全部商品（含未上架的），按 created_at 从新到旧、同时间按 id 从大到小，不分页，每项为 id、slug、三语名称、is_active、category_id 与所属分类的三语名称；load_admin_product(db, product_id) 不存在时返回 None，否则返回商品的 id、slug、version、category_id、三语名称与描述、is_active、max_per_order，规格名按 sort_order（同序按 id）列出 id、code、三语名称与同样排序的规格值（id、code、三语名称），SKU 按 id 列出 id、sku、price_sen、daily_initial_stock、available_stock、is_active 与按规格名顺序排列的规格值 ID，图片按 sort_order 列出 id 与 storage_ref；两者只发 SELECT，不写库；之后的任务不改这两个函数的签名与返回结构
      - 第一步（不碰数据库，新建与保存共用）——名称每种语言去掉首尾空白后 0–200 个字符、描述 0–5,000 个字符、规格名与规格值的名称 0–100 个字符，空串存为 None；分类 ID、价格、库存与限购为整数（布尔值不算）——价格 0 到 50,000,000 仙、每日初始库存 0 到 9,999、每单限购 1 到 1000；结构为 0–3 个规格名、每个规格名 1–20 个值、1–50 个 SKU，每个 SKU 在每个规格名下恰有一个值，没有规格名时恰有一个 SKU，新增 SKU 之间组合不重复；同一商品里规格名的英文名称、同一规格名下规格值的英文名称去掉首尾空白并转小写后不重复（为空的不参与比较）；temp_id 的格式、唯一与引用有效；新建商品时商品、每个规格名与每个规格值的英文名称必填，且请求里不得有任何 id；保存为上架时请求里看得到的上架条件须满足（英文名称与英文描述、全部规格名与规格值的英文名称齐全、至少一个启用的 SKU）；任一不通过抛 ProductInvalid（只带英文说明、不回显提交的值）、不发任何 SQL
      - 新建——create_product(db, admin_account_id, product, now)（now 为不带时区的 UTC 时间）；第一步通过后先回滚此前的事务，在新事务里以共享锁读所选分类（SELECT … FOR SHARE，即 with_for_update(read=True)），分类须存在、启用且有英文名称，否则回滚、抛 ProductInvalid；随后插入商品（version 1、created_at 取 now）、规格名、规格值与 SKU（新 SKU 的 available_stock 等于其每日初始库存），规格名、规格值各按请求顺序从 0 起给 sort_order
      - 代码——商品 slug 为 unique_code(code_base(英文名称, product), 100, 全站已有 slug)；规格名代码的基本部分先沿用全站英文名称相同（去掉首尾空白并转小写后在应用层比较，取回行后比对，不依赖数据库排序规则）的规格名中 id 最小者的代码，没有才取 code_base(英文名称, option)，再经 unique_code(基本部分, 50, 该商品已有与已生成的规格名代码)；规格值代码的基本部分先沿用全站同一规格名代码之下英文名称相同的规格值中 id 最小者的代码，没有才取 code_base(英文名称, value)，再经 unique_code(基本部分, 50, 该规格名下已有与已生成的规格值代码)；SKU 代码为 slug 依规格名的顺序以连字符接上各规格值的代码，没有规格名时为 slug 接 -std，再经 unique_code(…, 64, 全站已有 SKU 与同一次保存里先生成的)；按请求顺序生成；flush 时撞上唯一约束即回滚、抛 ProductChanged；代码生成写成保存任务能复用的函数
      - 审计——新建商品写一条 admin_product_created（对象类别 product、新值为 slug），随它新建的每个规格名、规格值与 SKU 各写一条（admin_product_option_created、admin_product_option_value_created、admin_product_variant_created，对象类别 product_option、product_option_value、product_variant，新值为代码），对象 ID 为各自的行 ID，旧值为空；之后一并提交，返回 load_admin_product 的结果；写入途中出现任何异常都先回滚再抛出
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_admin_products.py（SQLite 内存库按模型建表，数据照迁移 0003（alembic/versions/20260930_0003_demo_catalog.py）的示例商品写法造），只经 create_product 与两个 load 函数调用，不直接测共用检查的内部写法，每条测试的文档字符串写明它守住的 DESIGN 1.18 第 11 条原句——列表排序与含未上架的商品、单件读取的各项与排序、不存在为 None、读取只发 SELECT；第一步每类不合格（各上限两端、布尔值当整数、组合重复、英文名称大小写不同也算重复、缺英文名称、上架条件、没有规格名却有两个 SKU、temp_id 重复或引用无效、新建请求带 id）都不发 SQL；分类停用或缺英文名称被拒；Colour 沿用 color、1 m 沿用 1m、同一商品先 Color 后 Colour 得 color 与 color-2、没有规格名的 SKU 为 slug-std、slug 与 SKU 去重加后缀；新 SKU 的当日可用库存等于每日初始库存；审计条数与写法；新建的已上架商品出现在 app/services/catalog.py 的前台商品列表，未上架的不出现
      - docs/task-records/SHOP-TASK-183.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明函数签名与偏离取舍）；改动只落在 allowed_change_paths，不改模型与迁移、app/services/catalog.py、app/services/admin_codes.py、现有测试、前端、.platform/、CI 与部署配置，不装依赖，不改设计
```
