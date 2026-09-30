# TODO — 开发任务清单

> 最后更新：2026-09-30

---

## 本文件的约定

1. 一个 session 做一个编号任务：读本文件 → 做完 → 验收 → commit，中途不需要逐步审批。
2. **对计划本身有异议时才停下来讨论**，不要在每个任务上重新走一遍「先计划、等确认」。
3. 做完把 `[ ]` 改成 `[x]`，并在该任务下补一行记录：做了什么、偏离了什么、验证到什么程度。复选框只允许 `[ ]` / `[x]`。
4. 本文件里**没有**的工作（临时需求、探索型任务）才需要先写计划、等确认。

---

## 规划（OpenClaw planning-v1）

> 下面两个标记之间的块由 OpenClaw 控制面按固定格式解析，决定下一项任务；只管「做什么、先做什么」，执行权限仍以 `.platform/tasks.yaml` 为准。格式不对整块作废，项目 fail closed。

<!-- 块内每一行只能是下面三种格式之一（空行可以有，别的内容一律不行，包括注释）：
     当前计划：`1. `<任务 id>` <标题>`，从 1 连续编号，最多 20 项；每一项都必须在 tasks.yaml 登记为 ready，第一项就是下一次「开启」的任务。
     已阻塞：  `- `<任务 id>` <标题>｜阻塞：<原因>` 或 `- 待登记：<标题>｜阻塞：<原因>`（分隔符是全角竖线）。
     后续计划：`- `<任务 id>` <标题>` 或 `- 待登记：<标题>`。
     任务 id 必须符合控制面登记的 task_id_pattern，全块不重复；tasks.yaml 里每个 ready 任务都必须出现在块里。
     「待登记」行不带 id，只给人看，不会变成可执行的东西。三个标题与两个标记逐字不改。 -->

<!-- openclaw:planning-v1:begin -->
### 当前计划
1. `SHOP-TASK-007` 运费区与演示汇率的数据模型、示例费率与查询函数

### 已阻塞
- 待登记：用户可见页面实现｜阻塞：等 SHOP-TASK-003 修订后的线框与三语文案经 Kelvin 审阅

### 后续计划
- 待登记：购物车与结账计价 API
- 待登记：模拟支付与订单状态机
- 待登记：订单查询与确认收货
- 待登记：退款申请与审核
- 待登记：会员注册登录（Twilio Verify + 人机挑战）
- 待登记：优惠券与积分账本
- 待登记：管理后台
- 待登记：定时任务（库存重置、积分到期）
- 待登记：运营告警邮件
<!-- openclaw:planning-v1:end -->

---

## 任务记录

### SHOP-TASK-001 前端页面结构、线框与三语文案审阅稿

- [x] 交付审阅稿 `docs/UX.md` 与 `docs/UX-COPY.md`（Kelvin 审阅尚未进行；审阅通过前不实现任何页面）
- 做了什么：`docs/UX.md` 写全局框架、页面地图及 14 个前台页（P01–P14：首页、商品列表、详情、购物车、结账、模拟支付、支付结果、订单查询、订单详情/确认收货、退款申请、注册、登录/忘记密码、会员中心、隐私说明）与 7 个后台页（A01–A07），每页有目的、入口、去向、演示提示、桌面与手机文本线框，以及逐处标注 `docs/DESIGN.md` 1.6 小节标题的「金额与个人资料元素」表；`docs/UX-COPY.md` 写默认英文的英/中/马三语文案表，三列均无空格，含每页演示提示及下单、模拟支付、退款三处专门提示；收货表单旁告知不真实扣款、不真实发货、30 天后匿名化，不设勾选框。联系入口只写 WhatsApp 占位。
- 偏离：无。未改设计、需求、`frontend/`、后端、CI 或部署配置。
- 待决问题：15 项（Q1–Q15，两份文档同一编号），含下单后支付页授权、第 30 天退款截止与资料删除同日、备份残留期对外措辞、虚构电话被真实号码持有人认领等，均交 Kelvin 决定。
- 审阅修改第 1 轮：线框中原先直接写成中文的界面文字（如「加载更多」「订单摘要」「金额明细」「进度」「查看」「设置」、积分明细类型、隐私页段标题、后台按钮、字段、列头、事件记录与库存重置明细）全部改为文案键，并在 `docs/UX-COPY.md` 补齐英/中/马三列；`docs/UX.md`「阅读说明」写明不在 `[ ]` 内的中文只是审阅标注、不向用户显示；订单号统一标 `[K]`（依据「权限与资料保护」），P06–P10、P13、A02、A03 逐处标注并列入各页表格；两份文档的 Q14 改为同一措辞。待决问题数不变。
- 验证到什么程度：人工逐条对照验收标准自查；核对线框引用的文案键（含本轮新增键）均在 `docs/UX-COPY.md` 中定义，并逐页检查线框中向用户显示的文字均已写成文案键，其余中文仅为标注；检索两份文档不含 `://` 形式地址与邮箱。马来文未经母语者审校（Q14）；未做浏览器或视觉验证（本任务不实现页面）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-002 计价逐件分摊纯函数

- [x] 按 `docs/DESIGN.md` 1.6（提交 `845fd93`）「计价、优惠、积分与库存」第 2–4 条实现 `app/services/pricing.py`，测试见 `tests/test_pricing.py`
- 做了什么：`price_order` 按订单行序、件序展开商品；百分比券（`Decimal` 比率，`Decimal(10)` 即 10%）对整单商品小计算一次，用 `decimal` 的 `ROUND_HALF_UP` 取整到仙；固定额券取券额与商品小计较小者。券折扣按每件原价、积分抵扣按每件券后金额，用同一个最大余数函数 `allocate_largest_remainder` 分摊：先向下取整，余下的仙按余数从大到小补，平手按行序、件序，分母为零时全为零。`earned_points` 对整单现金实付合计每满 100 仙给 1 积分，再按逐件现金实付用同一算法分摊。积分抵扣超过券后商品额、任何负数、百分比不在 0–100 之间（含 NaN）、同时给两种券时拒绝（`ValueError`）；金额与积分参数不是 `int`（含 `float`、`bool`）或比率不是 `Decimal` 时拒绝（`TypeError`）。只 import 标准库。
- 偏离：无。按第 3 条「100 积分抵 RM1」，1 积分按 1 仙抵扣；`price_order` 结果里的获得积分是模拟支付成功后应给的数，发放时机由后续任务的调用方决定。未改设计，未加表、迁移或接口。
- 待决问题：无。第 2–4 条与第 4 条算例逐项一致，未发现设计本身的问题。
- 验证到什么程度：第 4 条算例已按实现的整数算法人工推算一遍（券 100/100/50、积分 120/120/60、现金 780/780/390、获得 19 积分分为 8/7/4），测试逐字钉住这些数字；另覆盖半仙进位（1245 仙 × 10% = 125）、固定额券大于小计、余数平手按行序件序、零小计、积分超额、负数与 float 输入。检查命令结果由 Worker 另行记录。

### SHOP-TASK-003 按设计 1.8 修订页面线框与三语文案审阅稿

- [x] 修订审阅稿 `docs/UX.md` 与 `docs/UX-COPY.md` 至 0.2，依据改为 `docs/REQUIREMENTS.md` 1.7 与 `docs/DESIGN.md` 1.8（批准记录见 `docs/HANDOFF.md` 0.16）；顶部保留「审阅稿，Kelvin 审阅通过前不实现任何页面」，交 Kelvin 审阅
- 改了什么：结账 P05 改为先填手机号的三步流程——马来西亚、新加坡号码经人机挑战与短信验证码后自动注册或登录，再以会员身份继续；白名单外号码走游客流程；服务端判定短信无法送达或停发时显示可改为游客下单，验证码错误或超次不降级；已登录会员跳过验证，收货电话默认会员手机号且可改。手机号旁 `checkout.phone_notice` 告知自动注册、手机号保留至注销、可在会员中心注销；收货表单旁 `checkout.form_notice` 改为不真实扣款、不真实发货、收货资料长期保存；删除所有「30 天后匿名化」文案及 `order.recipient_anonymised`、`privacy.retention_backup`、`auth.register_submit`；不设勾选框、不设访客删除入口。新增短信验证组件 V1（结账验证、注册、短信登录、重设密码、注销确认共用）。P06、P07 按游客短期凭据 `[G]` 显示该单收货资料，不设确认收货、退款或其他订单入口，过期提示凭订单号和电话查单；P06 为游客与会员都提供取消按钮及确认框。P08–P10 按查单授权 `[L]`：仅本浏览器、仅该单、30 分钟，可查看、确认收货、申请退款，不能支付或取消，查其他订单须重新输入；P09、P10 另有会员模式。P12 分密码与短信验证码两种登录，未设密码与密码错误同一条 `auth.login_failed`；P13 增加首次设置密码、可用公开券与使用记录、短信验证码确认注销。`pay.method_card` 改为演示信用卡/借记卡（无需输入卡号），`detail.english_only` 改为「仅英文」，参考外币只在结账页选定收货国家后显示（审阅修改：从支付页 P06 删去参考外币金额及其 M2 行，`common.fx_reference` 说明删去「及订单快照中」），WhatsApp 未配置时隐藏，后台不设导出。所有「金额与个人资料元素」表改标 DESIGN 1.8 小节（「保留与匿名化」改为「资料保留」），两种授权相关元素标「权限与资料保护」；前台 `auth.login_failed` 措辞改动后，后台 A01 改用新键 `admin.login_failed`。新增或改动的文案键均在 UX-COPY 填齐英、中、马三列。
- 偏离：无。未改设计、需求、交接单、`frontend/`、后端、CI 或部署配置；未写任何真实联系方式。
- 待决问题变化：共 19 项，Q1–Q15 编号不变。已决 11 项（Q1、Q2、Q5、Q7、Q12、Q15 依据 DESIGN 1.8 小节；Q6、Q8、Q9、Q10、Q13 依据 Kelvin 2026-09-29 的决定）；Q3、Q4 因收货资料不再删除而不再适用；Q11 部分已决（1.7 起白名单号码不再用于游客订单，短信降级下单或扩大白名单时认领风险仍待 Kelvin 决定）；Q14 马来文审校仍待决。新增待决 Q16（结账第一步尚无收货国家，默认区号如何定）、Q17（会员待支付订单能否从会员中心回到支付页）、Q18（注销后手机号在备份中残留的对外措辞，承接原 Q3）、Q19（`Coupon` 无公开字段，「可用的公开券」范围）。
- 验证到什么程度：人工逐条对照验收标准自查；用检索核对 UX.md 线框引用的文案键均在 UX-COPY 中定义（含本轮新增的 59 个键），已删除的键不再被引用；检索两份文档不再出现「DESIGN 1.6」、`://` 形式地址或邮箱，「匿名化」只出现在说明已删除或「不匿名化」的语境中。马来文未经母语者审校（Q14）；未做浏览器或视觉验证（本任务不实现页面）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-004 商品目录数据模型与迁移

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

### SHOP-TASK-005 商品目录只读查询与筛选 API

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 Product / Variant 一行与「边界与原则」，在 SHOP-TASK-004 的表上提供公开只读的商品目录接口：`app/db/session.py`、`app/services/catalog.py`、`app/api/catalog.py`，`app/main.py` 挂上路由，测试见 `tests/test_catalog_api.py`
- 接口清单（只有 GET，全部在 `/api/catalog/` 下；`lang` 只接受 `en`、`zh`、`ms`，默认 `en`，其他值 422）：
  - `GET /api/catalog/categories?lang=`：启用且有英文名称的分类，按 id 排序；每项 `slug`、`name`。
  - `GET /api/catalog/products?lang=&q=&category=&option=&sort=&page=&page_size=`：已发布商品的一页，返回 `total`、`page`、`page_size`、`items`；每项 `slug`、`name`、`category`（`slug`、`name`）、`image`（排列序号最小的图片引用，没有图片为 null）、`min_price_sen`（启用 SKU 最低单价）、`sold_out_today`（所有启用 SKU 的当日可用库存均为 0）。`category` 可重复，多个分类为或；`option` 可重复，写作 `<规格名 code>:<规格值 code>`，同一规格名下多个值为或、不同规格名之间为且，且须由同一个启用 SKU 同时满足，格式不对 422；`sort` 只接受 `newest`（默认）、`price_asc`、`price_desc`，价格按最低单价，平手一律按商品 id 升序；`page` 从 1 起，`page_size` 默认 24、上限 48，超过上限 422。
  - `GET /api/catalog/products/{slug}?lang=`：`slug`、`name`、`description`、`category`、`images`（按排列序号）、`options`（规格名按排列序号，每个含 `code`、`name` 与按序的 `values`）、`variants`（每个启用 SKU 的 `sku`、`options`（规格名 code → 所选规格值 code）、`price_sen`、`available_stock`，按 id 排序）。未发布与不存在的 slug 返回同一个 404。
  - 所有文案字段都是 `{text, english_fallback}`：缺少请求语言时给英文并把 `english_fallback` 置真，供页面显示「仅英文」标签；文案为 NULL、空串或只有空格都算缺少。金额只有 MYR 整数仙，不含参考外币、每日初始库存、启用状态等后台字段。
  - 发布规则在一个 SQL 条件里判断：商品与所属分类都启用，分类英文名称、商品英文名称与英文描述、该商品所有规格名与规格值的英文名称齐全，且至少有一个启用的 SKU。搜索按当前语言名称或英文名称做不区分大小写的包含匹配，`%`、`_` 按字面匹配。
  - `app/db/session.py`：`get_session` 依赖在第一次请求时按 `SHOP_DATABASE_URL` 建引擎与会话工厂（同一连接串只建一次），每个请求一个会话、不提交；未配置数据库时应用照常启动、健康检查不受影响，目录接口返回 503。
- 偏离：未改设计与审阅稿。与 `docs/UX.md` 0.2 的出入：P02 线框的规格筛选栏需要列出可选的规格名与规格值，验收标准的列表字段里没有这一项，本任务未提供，留给页面任务按需另行登记；P01 线框的分类卡片带图，`categories` 表没有图片列，分类列表不给图片。规格名属于单件商品，跨商品筛选按规格名与规格值的 code 匹配。「最新」的平手同样按商品 id 升序。「今日售罄」与当日可用库存直接读 `available_stock`，每日重置由之后的任务维护。`app/core/config.py` 中 `database_url` 的注释「只有 alembic 用到；应用本身还没有任何表」已过时，该文件不在本任务可改范围内，记为待清理项。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_catalog_api.py` 用 TestClient 与 SQLite 内存库（StaticPool，覆盖 `get_session`）覆盖：已发布商品的列表项与详情整体相等（多一个后台字段即失败，含默认英文）；发布规则的八个条件逐一使商品从列表消失、详情 404 且与不存在的 slug 响应相同（每例先确认改动前是发布的）；分类列表只含启用且有英文名称的分类；回退英文及逐字段标记（中文、马来文）；最低单价与售罄只算启用 SKU、停用 SKU 不出现在详情；搜索（当前语言名、英文名、大小写、英文界面不按中文名匹配、`%` 字面匹配）；分类筛选为或；规格筛选同名为或、异名为且，含反例（颜色只由 SKU-A 命中、尺寸只由 SKU-B 命中时不出现；唯一满足的 SKU 停用时不出现）；三种排序、按最低单价而非最高单价、稳定平手、非法排序值被拒；分页总数与每页条数上限；非法语言参数（含 `EN`）在三个接口都被拒；格式不对的规格筛选被拒；三个接口只发 SELECT、写方法 405；未配置数据库时健康检查 200、目录接口 503。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未在真 MySQL 上跑这些查询（CI 的 MySQL 只用于迁移检查）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-006 示例商品目录种子数据

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 Product / Variant 一行，在 SHOP-TASK-004 的表上用数据迁移 `alembic/versions/20260930_0003_demo_catalog.py`（revision `0003`，down_revision `0002`）写入示例目录；占位图 `frontend/public/demo-images/*.svg`；`frontend/Dockerfile` 在 `COPY src ./src` 旁加 `COPY public ./public`；测试见 `tests/test_demo_catalog_seed.py`
- 做了什么：数据全是迁移文件内的常量（`CATEGORIES`、`OPTIONS`、`OPTION_VALUES`、`PRODUCTS`），用 `sa.table` 轻量表定义与 `op.bulk_insert` 写入，不 import app 的模型；写完一层按 slug / SKU / 商品 id 查回 id 再写下一层。downgrade 按依赖倒序显式删除：先按 SKU 删规格关联行与 SKU，再按商品 slug 删规格值、规格名、图片、商品，最后按 slug 删分类，不依赖外键级联。
  - 分类（按 id 顺序，全部启用）：`apparel`、`bags`、`home`、`kitchen`、`stationery`、`gadgets`，各 5 件商品，共 30 件，全部启用；76 个 SKU，全部启用。
  - 规格名只有两个 code：`color`（Colour / 颜色 / Warna）与 `size`（Size / 尺寸 / Saiz）。SKU 为 `<商品 slug>-<各规格值 code>`，无规格商品为 `<商品 slug>-std`，全部小写。
  - 商品清单（形态；单价；备注）：
    - apparel：`crew-neck-tee`（颜色×尺寸；RM39–42；缺 navy/xl）、`pullover-hoodie`（颜色×尺寸；RM89–95；缺 grey/xl）、`cotton-cap`（颜色；RM25）、`knit-beanie`（颜色；RM29）、`ankle-socks-3-pack`（无规格；RM15）
    - bags：`canvas-tote-bag`（颜色；RM35）、`everyday-backpack`（颜色×尺寸；RM129–149；缺 grey/large）、`zip-pouch`（无规格；RM18）、`drawstring-bag`（颜色；RM12）、`weekender-duffel`（无规格；RM189）
    - home：`linen-cushion-cover`（颜色；RM45）、`cotton-bath-towel`（颜色×尺寸；RM29–49；缺 navy/medium）、`soy-wax-candle`（无规格；RM32）、`woven-storage-basket`（无规格；RM55；**仅英文**，中文与马来文名称和描述刻意留空）、`round-wall-clock`（颜色；RM69）
    - kitchen：`ceramic-mug`（颜色；RM22–24）、`stainless-water-bottle`（颜色×尺寸；RM45–55；缺 white/large）、`bamboo-cutting-board`（无规格；RM39）、`cotton-apron`（颜色；RM28；**全部 SKU 库存为 0**，演示今日售罄）、`glass-food-container-set`（无规格；RM65）
    - stationery：`dotted-notebook`（颜色；RM16）、`gel-pen-set`（无规格；RM9）、`desk-organiser`（无规格；RM35）、`sticky-notes-pack`（无规格；RM5）、`zip-pencil-case`（颜色；RM14）
    - gadgets：`wireless-mouse`（颜色；RM59）、`braided-charging-cable`（颜色×尺寸，尺寸值为 1 m / 2 m；RM15–19；缺 white/2m）、`folding-phone-stand`（无规格；RM25）、`compact-power-bank`（无规格；RM89）、`portable-speaker`（颜色；RM299）
  - 形态合计：无规格 12 件、单规格（颜色）12 件、双规格（颜色×尺寸）6 件；每件双规格商品都缺一个组合，缺的两个值各自仍有 SKU。单价范围 RM5–RM299（500–29900 仙），同一商品内部分 SKU 价格不同；每个 SKU 的 `available_stock` 等于 `daily_initial_stock`。
  - `created_at`：不带时区的 UTC，第一件为 2026-09-01 01:00，之后每件晚 7 小时，互不相同；按最新排序时清单里越靠后的越靠前。
  - 图片引用格式：每件商品一行 `product_images`，`sort_order` 为 0，`storage_ref` 为以斜杠开头的站点根路径 `/demo-images/<分类 slug>.svg`，指向所属分类的占位图；Vite 把 `frontend/public/` 原样拷到构建产物根目录，nginx 按路径发出，页面可原样用作图片地址。
  - 六张 SVG：400×300 的纯几何图形（rect、circle、ellipse、line、polygon、path），不含文字、script、foreignObject、style、事件属性、href、`url()`、`@import`。
- 偏离：无，未改设计、表结构、接口与前端代码。说明几处取舍，请审阅：
  - 每张 SVG 根元素带 SVG 命名空间声明 `xmlns`，它是命名空间标识而不是链接，也不会被请求；独立的 SVG 文件作为图片加载时缺了它浏览器不渲染。测试禁止任何 `href` 属性，不禁止这个声明。
  - 尺寸规格名的值因商品而异（服饰为 S/M/L/XL，包袋、毛巾、水瓶为 Small/Medium/Large，充电线为 1 m / 2 m 长度），都用 `size` 这一个 code，跨商品筛选 `size:<值>` 只命中用了该值 code 的商品。
  - 刻意的「仅英文」商品选了无规格的 `woven-storage-basket`，因此没有缺中文或马来文的规格名与规格值。
  - 马来文由非母语者撰写，未经母语者审校（同 SHOP-TASK-001 的 Q14）。商品与品牌名均为虚构的通用描述；「无真实品牌与商标」只能由人工审阅确认，测试只能检查不含网址、邮箱与长串数字。
  - downgrade 只保证在写入后未经后台编辑、未被其他数据引用的库上成立（CI 与开发库）；部署只执行 upgrade。示例商品在生产库经后台编辑或被之后的订单引用后，不承诺可回退。
  - 迁移写完一层要查回 id，不支持离线 `--sql` 模式生成脚本；部署只在线 upgrade，不受影响。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_demo_catalog_seed.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，经 Alembic 的 `Operations` 执行迁移文件的 upgrade，再查询实际写入的行，覆盖：分类数量、顺序与启用；商品数量、启用与每分类下限；slug 与 SKU 唯一、小写且与迁移常量一致；英文齐全；只有一件商品缺中文与马来文，其余分类、规格名、规格值齐全；文案不含网址、邮箱与电话样数字串；三种规格形态各至少 3 件、每个 SKU 在每个规格名下恰好一个值、组合不重复；每件双规格商品缺一个组合，且用 SHOP-TASK-005 的 `list_products` 验证缺的两个值分别能筛出、同时筛不出；单价是整数且在范围内、不全相同；可用库存等于每日初始库存；至少一件售罄；创建时间互不相同、不带时区且早于迁移日期；每件商品恰好一张图片、引用指向所属分类且文件存在；全部商品按发布规则可列出、中文界面恰好一件回退英文；downgrade 删净示例数据的七张表、另插入的非示例分类与商品原样保留；六张 SVG 不超过 4 KB、只有 SVG 命名空间下的几何元素、无文字内容、无禁用元素与属性。这些测试及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`、前端镜像构建，都只由 PR 的必需 CI 检查 backend 与 frontend 执行，Worker 沙箱不跑。未做浏览器或视觉验证。检查命令结果由 Worker 另行记录。

### SHOP-TASK-007 运费区与演示汇率的数据模型、示例费率与查询函数

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 ShippingRate / DemoFxRate 一行与「边界与原则」建立运费区与演示汇率两张表：`app/models/shipping.py`、迁移 `alembic/versions/20260930_0004_shipping_fx.py`（revision `0004`，down_revision `0003`）、数据迁移 `alembic/versions/20260930_0005_demo_shipping_fx.py`（revision `0005`，down_revision `0004`）、只读查询 `app/services/shipping.py`；测试见 `tests/test_shipping.py` 与 `tests/test_demo_shipping_seed.py`
- 两张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；检查约束只用 LENGTH、LIKE、比较与 IN）：
  - `shipping_rates`：`id`（`pk_shipping_rates`）、`zone_type` String(10)（`my_state` / `country` / `other`）、`zone_code` String(5)（唯一 `uq_shipping_rates_zone_code`）、`fee_sen` Integer（MYR 整数仙）、`version` Integer，全部非空。检查约束：
    - `ck_shipping_rates_zone_code_matches_type`：`(zone_type = 'my_state' AND LENGTH(zone_code) = 5 AND zone_code LIKE 'MY-%') OR (zone_type = 'country' AND LENGTH(zone_code) = 2 AND zone_code <> 'MY') OR (zone_type = 'other' AND zone_code = 'OTHER')`，同时把类型限定为这三种
    - `ck_shipping_rates_fee_sen_non_negative`：`fee_sen >= 0`
    - `ck_shipping_rates_version_positive`：`version >= 1`
  - `demo_fx_rates`：`id`（`pk_demo_fx_rates`）、`country_code` String(2)（唯一 `uq_demo_fx_rates_country_code`）、`currency_code` String(3)、`currency_decimals` Integer、`rate` Numeric(18, 6, asdecimal=True)（1 MYR 等于多少该币种；全项目唯一用定点小数存的数，金额仍一律整数仙）、`version` Integer，全部非空。检查约束：
    - `ck_demo_fx_rates_country_code_valid`：`LENGTH(country_code) = 2 AND country_code <> 'MY'`
    - `ck_demo_fx_rates_currency_code_valid`：`LENGTH(currency_code) = 3 AND currency_code <> 'MYR'`
    - `ck_demo_fx_rates_currency_decimals_range`：`currency_decimals >= 0 AND currency_decimals <= 3`
    - `ck_demo_fx_rates_rate_positive`：`rate > 0`
    - `ck_demo_fx_rates_version_positive`：`version >= 1`
  - 0004 的 downgrade 先删 `demo_fx_rates` 再删 `shipping_rates`。
- 示例数值（0005 写入，版本号均为 1，全部是迁移内常量，用 `sa.table` 轻量表定义与 `op.bulk_insert` 写入，不 import app 的模型）：
  - 运费（仙）：州属 MY-12、MY-13、MY-15 为 1500，MY-01 至 MY-11、MY-14、MY-16 为 800；国家 SG 2000、BN 2500、TH 3000、CN 3500、JP 4500、AU 5000、US 6000、GB 6000；兜底 OTHER 8000。共 25 行。
  - 汇率（虚构的固定演示值，不是市场汇率；国家、币种、小数位、1 MYR 等于）：SG SGD 2 0.310000；TH THB 2 7.600000；CN CNY 2 1.650000；JP JPY 0 34.000000；AU AUD 2 0.350000；US USD 2 0.230000；GB GBP 2 0.170000；HK HKD 2 1.800000。共 8 行。BN 刻意不配汇率（只显示 MYR），HK 刻意不配国家运费（用兜底运费）。
  - downgrade 按区域代码与国家代码删除这批行；只保证在写入后未经后台编辑的库上成立，部署只执行 upgrade、从不降级。
- 查询函数（`app/services/shipping.py`，只发 SELECT，不写库、不缓存费率）：
  - `quote_shipping(session, country_code, state_code) -> ShippingQuote(fee_sen, zone_code, version)`。国家代码须是两位大写 ASCII 字母，否则抛 `InvalidDestination`（`ValueError` 子类），不转换大小写。`MY` 须给 `MY-01` 到 `MY-16` 之一，缺少或未知抛 `InvalidDestination`；其他国家给了州属代码（含空串）抛 `InvalidDestination`，有国家行用国家行，否则用 `OTHER` 行。兜底行不存在、或马来西亚州属代码合法但表里没有该行时抛 `ShippingRateMissing`（`LookupError` 子类），不返回零运费，州属缺行也不退回兜底。
  - `reference_amount(session, country_code, amount_sen) -> FxReference(currency_code, currency_decimals, amount_minor, version) | None`。国家代码校验同上；金额为负抛 `ValueError`，不是 `int`（含 `bool`、`float`）抛 `TypeError`，在查库前校验；`MY` 与没有汇率行的国家返回 `None`，不猜测、不请求外部汇率。
  - `convert_sen(amount_sen, rate, currency_decimals) -> int`：不碰数据库的纯函数。`amount_sen × rate ÷ 100` 再移到该币种最小单位，用 `decimal` 的 `ROUND_HALF_UP` 取整（恰好半个最小单位进一），精度按输入位数留足，唯一的舍入在最后一步，全程不用浮点。`rate` 不是 `Decimal`（含 `float`、`int`、字符串）抛 `TypeError`；非有限、不大于零、或去掉尾随零后超过 6 位小数抛 `ValueError`；小数位须是 0 到 3 的 `int`。
- 偏离：无，未改设计、接口、`app/main.py` 与前端代码。说明几处取舍，请审阅：
  - 数据库不保证大小写与字母：MySQL 默认排序规则不区分大小写（`'my'` 与 `'MY'` 比较相等、唯一约束也按不区分大小写判断），SQLite 的 LIKE 对 ASCII 不区分大小写而 `<>` 区分；MySQL 的 LENGTH 按字节计。代码是大写字母由写入方（本任务的 0005，之后的后台维护）与查询函数校验，`tests/test_demo_shipping_seed.py` 确认 0005 写入的代码都是大写。
  - 州属行的检查约束按验收标准只要求以 `MY-` 开头且长 5 位，`MY-99` 这类代码数据库不拒绝；是否属于 MY-01 到 MY-16 由写入方与查询函数校验。国家代码是否真实存在留给之后带国家列表的结账任务。
  - 类型与代码的搭配写成一个检查约束，它同时拒绝三种以外的类型，没有另设类型枚举约束。
  - 汇率「超过 6 位小数」按数值判断：`Decimal("0.3100000")` 去掉尾随零后只有 2 位，接受；`Decimal("0.3100001")` 拒绝。
  - SQLite 没有原生定点小数，SQLAlchemy 经浮点存取并发出 SAWarning；两个测试文件用 `filterwarnings` 只忽略这一条，并只断言 6 位小数的示例汇率读回相等，不在 SQLite 上断言完整 18 位精度的往返。MySQL 上是 DECIMAL(18, 6)，PyMySQL 直接返回 Decimal。
  - 版本号：本任务的行一律为 1；每次修改一行费率把该行版本号加一由之后的后台维护任务实现，本任务不实现修改。
- 验证到什么程度：人工逐条对照验收标准自查，并逐个核对 0004 的列类型、可空性、约束名与表达式、表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）；人工按实现的算法推算算例（1950 仙 × 0.310000 = 6.045 SGD → 605；25 仙 × 34 = 8.5 JPY → 9；1949 仙 → 604；1000 仙 × 1.8 → HKD 1800）。`tests/test_shipping.py` 用 SQLite 内存库按模型建表，覆盖：三种合法行与零运费可写入（对照）；负运费、运费行与汇率行版本号 0、类型与代码搭配不符（12 例，含国家行为 MY、未知类型）、重复区域代码、零与负汇率、小数位 -1 与 4、币种 MYR 与长度不对、汇率国家 MY 与长度不对、重复汇率国家各被对应的约束拒绝；汇率读回是相等的 Decimal；运费查询的州属、未知与缺少州属（含小写 `my-01`）被拒、州属缺行报错、非马来西亚带州属被拒、国家行、兜底、兜底缺失报错、非法国家代码（含小写、全角、非字符串）被拒、改行后立即读到新值、查询只发 SELECT；参考外币的有汇率、无汇率、马来西亚返回空、非法国家代码与金额被拒；换算钉住三个算例与半位以下舍去，拒绝负数、`bool`、`float` 金额与 `float`、`int`、字符串、零、负、超过 6 位小数、NaN、无穷的汇率。`tests/test_demo_shipping_seed.py` 经 Alembic 的 `Operations` 执行 0005 的 upgrade，查询实际写入的行逐项核对 16 个州属、8 个国家、兜底与 8 条汇率（期望值按验收标准另写，不取迁移常量）、行数恰好、版本号均为 1、代码均为大写，并用查询函数验证 SG、BN、HK、FR（两表都没有）与 MY-10、MY-13；再执行 downgrade，示例行删净、另插入的 NZ 运费行与汇率行保留。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；查询函数未在真 MySQL 上执行（CI 的 MySQL 只用于迁移检查）。检查命令结果由 Worker 另行记录。
