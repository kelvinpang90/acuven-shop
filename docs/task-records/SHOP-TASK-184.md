# SHOP-TASK-184 已有商品的保存规则（三步核对、改已有行、版本与审计）

- [x] 依据：`docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 11 条（后台维护商品：范围、核对的三步、有改动才加版本、审计、生效）；`docs/HANDOFF.md` 0.77 的落实写法（加锁顺序、审计操作名、字段名照模型列名）。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 11 条与 HANDOFF 0.77，商品与价格。本任务只处理已有行的修改，保存时新增规格值与 SKU 由 SHOP-TASK-185 接上，接口由 186 接上。不改模型与迁移、`app/services/catalog.py`、`app/services/checkout.py`、`app/services/admin_codes.py`、现有测试（含 `tests/test_admin_products.py`）、前端与设计。
- [x] 入口（`app/services/admin_products.py`）：`save_product(db, admin_account_id, product_id, version, product, now) -> AdminProduct`，`product` 为 SHOP-TASK-183 的 `ProductInput`，返回 `load_admin_product` 的结果。183 的请求结构、`create_product`、两个 load 函数与 `check_product_request` 的签名、返回结构与对新建请求的判定都未改动（共用检查本来就按「已有行带 id、新增行带 temp_id」的一般形状写，保存请求不需要扩展它）。
- [x] 第一步（`_check_save_request`，不发任何 SQL，不通过抛 `ProductInvalid`）：`check_product_request(product, creating=False)`；另查 `version` 为整数（布尔值不算）、规格名都带 `id`；本任务里请求带任何新增的规格值或 SKU（带 `temp_id` 的项）即拒绝，由 185 放开。同一类行 ID 不重复由共用检查完成。
- [x] 第二步（`_lock_and_match`）：先回滚此前的事务；新事务里以 `lock_product_statement(product_id)`（`SELECT … FOR UPDATE`）锁商品行，再以 `lock_variants_statement(product_id)`（按 id 从小到大，`FOR UPDATE`）锁该商品的 SKU 行，两者都带 `populate_existing`。商品不存在、`version` 与当前不同、请求里的规格名、规格值与 SKU 的行 ID 不恰好是该商品现有的全部行、规格值挂在别的规格名下，任一即 `ProductChanged`（经外层回滚）。
- [x] 第三步（`_check_category`，同一事务）：分类与当前不同时以 `check_category_for_product`（`with_for_update(read=True)`）读新分类，须存在、启用且有英文名称；分类不变而保存为上架时以同一函数读所属分类；分类不变且不上架时不读（停用或缺英文名称的当前分类可以保留）。不通过即 `ProductInvalid`。
- [x] 写入：按商品（`PRODUCT_FIELDS`：category_id、is_active、max_per_order、name_en/zh/ms、description_en/zh/ms）、规格名（name_en/zh/ms）、规格值（同）、SKU（`VARIANT_FIELDS`：price_sen、daily_initial_stock、is_active）逐项与当前值比较，有改动的写新值。有任何改动时商品版本号加一（一次保存只加一），没有改动时回滚、不加版本、不写审计，返回当前的商品。改每日初始库存不动 `available_stock`；slug、规格名与规格值代码、SKU 代码不变。写入途中出现任何异常都先回滚再抛出。
- [x] 审计（经 `record_audit`）：每改一项一条，操作名 `admin_product_changed`、`admin_product_option_changed`、`admin_product_option_value_changed`、`admin_product_variant_changed`，对象类别 `product`、`product_option`、`product_option_value`、`product_variant`，对象 ID 为各自的行 ID；旧值与新值写成「字段 值」：上架与启用状态写 `true` / `false`，整数照写（含 0），文案取前 100 个字符，为空时只写字段名；SKU 的两侧前面加 SKU 代码（如 `crew-neck-tee-black-m price_sen 2500`）。
- [x] 测试：新增 `tests/test_admin_product_save.py`（写法照 `tests/test_admin_products.py`：SQLite 内存库按模型建表，分类直接写入，商品只经 `create_product` 造；每条测试的文档字符串写明它守住的第 11 条原句）。覆盖改名不改 slug 与代码；改价格后 `quote_cart` 取新价；版本号不符（较新、较旧、两次保存带同一旧版本号）、漏列或多列 SKU、规格值与规格名的行 ID、规格值挂错规格名、列出别的商品的行、商品不存在与超出 INT 范围的商品 ID 都是 `ProductChanged` 且不写库；改到停用、缺英文名称或不存在的分类被拒（上架与否都一样），改到可用的分类可以保存；当前分类停用或缺英文名称时保存为上架被拒、下架保存可以保留它；原样保存与只差首尾空白时不加版本不写审计，有改动加一；审计的字段、顺序与写法；改每日初始库存不改当日可用库存；下架后前台商品列表不再出现、计价把它的行标为不可购买，停用 SKU 后计价把该行标为不可购买；审计出错时整次回滚；第一步各类不合格不发 SQL；两条锁定查询按 MySQL 方言编译带 `FOR UPDATE`（SKU 按 id 排序）并带 `populate_existing`，保存时回滚后依次锁商品行、SKU 行，改分类或保存为上架时再以共享锁（`SHARE`）读分类，分类不变且下架时不读。请求带新增项时的拒绝不断言（185 会改掉它）。
- 偏离与取舍：
  - 规格名与规格值的行不加锁，只以 `populate_existing` 读出：它们只经后台保存改动，而保存先锁了商品行，两次保存在商品行上排队；加锁顺序仍是 HANDOFF 0.77 的商品行、SKU 行、分类行的共享锁。
  - 版本号在锁完 SKU 行之后才比较（商品不存在时在锁 SKU 之前即回答），这样每次保存的加锁顺序都一样，不因核对结果而变。
  - `product_id` 不是 1 到 2³¹−1 的整数时按「商品不存在」回答 `ProductChanged`，不拿去查库（与 183 拦下超出 INT 范围的分类 ID 同一理由：SQLite 驱动会在比较之前报溢出）。`product_id` 不是请求本身，所以不归第一步。
  - 行 ID 的核对在应用层做：请求里规格名 ID 的集合、「规格值 ID → 所在规格名 ID」的映射、SKU ID 的集合，分别与该商品现有的相等；第一步已查过同一类行 ID 不重复，相等即恰好是现有的全部行，「规格值挂在别的规格名下」也包含在映射的比较里。
  - 英文名称不重复与上架条件中看得到文案与 SKU 启用状态的部分都在第一步按请求判断：保存请求须列出现有的全部行，请求即该商品保存后的全部内容，不需要再与库里的行比较。
  - 分类改了且保存为上架时只读一次新分类（新分类即保存后的所属分类）。
  - 比较时库里的空串与 NULL 都按空，所以原样保存（读取把空串返回为 None）或只差首尾空白都不算改动。
  - 审计的顺序：商品各项按上面的字段顺序，之后规格名（按 sort_order 再按 id）、规格值（按所属规格名的顺序再按自己的 sort_order 与 id）、SKU（按 id），同一行内按字段顺序。
  - 没有改动时返回值在回滚之前、于同一事务里读出（与 `save_category` 一致）。
- 验证范围：上面列出的是本次新增的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。SQLite 不复现 MySQL 的行锁与共享锁，所以与下单扣减、每日重置、分类保存同时进行时的等待行为不在这里覆盖，只检查锁定查询的写法与它们在事务里的顺序。

## 登记时的验收标准（原文）

2026-11-01 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 入口——save_product(db, admin_account_id, product_id, version, product, now)，product 为 SHOP-TASK-183 的 ProductInput；不改 183 的请求结构、create_product 与两个 load 函数的签名与返回结构；共用检查可以扩展以处理保存请求（已有 SKU 的 option_values 为 None、ValueRef 以行 ID 引用已有规格值、按请求里全部项计算上限与查重），但不改它对新建请求的判定，tests/test_admin_products.py 保持通过、不改。本任务里请求带任何新增的规格值或 SKU（带 temp_id 的项）即抛 ProductInvalid，由 SHOP-TASK-185 放开；本任务的测试不断言这一拒绝（185 会改掉它）
      - 第一步——调用共用检查，另查 version 为整数、规格名都带 id、同一类行 ID 不重复；不通过抛 ProductInvalid、不发任何 SQL（只看请求就能判断的归第一步，请求列出的行 ID 与库里不符归第二步）
      - 第二步——先回滚此前的事务，在新事务里依次以 SELECT … FOR UPDATE 锁商品行、再按 id 从小到大锁该商品的 SKU 行（锁定查询以函数给出并带 populate_existing）；商品不存在、version 与当前不同、请求里的规格名、规格值与 SKU 的行 ID 不恰好是该商品现有的全部行、规格值挂在别的规格名下，任一即回滚、抛 ProductChanged
      - 第三步（同一事务）——分类与当前不同时，以共享锁（with_for_update(read=True)）读新分类，须存在、启用且有英文名称；保存为上架时以共享锁读所属分类，须启用且有英文名称（分类不变且不上架时不检查，停用的当前分类可以保留）；不通过即回滚、抛 ProductInvalid
      - 写入与审计——逐项与当前值比较，有改动的写新值；有任何改动时商品 version 加一，没有改动时回滚、不加版本、不写审计；每改一项写一条审计（对象为商品、规格名、规格值或 SKU，以行 ID 记录），旧值与新值写成「字段 值」——字段为 category_id、is_active、max_per_order、name_en、name_zh、name_ms、description_en、description_zh、description_ms、price_sen、daily_initial_stock（上架与启用状态写 true 或 false，文案取前 100 个字符，为空时只写字段名），SKU 的两侧都在前面加 SKU 代码（如 crew-neck-tee-black-m price_sen 2500）；改每日初始库存不改 available_stock；slug 与各代码不变；返回 load_admin_product 的结果；写入途中出现任何异常都先回滚再抛出
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_admin_product_save.py（写法照 tests/test_admin_products.py，经 create_product 造数据），每条测试的文档字符串写明它守住的 DESIGN 1.18 第 11 条原句——改名不改 slug 与代码；改价格只影响之后的计价（app/services/checkout.py 的 quote_cart 取新价）；version 不符、漏列或多列行 ID、规格值挂错规格名、商品不存在都是 ProductChanged 且不写库；改到停用或缺英文名称的分类被拒、分类不变时停用的当前分类可以保留但保存为上架被拒；没有改动不加版本不写审计、有改动加一；审计的字段与写法；改每日初始库存不改当日可用库存；下架后前台不再出现、停用 SKU 后计价把该行标为不可购买；锁定查询按 MySQL 方言编译带 FOR UPDATE、分类读取带 FOR SHARE（或 LOCK IN SHARE MODE）
      - docs/task-records/SHOP-TASK-184.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明偏离取舍）
      - 改动只落在 allowed_change_paths；不改模型与迁移、app/services/catalog.py、app/services/checkout.py、app/services/admin_codes.py、现有测试（含 tests/test_admin_products.py）、前端、.platform/、CI 与部署配置，不装依赖，不改设计
```
