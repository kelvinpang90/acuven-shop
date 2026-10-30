# SHOP-TASK-185 保存商品时新增规格值与 SKU

- [x] 依据：`docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 11 条（可新增规格值与 SKU；新增的规格值与 SKU 各带一个请求内的临时编号，新增 SKU 以行 ID 引用已有规格值、以临时编号引用同一次保存里新增的规格值；新增的排在已有的之后；第三步查新增 SKU 的组合与已有 SKU（含停用的）不重复；新增 SKU 的当日可用库存等于每日初始库存）；`docs/HANDOFF.md` 0.77 的落实写法（代码生成复用 SHOP-TASK-183 的函数；新增 SKU 以行 ID 引用了请求里没有列出的规格值归第一步「取值不合格」；新增的规格值与 SKU 各写一条 `*_created` 审计）。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 11 条与 HANDOFF 0.77。不做接口与前端，不改模型与迁移、`app/services/catalog.py`、`app/services/checkout.py`、`app/services/admin_codes.py`、现有测试与设计。
- [x] 入口（`app/services/admin_products.py`）：仍是 SHOP-TASK-184 的 `save_product`，签名与返回不变；去掉 `_check_save_request` 里「请求带新增项即 `ProductInvalid`」，已有行的比较、写入与审计不变。183 的请求结构、`create_product`、两个 load 函数与 `check_product_request` 都未改动。
- [x] 第一步：共用检查 `check_product_request(creating=False)` 本来就按请求里的全部项判断，不需要扩展——每个规格名 1–20 个值与 1–50 个 SKU 按「现有加新增」计数，英文名称不重复在现有与新增之间一起比较，新增的规格值英文名称必填，新增 SKU 的 `option_values` 个数等于规格名个数，以行 ID 引用的须是请求里该规格名下列出的已有值（`_check_ref`），以 `temp_id` 引用的须是同一规格名下新增的值，新增 SKU 之间组合不重复，`temp_id` 的格式与不重复。不通过抛 `ProductInvalid`、不发任何 SQL。
- [x] 第二步（`_lock_and_match`）：行 ID 的比较只取请求里带 id 的规格值与 SKU，新增的项不参与；新增 SKU 以行 ID 引用的已有值若请求把它列在别的规格名下，「规格值 ID → 规格名 ID」的映射与库里不同，即 `ProductChanged`。
- [x] 第三步（同一事务，在分类检查之后）：`_check_new_combinations` 读该商品的 SKU 规格值关联，新增 SKU 只以行 ID 引用已有值、且所选值集合与某个已有 SKU（含停用的）相同时抛 `ProductInvalid`（经外层回滚）；引用了新增值的组合不会与已有的相同。没有新增 SKU 时不查库。
- [x] 写入（`_insert_additions`，在已有行的比较与写入之后）：先生成全部代码再插入。新增的规格值按规格名的顺序、同一规格名下按请求顺序，`sort_order` 从该规格名已有的最大值加一起，代码经 `generate_value_codes`（taken 为该规格名已有的代码）沿用全站同一规格名代码之下的代码并在该规格名下去重；新增的 SKU 按请求顺序插入，代码经 `generate_skus` 以商品 slug 按规格名的顺序（库里的 sort_order）接上各值的代码、全站去重，`available_stock` 等于 `daily_initial_stock`，并写入各规格名下的关联行。flush 撞上唯一约束即 `ProductChanged`。
- [x] 版本与审计：有改动或新增时商品版本号加一（与已有行的改动合并只加一次）；已有行的 `*_changed` 审计之后，每个新增的规格值一条 `admin_product_option_value_created`（对象 `product_option_value`）、每个新增的 SKU 一条 `admin_product_variant_created`（对象 `product_variant`），对象 ID 为新行 ID，新值为代码，没有旧值。写入途中任何异常都先回滚再抛出。
- [x] 测试：新增 `tests/test_admin_product_additions.py`（写法照 `tests/test_admin_product_save.py`，每条测试的文档字符串写明它守住的第 11 条原句）。覆盖：新增规格值并以 `temp_id` 被新增 SKU 引用、新增 SKU 以行 ID 引用已有规格值；新增的规格值排在已有值之后（已有最大 sort_order 加一起、按请求顺序）；与停用或启用的已有 SKU 组合相同被拒且其余改动不留下；引用别的规格名下的值、请求里没列出的值、别的规格名下新增的值、不存在的临时编号、个数不对、id 与 temp_id 同时或都不给、新增值缺英文名称或与已有值重名、新增 SKU 之间组合相同、临时编号重复或格式不对，都在第一步被拒且不发 SQL；Colour 共 20 个值与共 50 个 SKU 可以保存、21 个值与 51 个 SKU 被拒；新增规格值沿用全站代码并在规格名下去重、SKU 全站去重；生成的代码撞上唯一约束（替换生成函数模拟）为 `ProductChanged`；带新增项时版本号不符或引用的值实际属于别的规格名为 `ProductChanged`；新 SKU 的当日可用库存等于每日初始库存、已有 SKU 的不变，`quote_cart` 按新 SKU 的价格计价；只新增与「改已有行加新增」都只加一次版本；审计的操作名、对象与新值；审计出错时整次回滚；带新增项时加锁顺序不变。
- 偏离与取舍：
  - 共用检查没有扩展：它对保存请求的处理（已有 SKU 的 `option_values` 为 None、ValueRef 以行 ID 引用请求里列出的已有值、按请求里全部项计算上限与查重）在 183 写成时已经具备，184 也已照用。
  - 组合查重按「所选规格值 ID 的集合」比较：规格值 ID 在商品内唯一、各属一个规格名，集合相同即每个规格名下的值相同。
  - 只新增规格值、不新增 SKU 也算改动（加版本、写审计），设计没有要求新增的值必须被 SKU 引用。
  - SKU 代码按库里规格名的顺序（sort_order 再按 id）取值的代码，不按请求里规格名的排列；两者在正常的保存请求里相同。
  - 审计顺序：已有行的改动在前，之后新增的规格值（按规格名的顺序再按请求顺序）、新增的 SKU（按请求顺序）。
- 验证范围：上面列出的是本次新增的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。SQLite 不复现 MySQL 的行锁、共享锁与两个会话同时保存时的唯一约束冲突，后者以替换生成函数模拟。
