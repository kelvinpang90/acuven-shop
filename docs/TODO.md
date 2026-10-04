# TODO — 开发任务清单

> 最后更新：2026-10-04

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
1. `SHOP-TASK-019` 订单访问授权：服务端会话、授权记录、cookie 与 CSRF
2. `SHOP-TASK-022` 站点设置：短信验证开关的存储与读取
3. `SHOP-TASK-020` 游客下单 API
4. `SHOP-TASK-021` 模拟支付、取消与支付超时 API
5. `SHOP-TASK-023` 结账参考数据：地区与国家码列表、马来西亚州属列表
6. `SHOP-TASK-033` 框架测试按路由表推算尚未实现的页面
7. `SHOP-TASK-024` 模拟支付 P06 与支付结果 P07（游客）
8. `SHOP-TASK-025` 结账 P05 游客路径
9. `SHOP-TASK-026` Redis 连接与限流基础
10. `SHOP-TASK-027` 订单查询与确认收货 API
11. `SHOP-TASK-028` 订单查询 P08 与订单详情 P09（查单模式）
12. `SHOP-TASK-029` 退款申请 API（查单模式）
13. `SHOP-TASK-030` 退款申请 P10 与订单详情 P09 的退款部分（查单模式）
14. `SHOP-TASK-031` 定时任务运行器与支付超时取消
15. `SHOP-TASK-032` 每日库存重置

### 已阻塞
- 待登记：短信验证组件 V1 与结账会员路径（马新号码验证、自动注册或登录、优惠券与积分；会员访问 P06、P07）｜阻塞：依赖短信验证服务、会员注册登录接口、优惠券与积分账本
- 待登记：注册 P11 与登录、忘记密码 P12｜阻塞：依赖会员注册登录接口与短信验证组件 V1
- 待登记：会员中心 P13 与订单详情、退款申请的会员模式｜阻塞：依赖会员注册登录接口、会员注销、优惠券与积分账本
- 待登记：后台登录 A01 与后台框架｜阻塞：依赖管理后台登录、会话与审计记录
- 待登记：后台订单与模拟发货 A02、退款审核 A03｜阻塞：依赖后台登录 A01 与管理后台业务接口
- 待登记：后台商品 A04（含每单限购）与库存重置结果 A07｜阻塞：依赖后台登录 A01、管理后台业务接口与每日库存重置
- 待登记：后台优惠券 A05、运费区与演示汇率 A06｜阻塞：依赖后台登录 A01 与管理后台业务接口
- 待登记：后台店铺装修 A08（含精选商品）与前台按设置显示｜阻塞：依赖后台登录 A01 与店铺装修设置的存储与读取
- 待登记：后台站点设置 A09（短信验证开关）｜阻塞：依赖后台登录 A01 与管理后台业务接口

### 后续计划
- 待登记：店铺装修设置的存储与公开读取（主题、主色、标志、首页区块、精选商品）
- 待登记：WhatsApp 联系链接的配置来源（上线前；配置缺失时前台继续隐藏）
- 待登记：短信验证服务（Turnstile 核验、Twilio Verify、限流与每日预算；按短信验证开关停发）
- 待登记：会员注册登录接口（短信与密码登录、设置与重设密码、认领游客订单；短信验证关闭时暂停短信相关入口）
- 待登记：会员注销（短信验证关闭时以密码或已登录会话确认）
- 待登记：短信验证记录到期删除（保留期待定，短信正式上线前完成）
- 待登记：公开接口限流（计价、下单）
- 待登记：优惠券与积分账本
- 待登记：管理后台登录、会话与审计记录
- 待登记：管理后台业务接口（商品与每单限购、订单与发货、退款审核与全部退款后冻结履约、优惠券、运费与汇率、库存重置结果、店铺装修、站点设置）
- 待登记：发货满 7 天自动确认收货（定时任务；全部退款后冻结的订单不自动完成）
- 待登记：积分到期（定时任务，依赖优惠券与积分账本）
- 待登记：运营告警邮件（上线前提：含定时任务失败告警，Kelvin 2026-10-01 决定）
- 待登记：马来文文案母语审校（上线后补，Kelvin 2026-09-30 决定）
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

### SHOP-TASK-008 按 Kelvin 审阅意见把线框与三语文案修订为 0.3

- [x] 修订 `docs/UX.md` 与 `docs/UX-COPY.md` 至 0.3（2026-09-30），依据改为 `docs/REQUIREMENTS.md` 1.8 与 `docs/DESIGN.md` 1.9（2026-09-30 获 Kelvin 批准，记录见 `docs/HANDOFF.md` 0.19）；顶部改为「审阅稿，Kelvin 已于 2026-09-30 审阅，0.3 合并后可开始页面实现」；两份文档各加「0.3 修订要点」
- 改了什么：
  - 依据：两份文档中作为现行依据的 DESIGN 引用（含各「金额与个人资料元素」表头）改为 1.9，REQUIREMENTS 改为 1.8，DESIGN 小节标题不变；注明 Kelvin 2026-09-30 对 Q11、Q14、Q16–Q19 的决定见 `docs/HANDOFF.md`。
  - Q16：P05 线框、说明、「金额与个人资料元素」表（P3、P4 行）与 `checkout.phone_step_hint` 的提示列按 DESIGN 1.9「权限与资料保护」写明第 1 步国家码下拉列出所有国家、默认 +60，以 `+` 开头时以输入为准，之后选的收货国家不改变已判定的号码；游客收货电话即第 1 步号码（只读），以收货国家作为默认区号只用于会员改填的收货电话。
  - Q17：P06 入口、P09 模式说明、去向、线框与表格、P13 去向与说明、`account.order_pay` 提示列写明会员可从 P09 会员模式回到 P06 继续支付或取消；页面地图 P09 去向补 P06。
  - Q18：`privacy.member_backup` 三语改写为注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间，不写天数、不暗示一定清除；P14 线框去掉草稿标注，P13、P14 表格同步。
  - Q19：Q19 正文、P13 说明与 M3 行、A05 演示提示与 `admin.coupon_listed_note` 三语统一为所有启用中且在有效期内的券对全部会员相同列出，不按每会员或总次数上限过滤；不改数据模型。
  - Q11、Q14：标为已决；Q11 不改文案；Q14 在「约定」与正文写明马来文先上线、上线后由母语者审校，马来文仍标为草稿，不再是页面实现前置条件。
  - V1 用于重设密码与注销确认时，短信发送失败或号码不在白名单改显示新键 `auth.sms_not_sent_no_change`，不显示游客文字或 `auth.continue_guest`；V1 线框、P12、P13 同步；结账、注册、短信登录不变（`auth.not_supported_country`、`auth.sms_failed`、`auth.continue_guest` 的提示列改为只用于注册与短信登录，措辞不变）。修订第 1 轮：V1 线框「号码不在白名单」的注册 / 短信登录一行曾误加 `([auth.continue_guest])`，已恢复为 0.2 的只显示 `[auth.not_supported_country]`；P11 线框与说明中该按钮在号码不在白名单时出现的写法是 0.2 原有内容，本任务未改，V1 与 P11 在这一点上的差异沿用 0.2，留待后续统一。
  - P12 忘记密码：未注册号码通过短信验证后不创建账号，显示新键 `auth.reset_not_registered` 与去 P11 的 `common.nav_register`；V1 用途表、P12 桌面与手机线框、说明、去向同步。
  - 文案：`privacy.browser_access` 三语改写为本浏览器获得该单 30 分钟的访问，30 分钟后或在其他浏览器须凭订单号和电话重新查单；`order.cash_paid`、`refund.submit_hint` 三语改为「实付金额（不含积分抵扣）」一类说法；`account.points_pending` 中文改为「退款后尚欠积分」；`home.how_2` 三语改为马新号码经短信验证以会员继续、其他号码以游客结账。`admin.refund_amount` 保留；键名均不变，未删除任何键。
  - 小修：P11 入口改为 P12 `common.nav_register` 与 P07 `result.guest_register`，写明页头只有登录；P08 入口删去 P14；手机版结账顶部折叠摘要在选定收货国家前显示商品小计、之后显示合计；「阅读说明」写明倒计时是 P06 的 15 分钟支付时限，不是 30 分钟的凭据。
- 偏离：手机版结账底部固定栏与顶部折叠摘要同样改为选定收货国家前显示商品小计（验收标准只点名顶部摘要，为免同屏两处不一致一并改）；桌面摘要未改。未改设计、需求、交接单、`frontend/`、后端、CI 或部署配置；未写任何真实联系方式。
- 待决问题变化：仍为 19 项，编号不变，未新增。Q11、Q14、Q16、Q17、Q18、Q19 依据 Kelvin 2026-09-30 的决定转为已决；现为已决 17 项（Q1、Q2、Q5–Q19）、不再适用 2 项（Q3、Q4）、待决 0 项。未发现与 DESIGN 1.9 或 REQUIREMENTS 1.8 冲突之处。
- 验证到什么程度：人工逐条对照验收标准自查；用检索核对两份文档不再出现「待决 Q16」至「待决 Q19」字样与作为现行依据的「DESIGN 1.8」「REQUIREMENTS 1.7」，前台文案不再以「现金 / cash / tunai」指实付金额（后台 `admin.refund_amount` 与 `account.demo_hint` 的「现金价值」除外），两份文档的「待决问题」正文与状态统计一致；新增的 2 个键三列齐全、无变量，线框新引用的键均在 UX-COPY 中定义。马来文未经母语者审校（Q14，上线后补）；未做浏览器或视觉验证（本任务不实现页面）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-009 购物车与结账计价 API（游客计价）

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 Cart 与 ShippingRate / DemoFxRate 两行、「计价、优惠、积分与库存」第 1、2 条及「边界与原则」提供公开、只读的计价接口：`app/services/checkout.py`、`app/api/checkout.py`，`app/main.py` 挂上路由，`app/services/catalog.py` 导出发布条件与文案回退；测试见 `tests/test_checkout_quote.py`
- 接口：只有 `POST /api/checkout/quote?lang=`（`lang` 与目录接口相同，只接受 `en`、`zh`、`ms`，默认 `en`，其他值 422）。不需登录，不读写 cookie；只读、不改状态，所以不要 CSRF 令牌。
  - 请求体（JSON）只有 `lines`（1 到 20 行，每行 `sku`：1 到 64 个字符的字符串，`quantity`：1 到 10 的整数）、可选的 `country_code` 与可选的 `state_code`。多出的字段（含任何价格、金额、运费、汇率、折扣、优惠券字段）一律 422；同一 SKU 出现两次、只给州属不给国家 422；按严格模式校验，件数 `2.0`、`"2"`、`true` 与数字 SKU 都 422。国家与州属代码的合法性沿用 `app/services/shipping.py` 的 `quote_shipping`：国家须两位大写字母，`MY` 须给 `MY-01` 到 `MY-16`，其他国家不许给州属，不转换大小写，不合法 422。
  - 请求体上限 8 KB（8192 字节）：在请求体依赖里用 `request.stream()` 逐块累计实际读到的字节，超过即停止读取并返回 413，不依赖 Content-Length；恰好 8192 字节照常处理。这个依赖写在会话依赖之前，FastAPI 按声明顺序解析，所以超限请求体先于请求模型校验、非法 `lang` 与未配置数据库的 503 得到 413。未超限的请求体再用 `QuoteRequest.model_validate_json` 校验，错误以 `RequestValidationError` 回 422，错误项只含 `type`、`loc`、`msg`，不回显输入。
  - 响应：`lines` 按请求顺序逐行返回，`status` 只有 `ok`、`insufficient_stock`、`unavailable` 三种。
    - `unavailable`：SKU 不存在（逐字比对）、SKU 停用，或所属商品按 SHOP-TASK-005 的发布规则未发布；这一行只有 `sku`、`quantity`、`status` 三个字段，不计入任何金额。
    - `ok` 与 `insufficient_stock`：另有 `available_stock`（库存不足时是当日可用库存，可为 0；正常行为 null）、`product_slug`、`name`（`{text, english_fallback}`）、`options`（按规格名排列序号，每项 `code`、`name`、`value`（`code`、`name`））、`image`（排列序号最小的图片引用，没有为 null）、`unit_price_sen`、`line_subtotal_sen`（单价 × 件数）。当日可用库存小于件数即 `insufficient_stock`，该行照常计入小计。
    - `subtotal_sen`：正常与库存不足各行的商品小计，由 `app/services/pricing.py` 的 `price_order`（不传券与积分）算出。
    - `shipping`（`fee_sen`、`zone_code`、`version`）、`total_sen`（商品小计 + 运费）、`fx_reference`（`currency_code`、`currency_decimals`、`amount_minor`、`version`，按合计换算；该国无汇率或为马来西亚时为 null）：只在给了 `country_code` 时有值，否则三项都为 null，不猜测国家、不看 IP 或请求头。
    - `can_place_order`：所有行均为 `ok` 且给了收货国家时为真，否则为假。
    - 合法目的地因运费行缺失（马来西亚州属行或兜底行）取不到运费时返回 503，`detail` 固定为 `shipping is unavailable`，不含区域代码，不返回零运费；州属行缺失时不退回兜底行。
  - 金额全部是 MYR 整数仙，全程不用浮点；响应不含每日初始库存、启用状态等后台字段，也不含优惠券、积分与会员字段或占位字段。只发 SELECT，不写库、不缓存价格与费率、不建订单、不预留或扣减库存，下单时由之后的任务重新计算与校验。
  - `app/services/catalog.py` 只把 `_published` 改名为 `published`、`_localized` 改名为 `localized` 供结账计价导入，目录接口行为不变，`tests/test_catalog_api.py` 未改。计价查询的外层对 `ProductVariant` 用别名，发布规则里「至少有一个启用的 SKU」的子查询才只与商品关联。
- 偏离：未改设计与审阅稿。说明几处取舍，请审阅：
  - 设计闸门写的是 DESIGN 1.8（提交 `2b68ad0`），UX 审阅稿已改标 DESIGN 1.9；1.9 只改了「权限与资料保护」第 1、2 条与「上线依赖与设计闸门」末条，本任务用到的「数据模型」「计价、优惠、积分与库存」「边界与原则」两版相同，未发现冲突。
  - 与 `docs/UX.md` P04、P05 的出入：P04 的 `[cart.item_changed]`「服务端校验有变化时」显示；浏览器不提交价格，所以接口不比对浏览器保存的价格，页面按各行 `status` 与返回的单价自行判断是否有变化。P05 的优惠券 M3、积分 M4 两行不在本任务，由之后的「优惠券与积分账本」任务扩展本接口。
  - SKU 在 Python 里逐字比对：MySQL 默认排序规则不区分大小写、比较时忽略尾随空格，只靠 WHERE 会把 `tee-red-m` 当成 `TEE-RED-M`。
  - 正常行的 `available_stock` 为 null，只在库存不足时给出可用数；验收标准只要求库存不足时给出。
  - 目的地不合法的 422 响应是 `{"detail": "invalid country or state code"}`，与请求模型的 422 错误列表格式不同：目的地合法性按 `quote_shipping` 判断，不在请求模型里另写一套规则。
  - 请求体由依赖手动读取，OpenAPI 文档里没有请求体的结构描述；请求与响应字段以本段为准。
  - 限流不在本任务，留给之后统一处理公开接口限流的任务；本接口目前没有限流。
- 验证到什么程度：人工逐条对照验收标准自查，并人工推算参考外币算例（7180 仙 × 0.31 = 2225.8 → SGD 2226 分；13180 仙 × 34 ÷ 100 = 4481.2 → JPY 4481）。`tests/test_checkout_quote.py` 用 TestClient 与 SQLite 内存库（StaticPool，覆盖 `get_session`）按模型建表并自建目录、运费与汇率数据，覆盖：正常行整体相等（规格按排列序号、首张图片、单价、行小计，多一个字段即失败）；商品小计等于各行之和且不计不可购买的行、行序与请求一致；SKU 停用与发布规则七个条件逐一使该行只含 SKU、件数与状态（每例先确认改动前是正常的）；SKU 不存在及大小写不同、带尾随空格的 SKU 为不可购买；库存不足标注与可用数（含 0）并计入小计、件数等于可用数仍正常；回退英文及逐字段标记；马来西亚州属、有国家行（有汇率与无汇率）、兜底国家的运费、合计、参考外币与版本号；不给国家时三项为空且不可下单（带来源头也不猜）；可下单标记的四种组合另加不可购买行；行数 0 与 21、件数 0、11、1.5、2.0、字符串与布尔、缺件数、SKU 空串、65 个字符与数字、重复 SKU、行上与顶层的价格金额运费汇率折扣优惠券字段、只给州属、非法国家与州属代码（含小写）、缺州属与非马来西亚带州属、非 JSON 请求体都 422；20 行、件数 10、64 个字符的 SKU 接受；非法语言参数（含 `EN` 与空串）422；恰好 8192 字节接受、8193 字节 413，带多余字段与非法语言参数的超限请求体 413，分块发送不带 Content-Length 的超限请求体 413，未配置数据库时超限请求体仍 413；兜底行缺失与合法马来西亚州属行缺失 503 且不含内部细节；计价只发 SELECT、不设 cookie、库存不变、GET 为 405。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；查询未在真 MySQL 上执行（CI 的 MySQL 只用于迁移检查）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-010 订单数据模型、迁移与状态迁移规则

- [x] 按 `docs/DESIGN.md` 1.9（提交 `361d8bf`）「数据模型」的 Order / OrderItem、OrderRecipient、PaymentAttempt / OrderEvent 三行及「订单与退款状态」「失败、并发与重试」「权限与资料保护」「资料保留」建立订单相关六张表：`app/models/order.py`（`app/models/__init__.py` 导出）、迁移 `alembic/versions/20260930_0006_orders.py`（revision `0006`，down_revision `0005`）、纯函数 `app/services/order_rules.py`；测试见 `tests/test_order_models.py` 与 `tests/test_order_rules.py`
- 六张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；金额一律 Integer 仙，积分一律 Integer 积分，1 积分抵 1 仙；时间一律不带时区的 UTC；检查约束只用比较、算术、LENGTH、LIKE、IN / NOT IN 与 IS NULL；订单及其子表的外键一律 ON DELETE RESTRICT）：
  - `orders`：`order_number` String(16)（唯一 `uq_orders_order_number`）、`status` String(32)、`subtotal_sen`、`coupon_discount_sen`、`points_redeemed`（积分数）、`shipping_fee_sen`、`total_sen`、`points_earned`、`shipping_zone_code` String(5)、`shipping_rate_version`、`idempotency_key` String(64)（唯一 `uq_orders_idempotency_key`）、`request_fingerprint` String(64)、`created_at`、`payment_expires_at`（非空）、`paid_at`（可空），除 `paid_at` 外全部非空。检查约束：
    - `ck_orders_order_number_length`：`LENGTH(order_number) = 16`
    - `ck_orders_status_valid`：`status IN ('awaiting_demo_payment', 'demo_paid', 'demo_packed', 'demo_shipped', 'demo_completed', 'demo_cancelled')`
    - `ck_orders_subtotal_sen_non_negative`、`ck_orders_coupon_discount_sen_non_negative`、`ck_orders_points_redeemed_non_negative`、`ck_orders_shipping_fee_sen_non_negative`、`ck_orders_total_sen_non_negative`、`ck_orders_points_earned_non_negative`：各列 `>= 0`
    - `ck_orders_discounts_within_subtotal`：`coupon_discount_sen + points_redeemed <= subtotal_sen`
    - `ck_orders_total_formula`：`total_sen = subtotal_sen - coupon_discount_sen - points_redeemed + shipping_fee_sen`
    - `ck_orders_total_covers_shipping`：`total_sen >= shipping_fee_sen`
    - `ck_orders_paid_status_has_paid_at`：`status NOT IN ('demo_paid', 'demo_packed', 'demo_shipped', 'demo_completed') OR paid_at IS NOT NULL`
    - `ck_orders_unpaid_status_no_paid_at`：`status NOT IN ('awaiting_demo_payment', 'demo_cancelled') OR paid_at IS NULL`
    - `ck_orders_idempotency_key_not_empty`：`LENGTH(idempotency_key) >= 1`
    - `ck_orders_request_fingerprint_length`：`LENGTH(request_fingerprint) = 64`
  - `order_items`：`order_id` → `orders.id`（RESTRICT）、`line_index`（同一订单内唯一 `uq_order_items_order_id_line_index`）、`variant_id` → `product_variants.id`（可空，ON DELETE SET NULL）、`sku` String(64)、`product_name_en/zh/ms` String(200)、`variant_label_en/zh/ms` String(300)（均非空，下单时已按回退英文取好，无规格商品存空串）、`unit_price_sen`、`quantity`、`line_subtotal_sen`。检查约束：`ck_order_items_line_index_non_negative`（`line_index >= 0`）、`ck_order_items_unit_price_sen_non_negative`（`unit_price_sen >= 0`）、`ck_order_items_quantity_positive`（`quantity >= 1`）、`ck_order_items_line_subtotal_formula`（`line_subtotal_sen = unit_price_sen * quantity`）。
  - `order_item_units`（逐件分摊快照，对应 `UnitAllocation`：行序由所属订单行给出，`unit_index`、`original_price` → `original_price_sen`、`coupon_discount` → `coupon_discount_sen`、`points_discount`、`cash_paid` → `cash_paid_sen`、`points_earned`）：`order_item_id` → `order_items.id`（RESTRICT），`uq_order_item_units_order_item_id_unit_index`。检查约束：`ck_order_item_units_unit_index_non_negative`、`ck_order_item_units_original_price_sen_non_negative`、`ck_order_item_units_coupon_discount_sen_non_negative`、`ck_order_item_units_points_discount_non_negative`、`ck_order_item_units_cash_paid_sen_non_negative`、`ck_order_item_units_points_earned_non_negative`（各列 `>= 0`）、`ck_order_item_units_cash_paid_formula`（`cash_paid_sen = original_price_sen - coupon_discount_sen - points_discount`）。
  - `order_recipients`：`order_id` → `orders.id`（RESTRICT，唯一 `uq_order_recipients_order_id`，每张订单恰好一条）、`name` String(200)、`phone` String(16)（普通索引 `ix_order_recipients_phone`）、`country_code` String(2)、`region` String(100)（可空）、`address` String(500)、`postal_code` String(20)；除 `region` 外全部非空，不加其他个人资料字段，不加密、不设到期删除或匿名化。检查约束：`ck_order_recipients_phone_format`（`phone LIKE '+%' AND LENGTH(phone) <= 16`）、`ck_order_recipients_country_code_length`（`LENGTH(country_code) = 2`）、`ck_order_recipients_region_matches_country`（`country_code <> 'MY' OR (region IS NOT NULL AND LENGTH(region) = 5 AND region LIKE 'MY-%')`）。
  - `payment_attempts`：`order_id` → `orders.id`（RESTRICT）、`method` String(20)、`result` String(10)、`idempotency_key` String(64)（唯一 `uq_payment_attempts_idempotency_key`）、`request_fingerprint` String(64)、`created_at`，全部非空；不存卡号或账户资料。检查约束：`ck_payment_attempts_method_valid`（`method IN ('demo_card', 'demo_bank', 'demo_ewallet')`，对应 `pay.method_card`、`pay.method_bank`、`pay.method_ewallet`）、`ck_payment_attempts_result_valid`（`result IN ('succeeded', 'failed')`）、`ck_payment_attempts_idempotency_key_not_empty`、`ck_payment_attempts_request_fingerprint_length`（同订单表）。
  - `order_events`：`order_id` → `orders.id`（RESTRICT）、`from_status`（可空）、`to_status`、`actor_type` String(10)、`created_at`，没有任何自由文本列。检查约束：`ck_order_events_from_status_valid`（`from_status IS NULL OR from_status IN (六种状态)`）、`ck_order_events_to_status_valid`（`to_status IN (六种状态)`）、`ck_order_events_placement_has_no_from_status`（`(from_status IS NULL AND to_status = 'awaiting_demo_payment') OR (from_status IS NOT NULL AND to_status <> 'awaiting_demo_payment')`）、`ck_order_events_actor_type_valid`（`actor_type IN ('guest', 'member', 'admin', 'system')`）。
  - 0006 的 upgrade 依次建 `orders`、`order_items`、`order_item_units`、`order_recipients`（及电话索引）、`payment_attempts`、`order_events`；downgrade 按依赖倒序删除。
- 订单号格式：`secrets.randbits(80)` 编码为 16 位 Crockford Base32 大写字符（字母表 `0123456789ABCDEFGHJKMNPQRSTVWXYZ`，不含 I、L、O、U），不含分隔符；`generate_order_number()` 生成，`is_valid_order_number(value)` 只接受恰好 16 位且每位都在字母表里的字符串，不做任何规范化。显示分组与查单输入规范化不在本任务。
- 状态迁移表（`is_transition_allowed(current, target, actor)`，其余一律拒绝，包括原地迁移、倒退、跨级、已支付订单取消、未知状态或操作者）：
  - `awaiting_demo_payment` → `demo_paid`：guest、member
  - `awaiting_demo_payment` → `demo_cancelled`：guest、member、system
  - `demo_paid` → `demo_packed`：admin
  - `demo_packed` → `demo_shipped`：admin
  - `demo_shipped` → `demo_completed`：guest、member、system
  - 模拟支付失败不是状态迁移，只记一条 `result = 'failed'` 的支付尝试；全部退款后冻结履约由之后的退款任务扩展，本任务不预留参数。
- 偏离：未改设计。说明几处取舍，请审阅：
  - 设计闸门写的是 DESIGN 1.9（提交 `361d8bf`）；仓库里现为 1.11 候批稿（1.10 已批准）。1.10、1.11 只改了 `Product` / `Variant` 一行、新增 `SiteSetting` 一行及短信验证开关相关段落，本任务用到的 Order / OrderItem、OrderRecipient、PaymentAttempt / OrderEvent 三行与「订单与退款状态」「资料保留」各版相同，未发现冲突。
  - 设计的 Order 写有「可选会员 ID」，按验收标准本任务不加，由会员任务另加；券 ID 与参考外币快照同样不加。
  - 验收标准之外另加的约束：订单号长度 16、收货国家代码长 2、行序与件序不小于 0、事件前后状态限于六种、下单事件（且只有下单事件）的迁移前状态为空、支付结果限于两种。它们都由设计原句直接推出，不限制设计允许的数据。
  - 幂等键在库里只保证非空（`LENGTH >= 1`），不超过 64 个字符由 String(64) 列长保证：MySQL 的 LENGTH 按字节计，写 `<= 64` 会把 64 个多字节字符误拒。SQLite 不检查列长。请求指纹库里只保证长 64，是否全为十六进制（SHA-256）由之后的下单与支付任务写入时校验；指纹怎样从请求算出、同键同请求返回原结果、同键不同内容报冲突，也由它们在事务内按键查询、比对指纹实现，唯一约束处理并发。
  - 应付不低于运费、券折扣加积分抵扣不超过商品小计、商品小计与应付不小于零这几条是其他约束的推论：违反它们的数据必然同时违反另一条约束。按验收标准仍逐条写出。
  - MySQL 默认排序规则不区分大小写：状态、操作者类别、支付方式与 `MY` 的比较在 MySQL 上不区分大小写（如 `'DEMO_PAID'` 能通过检查约束），大小写由写入方保证；状态迁移判定函数区分大小写。
  - 整单金额与逐件分摊合计一致（各件原价之和等于商品小计、分摊券额与积分额之和等于整单券折扣与积分抵扣、逐件获得积分之和等于整单获得积分）跨行，不能用单行检查约束表达，由之后的下单任务写入时保证。
  - 邮编非空，没有邮编的国家由写入方存空串；规格说明对无规格商品存空串。电话是否为合法 E.164 由之后的下单接口按 DESIGN 1.9 校验，本任务不固化任何电话解析规则。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并逐个核对 0006 的列类型、可空性、约束名与表达式、外键删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_order_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，先写入设计第 4 条算例的整张订单及其各子表作对照，再覆盖：每个检查约束至少一个反例（先在独立连接上对该行逐条求值全部检查约束、断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一），含应付不等于公式、应付低于运费、折扣超过小计、各金额与积分为负、未知状态、待支付或已取消却有支付时间、已支付或已发货却无支付时间、订单号长度不对、幂等键与请求指纹为空、行小计不等于单价乘件数、件数 0、现金实付不等于公式、马来西亚的地区为空或不是州属代码、电话不以加号开头或超过 16 个字符、未知支付方式与结果、未知操作者类别与状态、下单事件带迁移前状态；支付到期时间、幂等键与请求指纹为 NULL 被拒；重复订单号、订单与支付的重复幂等键、同一订单两条收货资料、同一订单内重复行序、同一订单行内重复件序被唯一约束拒绝；其他国家的地区为空或自由文本可写入；删除有订单行、收货资料、支付尝试或事件的订单与删除有逐件分摊的订单行被外键拒绝，子行引用不存在的父行被拒；删除被订单行引用的 SKU 规格后订单行保留、规格外键为空、快照不变。`tests/test_order_rules.py` 覆盖字母表、订单号长度与字符集、不含 I、L、O、U 与分隔符、20000 个不重复、每一位取遍字母表、格式校验接受合法与拒绝小写、分隔符、长度不对、禁用字符、全角与非字符串；五条迁移逐一允许其操作者，六种状态 × 六种状态 × 四种操作者的其余组合穷举被拒，另逐一点名原地、倒退、跨级、已支付取消与错误操作者，以及未知状态与大小写不同的值。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

### SHOP-TASK-011 手机号 E.164 规范化与短信白名单判定

- [x] 按 `docs/DESIGN.md` 1.9（提交 `361d8bf`）「权限与资料保护」第 1、2 条与「失败、并发与重试」的「初期短信国家」提供两个纯函数：`app/services/phone.py`，测试见 `tests/test_phone.py`
- 函数签名：
  - `normalize_phone(raw: object, default_region: object) -> NormalizedPhone`：`NormalizedPhone` 是 `NamedTuple(e164: str, country_code: int)`，如 `("+60123456789", 60)`。`default_region` 是两位大写 ISO 3166-1 代码，须在 `phonenumbers.SUPPORTED_REGIONS` 里。两种调用（docstring 写明）：结账第一步传页面国家码选择对应的地区（页面默认 `MY`）；会员改填收货电话传收货国家。输入以加号开头时以输入的国家码解析（`phonenumbers.parse(text, None)`），忽略默认地区；否则按默认地区解析，马来西亚本地写法带或不带前导 0 都可以。
  - `is_sms_whitelisted(e164: object) -> bool`：只接收号码、不接收地区，国家呼叫码在模块常量 `SMS_WHITELIST_COUNTRY_CODES = frozenset({60, 65})`（马来西亚、新加坡）里为真，其余为假。
  - 拒绝一律抛 `InvalidPhoneNumber`（`ValueError` 子类），消息固定为 `invalid phone number`，不含输入原文；解析异常以 `from None` 抛出，不串联 phonenumbers 的异常。
- 拒绝规则（`normalize_phone`）：
  - 默认地区不是字符串、不是两位大写 ASCII 字母（不转换大小写）或 phonenumbers 不支持（如 `ZZ`、`XX`）；输入以加号开头时也照样校验。
  - 输入不是字符串；`strip()` 去掉首尾空白后为空或超过 32 个字符；含 ASCII 数字、空格、连字符、点、括号与开头的一个加号以外的任何字符（字母、`/ # * , ; _`、制表与不换行空格、非开头或重复的加号、全角加号与全角或其他文字的数字）。
  - phonenumbers 解析失败；解析出分机（字符限制下不应出现，作为兜底）。
  - `is_possible_number_with_reason` 的结果不是 `IS_POSSIBLE`：`IS_POSSIBLE_LOCAL_ONLY`（缺区号、只能本地拨打）、`TOO_SHORT`、`TOO_LONG`、`INVALID_LENGTH`、`INVALID_COUNTRY_CODE` 一律拒绝。不调用 `is_valid_number`，不检查号码起始数字或前缀是否已分配。
  - 格式化后的 E.164 超过 15 位数字。
- 白名单判定的输入规则：须是加号加 ASCII 数字、不超过 15 位数字，且按上面同样的可能性规则重新解析后格式化结果与输入逐字相同（即 `normalize_phone` 的产出）；带空格或分隔符、首尾空白、无加号、国家码后带前导 0（如 `+600123456789`）、长度不可能、未知国家码与非字符串一律抛 `InvalidPhoneNumber`。
- 偏离：未改设计。说明几处取舍，请审阅：
  - 设计闸门写的是 DESIGN 1.9（提交 `361d8bf`）；仓库里现为 1.11 候批稿。1.10、1.11 对「权限与资料保护」第 1、2 条只加了短信验证开关的适用条件，规范化与白名单判定的规则各版相同，未发现冲突。开关本身不在本任务，由调用方按开关决定是否使用白名单判定。
  - 验收标准之外另加一条拒绝：phonenumbers 对部分国家（如德国，本国号码最长 15 位）认为长度可能、但加上国家码超过 E.164 的 15 位上限的号码被拒。依据是设计要求「规范化为 E.164」，E.164 号码最多 15 位数字；`order_recipients.phone` 的检查约束也只容得下 16 个字符。它只多拒绝、不放宽 IS_POSSIBLE 规则。
  - 不以加号开头的输入完全按默认地区的拨号规则解析，包括该地区的国际冠码：默认地区为马来西亚时 `00 65 8123 4567` 会解析为 `+6581234567`。设计只规定「访客明确输入 `+` 国家码时以输入为准」，未提国际冠码；按默认地区解析即按该地区的拨号习惯，本任务不另加禁止规则。
  - 同理，加号开头的号码在国家码后带本国前缀（如 `+60 012 345 6789`）时，phonenumbers 会去掉前缀，得到 `+60123456789`。
  - 白名单判定在非 E.164 输入上抛异常，而不是返回假：返回假会让调用方把格式错误的马新号码当作白名单外号码放行游客下单。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查。`tests/test_phone.py` 覆盖：马来西亚本地写法（带或不带前导 0、空格、连字符、点、括号、首尾空白）与带加号写法规范化为同一 `+60123456789`；新加坡本地与带加号写法；默认地区为马来西亚时加号开头的新加坡号码按新加坡解析，加号开头的英国号码在五个默认地区下结果相同；英国、美国、日本、泰国号码；两个可能成立但 phonenumbers 判为无效的号码（`+65 2123 4567`、美国 `(212) 155-0123`）被接受，测试先断言它们 `is_valid_number` 为假、可能性为 `IS_POSSIBLE`；默认地区为美国时的 7 位号码被拒，测试先断言它的可能性原因是 `IS_POSSIBLE_LOCAL_ONLY` 且 `is_possible_number` 为真；超过 15 位数字的德国号码被拒，测试先断言 phonenumbers 判它 `IS_POSSIBLE`；过短、过长、含字母、含非法字符、空串、只有空白、恰好 32 个字符接受而 33 个字符被拒、非字符串输入、非法或不支持的默认地区都被拒，且异常是同一个 `ValueError` 子类、消息相同、不含输入原文与其中的数字串、不串联其他异常；白名单常量恰好为 60 与 65，马来西亚、新加坡为真，印尼、泰国、文莱、英国、美国、日本、澳大利亚为假，判定函数只有号码一个参数、同一号码按不同默认地区规范化后判定相同，非 E.164 输入被拒。每条测试的文档字符串写明它守住的设计原句。测试里依赖 phonenumbers 元数据的前提（无效、只能本地拨打、德国 15 位可能）都先在测试里断言，元数据变化时会在前提处失败。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

### SHOP-TASK-012 会员、会话、短信验证记录与每日预算的数据模型

- [x] 按 `docs/DESIGN.md` 1.9（提交 `361d8bf`）「数据模型」的 Member / VerificationAttempt 一行及「失败、并发与重试」「权限与资料保护」「资料保留」建立四张表，并给订单表加会员 ID 与认领状态：`app/models/member.py`（`app/models/__init__.py` 导出 `Member`、`MemberSession`、`VerificationAttempt`、`SmsDailyUsage`）、`app/models/order.py`、迁移 `alembic/versions/20260930_0007_members.py`（revision `0007`，down_revision `0006`）；测试见 `tests/test_member_models.py`
- 四张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；时间一律不带时区的 UTC；检查约束只用比较、LENGTH、LIKE、IN / NOT IN 与 IS NULL；指向会员的外键一律 ON DELETE RESTRICT，会员行不物理删除）：
  - `members`：`phone` String(16)（可空，唯一 `uq_members_phone`，多个 NULL 可共存）、`password_hash` String(255)（可空）、`status` String(10)、`created_at`、`deleted_at`（可空）；只有这几列，不存其他个人资料。检查约束：
    - `ck_members_status_valid`：`status IN ('active', 'deleted')`
    - `ck_members_active_has_phone`：`status <> 'active' OR (phone IS NOT NULL AND deleted_at IS NULL)`
    - `ck_members_deleted_cleared`：`status <> 'deleted' OR (phone IS NULL AND password_hash IS NULL AND deleted_at IS NOT NULL)`
    - `ck_members_phone_format`：`phone IS NULL OR (phone LIKE '+%' AND LENGTH(phone) <= 16)`
  - `member_sessions`：`member_id` → `members.id`（RESTRICT）、`token_hash` String(64)（会话令牌的 SHA-256 十六进制摘要，唯一 `uq_member_sessions_token_hash`，令牌原文不入库）、`created_at`、`expires_at`（非空）、`revoked_at`（可空）。检查约束：`ck_member_sessions_token_hash_length`（`LENGTH(token_hash) = 64`）、`ck_member_sessions_expires_after_created`（`expires_at > created_at`）。
  - `verification_attempts`：`phone` String(16)、`purpose` String(20)、`status` String(16)、`provider_request_id` String(64)（可空，唯一 `uq_verification_attempts_provider_request_id`）、`created_at`、`updated_at`；普通索引 `ix_verification_attempts_phone_created_at`（`phone`, `created_at`）；不存验证码、IP 或其他资料。检查约束：
    - `ck_verification_attempts_phone_format`：`phone LIKE '+%' AND LENGTH(phone) <= 16`
    - `ck_verification_attempts_purpose_valid`：`purpose IN ('checkout', 'register', 'login', 'reset_password', 'delete_account')`
    - `ck_verification_attempts_status_valid`：`status IN ('sent', 'approved', 'rejected', 'undeliverable', 'suspended')`
    - `ck_verification_attempts_accepted_has_request_id`：`status NOT IN ('sent', 'approved', 'rejected') OR provider_request_id IS NOT NULL`
    - `ck_verification_attempts_suspended_no_request_id`：`status <> 'suspended' OR provider_request_id IS NULL`
    - `undeliverable` 的请求 ID 可有可无（提供方受理后无法送达时有，受理前即失败时没有）。
  - `sms_daily_usage`：`usage_date` Date（按马来西亚时间切日，唯一 `uq_sms_daily_usage_usage_date`）、`sent_count` Integer、`reserved_micro_usd` BigInteger、`settled_micro_usd` BigInteger（整数微美元，不用浮点或 Decimal）。检查约束：`ck_sms_daily_usage_sent_count_non_negative`、`ck_sms_daily_usage_reserved_micro_usd_non_negative`、`ck_sms_daily_usage_settled_micro_usd_non_negative`（各列 `>= 0`）。
  - 订单表新列：`member_id` Integer（可空，→ `members.id`，外键 `fk_orders_member_id_members` RESTRICT，普通索引 `ix_orders_member_id`）、`claim_status` String(16)（非空，Python 默认值 `open`）。检查约束：`ck_orders_claim_status_valid`（`claim_status IN ('open', 'claimed', 'not_claimable')`）、`ck_orders_member_claim_not_open`（`member_id IS NULL OR claim_status <> 'open'`）。订单表其他列与约束未改。
  - 除会员手机号、密码哈希、注销时间，会话撤销时间，验证记录的提供方请求 ID 与订单会员 ID 外，所有列非空。
  - 0007 的 upgrade 依次建 `members`、`member_sessions`、`verification_attempts`（及索引）、`sms_daily_usage`，再给 `orders` 加两列（认领状态先以服务端默认值 `open` 补齐已有行，再去掉默认值）、索引、外键与两条检查约束；downgrade 按依赖倒序撤销：先删两条检查约束、外键、索引与两列，再删四张表。
- 偏离：未改设计。说明几处取舍，请审阅：
  - 设计闸门写的是 DESIGN 1.9（提交 `361d8bf`）；仓库里现为 1.11 候批稿。1.10 只改 `Product` / `Variant` 一行；1.11 加短信验证开关（开关关闭时被拒的发送请求不写验证记录、注销可改以密码或会话确认），不改本任务各表的字段与约束，未发现冲突。开关本身（`SiteSetting`）不在本任务。
  - 订单表的两条认领状态检查约束在模型里挂在 `claim_status` 列上，而不是 `__table_args__`：`tests/test_order_models.py`（不在本任务可改范围）对 `Order.__table__.constraints` 的每条检查约束只用已有各列求值，表级约束引用新列会让该文件的对照测试报错。SQLite 允许列上的检查约束引用其他列，按模型建表照常生效；迁移在 MySQL 上用 `ALTER TABLE` 加成同名、同表达式的表级约束。代价是不能在 MySQL 上按模型 `create_all`（MySQL 不允许列上的检查约束引用其他列）；MySQL 的表一律只由迁移建。以后改 `tests/test_order_models.py` 的求值辅助函数时可把这两条移回 `__table_args__`，不改库。
  - 认领状态的默认值只在 Python 侧（`open`，即游客订单），迁移补齐已有行后去掉服务端默认值，与模型一致；会员下单忘写 `not_claimable` 时 `ck_orders_member_claim_not_open` 拒绝。
  - 「限流元数据」：设计的 VerificationAttempt 写有「限流元数据」，按验收标准只存号码、用途、状态与时间，不存 IP 或来源；按来源限流的短时计数在 Redis，由之后的短信服务任务实现。
  - 验收标准之外另加的约束：会话摘要长 64（`ck_member_sessions_token_hash_length`）。它由「SHA-256 十六进制摘要（64 个字符）」直接推出。
  - MySQL 默认排序规则不区分大小写、LENGTH 按字节计：状态、用途、认领状态的大小写，以及会话摘要为小写十六进制，都由写入方保证；手机号是否为合法 E.164 由写入方按 `app/services/phone.py` 校验，库里只保证以加号开头且不超过 16 个字符。
  - 每日用量的费用用 BigInteger：Integer 上限约 2147 美元，BigInteger 不会因预算调高而溢出。
- 待办：
  - 验证记录按设计「短信验证请求及发送记录短期保留用于防滥用」，本任务不做到期删除；删除由之后单独登记的任务（规划块的「短信验证记录到期删除」）在短信正式上线前完成。
  - 密码哈希、会话签发与到期时长、令牌生成与 cookie、短信发送与限流、每日预算的原子预占与结算、游客订单认领与注销都由之后的任务实现。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并逐个核对 0007 的列类型、可空性、约束名与表达式、外键删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_member_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，先写入无密码与有密码的有效会员、已注销会员、两条会话、七条各状态的验证记录（含两条无请求 ID 的停发记录）、两天的每日用量（含全零），以及游客订单、会员订单、已认领订单与注销后会员 ID 已清空的订单作对照；再覆盖：每个检查约束至少一个反例（先在独立连接上对该行逐条求值该表全部检查约束，含挂在列上的，断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一），含未知会员状态、有效会员无手机号或有注销时间、已注销会员仍有手机号或密码哈希或无注销时间、手机号不以加号开头或超过 16 个字符、会话到期等于或早于创建、摘要不是 64 个字符、未知用途与状态、sent / approved / rejected 无请求 ID、suspended 有请求 ID、负的条数与两项费用、会员订单认领状态为 open、未知认领状态；设计指定可空之外的每一列写入 NULL 被非空约束拒绝；重复手机号、重复会话摘要、重复请求 ID、重复日期被唯一约束拒绝，多个已注销会员手机号都为空可共存；会话与订单引用不存在的会员、删除被订单或会话引用的会员被外键拒绝；不写认领状态的订单为 open，清空会员 ID 后认领标记保留；各表只有设计指定的列可空、列集合不含验证码、IP、令牌原文或其他个人资料，指向会员的外键都是 RESTRICT，手机号与创建时间索引、订单会员 ID 索引存在。每条测试写明它守住的设计原句。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

### SHOP-TASK-013 商品目录接口补充首页与列表页所需字段

- [x] 按 `docs/REQUIREMENTS.md` 1.10「访客与会员流程」第 1 条与 `docs/UX.md` 0.5 的 P01、P02，在 SHOP-TASK-005 的目录接口上补三处只读数据：`app/services/catalog.py`、`app/api/catalog.py`；测试见 `tests/test_catalog_page_fields.py`，`tests/test_catalog_api.py` 只在两处整体相等断言里补上新字段。设计闸门：不适用（只增加商品展示用的只读字段，不改金额计算，不涉及个人资料）。
- 新增字段与接口：
  - `GET /api/catalog/products` 每项新增 `has_multiple_variants`：该商品启用的 SKU 多于一个时为真（即使各 SKU 同价），只有一个启用 SKU 时为假，停用的 SKU 不计入；用一个与最低单价同样关联到商品的 `COUNT` 子查询求得。其余字段、筛选、排序与分页不变；商品详情不变。
  - `GET /api/catalog/categories` 每项新增 `image`：该分类下按商品列表 `newest` 排序（创建时间降序，平手按商品 id 升序）排在第一的已发布商品（沿用 `published()`）的首张图片引用，取法与商品摘要的 `image` 相同（排列序号最小的一张）；该分类没有已发布商品，或排第一的商品没有图片时为 null（不往后找下一件）。分类列表改用新的 `CategoryListItem`（`slug`、`name`、`image`）；嵌在商品摘要与详情里的分类仍是 `CategorySummary`，不带 `image`。分类列表的过滤条件（启用且有英文名称）与顺序（按 id）不变。
  - 新增 `GET /api/catalog/options?lang=`：`lang` 同其他目录接口（只接受 `en`、`zh`、`ms`，默认 `en`，其他值 422）。返回规格名列表，每项 `code`、`name`（`{text, english_fallback}`）与 `values`（每项 `code`、`name`），形状与商品详情的 `options` 相同（复用 `OptionDetail` / `OptionValueDetail`）；不返回库存、启用状态、排列序号等后台字段。没有任何可筛选规格时返回 `[]`。
- 聚合与排序规则（规格筛选项）：
  - 只统计已发布商品的启用 SKU 实际用到的规格值（经 `variant_option_values` 关联到启用 SKU）；未发布商品、只被停用 SKU 用到或没被任何 SKU 用到的规格值不出现，没有一个值被统计的规格名也不出现。
  - 规格名按 `code` 聚合，同一规格名下按规格值 `code` 去重；同一 code 在不同商品上名称不同时，取商品 id 最小的那件商品上的名称；名称按请求语言给出，缺少时回退英文并标 `english_fallback`（同 SHOP-TASK-005）。
  - 规格名按它在各商品中最小的排列序号、再按 `code` 排序；规格值在所属规格名下同理。
  - 返回的 code 可原样写成商品列表的 `option=<规格名 code>:<规格值 code>`；两者都按 code 跨商品匹配。
- 偏离：未改设计、需求与审阅稿，未加表或迁移，未改发布规则、前端代码、CI 或部署配置；对照 `docs/DESIGN.md` 1.9（仓库现为 1.11 候批稿；1.10、1.11 对「数据模型」`Product` / `Variant` 一行只加了每单限购，与本任务无关）未发现冲突：分类图片取自已有的商品图片引用，不给 `categories` 加列。SHOP-TASK-005 记录的两处出入（P02 规格筛选栏、P01 分类卡片图片）由本任务补齐。说明几处取舍，请审阅：
  - 「最小排列序号」与「id 最小的商品」都只在被统计的行里取：某商品的规格名只挂着未被启用 SKU 用到的值、或商品未发布时，它的排列序号与名称不参与。验收标准未细分这一点，按「只统计……实际用到的规格值」的同一范围处理。
  - 规格筛选项是全目录的静态列表，不随当前的搜索、分类或其他筛选条件收窄，也不给每个值的商品数；P02 线框未要求这两项。
  - `has_multiple_variants` 只数启用 SKU，与商品有没有规格名无关：没有规格名却有两个启用 SKU 的商品同样为真。
  - 规格筛选项的外层查询对规格名、规格值、SKU 用别名：`published()` 里「所有规格名与规格值的英文名称齐全」的子查询会自动关联外层同名的表，不用别名就只检查当前一行。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_catalog_page_fields.py` 用 TestClient 与 SQLite 内存库（StaticPool，覆盖 `get_session`）按模型建表并自建数据，每条测试的文档字符串写明它守住的规则，覆盖：`has_multiple_variants` 在一个启用 SKU、两个同价启用 SKU、两个 SKU 其中一个停用三种情况下分别为假、真、假；分类图片取最新已发布商品排列序号最小的图片，更新的未发布商品不参与，创建时间相同按商品 id，最新已发布商品没有图片时为 null 而不取较旧商品的图片，只有未发布商品与没有商品的分类为 null，停用分类不出现，并对照商品列表 newest 的结果；分类列表带图时商品摘要与详情里的分类仍只有 `slug`、`name`；规格筛选项整体相等，不含停用 SKU、未被用到、未发布商品的值与只挂未用值的规格名；商品因另一个规格名缺英文名称而不发布时其值不出现、补上后出现；跨商品按 code 聚合去重，名称取 id 较小的商品（较新的商品 id 较大，排除按 newest 取名）；规格名与规格值按最小排列序号再按 code 排序（若只看先遇到的商品会得到不同顺序）；中文、马来文的回退英文与标记（含空串与只有空格），不带 `lang` 为英文；`fr`、`EN` 被拒 422；返回的每个 code 组成 `option` 参数筛出恰好对应的商品；空库与只有无规格商品时为 `[]`；三处不带凭据即 200、只发 SELECT、响应不设 cookie、写方法 405。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；查询未在真 MySQL 上执行（CI 的 MySQL 只用于迁移检查），分类图片用的是 SELECT 列表中带 ORDER BY 与 LIMIT 的关联标量子查询，MySQL 8 支持。检查命令结果由 Worker 另行记录。

### SHOP-TASK-014 前端基础：设计变量、三语字典、路由、全站框架与隐私说明页 P14

- [x] 按 `docs/UX.md` 0.7「全局框架」与 P14、`docs/UX-COPY.md` 0.6、`docs/design/`（`COMPONENTS.md` 与 `pages/P14-*.html`、`P01-*.html`）接入设计变量与组件样式，搭好三语字典、语言切换、路由与全站框架，实现隐私说明 P14；首页只有固定位置的 ★ `home.demo_hint`。不调用后端接口，不装依赖，未改 `package.json`、锁文件、`index.html`、`vite.config.ts`、CI 或部署配置。设计闸门：不适用（纯前端展示与静态文案，不处理金额，不收集、存储或展示个人资料）。
- 文件结构（全部在 `frontend/src/`）：
  - `styles/acuven-shop.css`：`docs/design/tokens/acuven-shop.css` 的逐字节副本（含开头的 `@font-face`），不手改；`styles/site.css`：全站布局与显隐，桌面为基础，手机差异只写在 `@media (max-width: 767px)` 里，不用 `acs--phone`，不写颜色、字体或圆角。`main.tsx` 先引入前者再引入后者。
  - `storeDesign.ts`：主题取值的唯一来源（`pandan`、`auto`，不设主色），注明之后由店铺装修任务改为读取 A08 设置。
  - `i18n/copy.ts`：文案字典 `COPY`（本任务用到的 30 个键，英、中、马三列从 UX-COPY 原样抄入）、`formatCopy`（`{变量}` 替换，缺变量抛错）、`translate`、语言列表与品牌字样常量 `BRAND`。
  - `i18n/language.tsx`：语言的读取、保存、`html lang` 设置与 `LanguageProvider` / `useLanguage` / `useCopy`。
  - `router.tsx`：路由表 `ROUTE_PATHS`、`RouterProvider` / `useRouter`、只接受表内路径的 `Link`，以及可单独测试的 `resolvePath`、`pushPath`、`canonicalizeLocation`、`settleLocation`、`isPlainLeftClick`。
  - `components/SiteFrame.tsx`：根元素（`class="acs site"`、`data-shop-theme`、`data-mode`）、演示横幅、页头（含手机 ☰ 菜单与当前语言下拉）、页脚、★ 提示组件 `DemoHint`。
  - `pages/HomePage.tsx`、`pages/PrivacyPage.tsx`；`App.tsx` 组合语言、路由、框架，并以按 `RoutePath` 穷举的页面表选页；`vite-env.d.ts` 引入 `vite/client` 类型。
- 路由规则：用 History 接口，不引入依赖。站内链接拦截普通左键点击、`pushState` 后换页，不整页刷新；带修饰键或非左键的点击交给浏览器；点当前页面的链接不重复记历史；`popstate` 时按地址栏换页，前进后退可用；每次换页（含前进后退与首次进入）后回到页面顶部。路由表只有 `/` 与 `/privacy`；未知路径（含 `/privacy/` 与大小写不同的写法）显示首页，并用 `replaceState` 把地址栏换成 `/`。`Link` 的目标类型就是路由表的路径，页头导航项（商品 `/products`、查询订单 `/track`、登录 `/login`）只在路径进了路由表后才渲染，现在都不渲染。路径与查询参数里不放订单号或电话，也不放语言。
- 语言规则：默认英文；页头语言选项（桌面三个链接，手机为当前语言按钮展开的三项，☰ 菜单里也有）指向当前页面本身，点击只切换语言并以 `acuven-shop.language` 为键写入本浏览器的 `localStorage`；取 `localStorage`、读、写任一步抛错都照常工作（读失败为英文，写失败只是不记住）；只接受 `en`、`zh`、`ms`，其他值（含 `ZH`、`zh-CN`、空串）回退英文。不读 `navigator.language` 或 IP，不写 cookie。`html` 的 `lang` 随当前语言设为 `en`、`zh-Hans`、`ms`（中文用 `zh-Hans`：文案是简体，读屏与字体回退按简体选）；语言选项链接各自带对应的 `lang` 属性。
- 字典校验方式：`i18n/copy.test.ts` 以 Vite 的 `?raw` 读入 `docs/UX-COPY.md`（不 import `node:fs`，不给 `tsconfig.app.json` 下的代码引入 Node 类型），把每行 `` | `键` | 英 | 中 | 马 | 提示 | `` 解析成表，断言字典里每个键在文档中恰好出现一次且三列逐字相同、三列非空且变量同名，并先断言解析出上百个键与已知一行（`common.nav_cart`）正确，防止解析失效时空转通过；变量替换用文档里 `common.nav_cart` 的三列真实模板验证，另测重复变量、数字值与缺变量抛错。`App.test.tsx` 把每个路由在三种语言下的服务端渲染结果拆成文本节点与 `aria-label` 等属性，断言每一段都是当前语言那一列的字典值或 `ACUVEN SHOP`。
- 样式校验方式：`styles/acuven-shop.test.ts` 按字节比较两份 `acuven-shop.css`。这里没有用 `?raw`：vitest 默认不处理 CSS，`.css?raw` 读到空字符串，两份空串比较也会“相同”；改为在测试里以运行时字符串动态取得 `node:fs`、只声明用到的 `readFileSync`，同样不引入 Node 类型，并先断言读到的设计稿含 `@font-face`。同一文件另断言 `main.tsx` 先引入 `acuven-shop.css` 再引入 `site.css`，`site.css`（去掉注释后）没有颜色值、颜色与字体与圆角属性、`acs--phone`，且有 767px 的媒体查询。
- 偏离与取舍，请审阅：
  - UX 与视觉稿冲突，按验收标准以 UX 为准：`docs/design/pages/P14-*.html` 仍是 0.6 以前的 `privacy.fictional`、`privacy.sms` 措辞且没有 `privacy.sms_toggle`，页面按 UX-COPY 0.6 的文案与 UX 0.7 线框（`privacy.sms_toggle` 在短信与日志段、`privacy.logs` 之后）实现，样式沿用相邻段落。
  - 视觉稿画了而本任务按验收标准不渲染的：页头搜索框（去 P02）、商品 / 查询订单 / 登录导航与购物车数量（P02、P08、P12、P04 尚未实现）、页脚与 P14 的 WhatsApp 按钮及 P14「联系」段（配置来源由之后单独登记的任务提供，等同 Q10 的配置缺失时隐藏）。因此字典没有收录 `privacy.h_contact`、`privacy.contact`、`privacy.contact_button`、`common.whatsapp_cta`、`common.nav_cart`、`list.search_placeholder`、`common.search`；`common.nav_shop`、`common.nav_track`、`common.nav_login` 已收录，供页头导航表引用，目前不显示。
  - 视觉稿里有、但 UX-COPY 没有对应键的读屏文字（语言切换的 `aria-label="Language"`、搜索框的 `aria-label`）没有渲染，不自行编写文案；需要时请在 UX-COPY 补键。
  - `site.css` 只放布局与显隐，视觉稿里几处行内的非布局样式没有搬过来：手机页头品牌字号（`calc(18px * var(--display-scale))`）、手机菜单与语言按钮的颜色、去下划线与字号字重、页脚品牌的 `color: var(--on-footer)`、页脚 Privacy 链接的字重、手机版 P14 引言不用 `acs-body-l`。页脚品牌因此用组件默认的 `--ink`：班兰主题浅色下与 `--on-footer` 同值、深色下相近；页脚底色深而 `--ink` 也深的其他主题会看不清，接入店铺装修时需要处理（由组件样式或允许 `site.css` 使用颜色变量）。
  - 手机当前语言下拉用按钮加展开的链接列表实现（`aria-expanded`、`aria-controls`），没有用原生 `select`，以便沿用 `.acs-lang` 的样式；☰ 菜单同理。两者都没有处理 Escape 键与点击外部关闭。
  - 演示横幅的桌面与手机两句都在页面里，由 `site.css` 按宽度只显示其一（`display: none` 的一句读屏也不读）。
  - 路由表的路径在 `router.tsx`，路径到页面组件的对应在 `App.tsx`（按 `RoutePath` 穷举，表里加路径不加页面或反之都过不了类型检查）：页面要用 `SiteFrame.tsx` 的 `DemoHint`，路由模块若再 import 页面会形成循环依赖。
  - 原外壳页测试（`App.test.tsx`「labels the site as a demo」）随外壳页一起替换，其意图（每页都标注演示）由新的 `App.test.tsx` 与 `SiteFrame.test.tsx` 守住。
  - 本机工作树把 `docs/design/tokens/acuven-shop.css` 检出为 CRLF（其他已有文件同样如此，推断是 autocrlf 检出、仓库里存 LF），新写的副本是 LF；提交后两份在仓库与 CI 的 Linux 检出里应同为 LF，逐字节比较以 CI 为准。若在本工作树提交前直接跑前端测试，这一条会因换行不同而失败。
- 验证到什么程度：人工逐条对照验收标准自查，并人工核对字典 30 个键与 UX-COPY 原文、两份 CSS 的行数与颜色值行数一致。测试（vitest 与 `react-dom/server`，未加测试依赖）每条写明它守住的 UX 或验收原句，覆盖：每个路由三种语言下演示横幅都在页头之前、含两句与 DEMO 标签、没有按钮链接等控件；根元素的主题属性、没有 `data-accent`、`acs--phone` 与行内样式；品牌链到首页；未实现页面的导航项与搜索表单不渲染，全部链接只指向路由表里的路径且不带查询参数；三种语言选项与当前语言标记、☰ 按钮的读屏标签、当前语言按钮、菜单里的隐私链接；页脚的演示说明与隐私链接，没有 WhatsApp、占位与表单；路由表恰好两项、未知路径解析与渲染为首页、`replaceState` 换地址、`pushState` 换页、同页不重复记历史、换页后回到顶部、只拦截普通左键；语言读取、保存、非法值回退英文、读写抛错、浏览器语言为中文时仍为英文、按保存的语言渲染、`html lang` 设置函数；P14 三种语言下全部文案按线框顺序出现、四个段落、没有「联系」段文案（从 UX-COPY 取原文反查）与 WhatsApp、正文没有链接按钮表单、没有折叠或隐藏；首页主体只有 ★ 提示且在页头之后；页面文字全部来自字典；字典与文档逐字一致与变量替换；CSS 逐字节相同与 `site.css` 的限制。浏览器相关的部分（实际点击、前进后退、滚动、`localStorage` 与 `document.documentElement.lang` 的真实读写、媒体查询下的显隐）只以可替换浏览器对象的函数做单元测试，effect 本身未在 DOM 中执行；不写 cookie 只经代码检查。lint、类型检查、测试、构建与镜像构建只由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，也未与参考图对比）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-015 首页区块 P01 与商品列表 P02

- [x] 在 SHOP-TASK-014 的框架上按 `docs/UX.md`（P01、P02）、`docs/UX-COPY.md` 与 `docs/design/pages/P01-*.html`、`P02-*.html` 实现首页四个区块与商品列表 P02，数据来自已有目录接口与 SHOP-TASK-013 补充的字段。价格只格式化服务端返回的整数仙；不做商品详情与购物车，不装依赖（未改 `package.json`、锁文件），不改后端、CI、部署配置或设计。设计闸门：不适用（只展示目录接口返回的价格，不改金额计算，不涉及个人资料）。
- 文件结构（全部在 `frontend/src/`）：
  - `format.ts`：`formatSen` 把整数仙写成两位小数（2190 → `21.90`），只做整数转十进制字符串与切分，不经浮点；非负安全整数以外的输入抛 `RangeError`。不分千位（UX-COPY「约定」只写两位小数）。
  - `api/catalog.ts`：目录接口的类型、请求地址（`categoriesUrl`、`optionsUrl`、`productsUrl`、`featuredProductsUrl`）、`fetchCatalog`（GET、`credentials: "omit"`、只带 `Accept` 头，非 2xx 抛错）与 `useCatalog` 钩子（按地址请求，地址变了就作废旧请求并重新请求；结果按地址记下，effect 里不同步设状态）。
  - `pages/productListQuery.ts`：P02 查询参数的解析与生成，以及清除筛选、换条件回第 1 页、转成接口参数等纯函数。
  - `components/ProductCard.tsx`：商品卡、目录图片（为空时占位形状）、价格文案规则 `priceCopy`。
  - `pages/HomePage.tsx`（`HomeView` 为可单独渲染的展示部分）、`pages/ProductListPage.tsx`（`ProductListView` 同上）。
  - `storeDesign.ts` 加首页区块设置 `homeBlocks`（`HOME_BLOCKS` 顺序，全部显示）；`router.tsx` 路由表加 `/products` 并支持查询串；`App.tsx` 页面表加商品列表；`components/SiteFrame.tsx` 加页头搜索框与请求失败提示 `ErrorNotice`；`i18n/copy.ts` 从 UX-COPY 原样抄入本任务新用到的 31 个键（`common.price_myr`、`common.error_retry`、`common.search`、`common.a11y_page_prev`、`common.a11y_page_next`、`home.*` 10 个、`list.*` 16 个）；`styles/site.css` 加 P01、P02 与页头搜索的布局。
- 页面结构：
  - P01：`<main>` 里先是 ★ `home.demo_hint`，其后按 `storeDesign.homeBlocks` 的顺序渲染显示中的区块，隐藏的整块不渲染。主视觉：`home.hero_title`、`home.hero_body`、链到 `/products` 的 `home.hero_cta`，右侧三个装饰图块（`aria-hidden`，不是链接）依次取前三件精选商品的图片，其中无图的商品保留位置显示占位形状（不补取第四件的图片），不足三件或未取到时也为占位形状。演示怎么玩：`<ol>` 四步，编号由 `site.css` 的 CSS 计数器生成，页面上不写数字文字。按分类浏览：分类图片（为空时占位形状）与名称，链到 `/products?category=<slug>`。精选商品：最新 4 件的商品卡。
  - P02：标题与 ★ `list.demo_hint`；桌面左侧筛选栏（`list.filter_title`、`list.filter_category` 下的分类、各规格名下的值、`list.filter_clear`），勾选即改网址；右侧结果数 `list.results_count`（接口给的总数）与排序（三项），商品卡网格，页码（上一页、最多 5 个页码、下一页，都是带查询参数的链接，上一页、下一页的读屏标签为 `common.a11y_page_prev`、`common.a11y_page_next`，不可用时为读屏忽略的占位）；空结果 `list.empty` 与 `list.filter_clear`。767px 以下：`list.filter_title` 按钮打开全屏筛选抽屉（`role="dialog"`、`aria-modal`，打开时聚焦、Esc 关闭并把焦点还给按钮；勾选只改抽屉里的草稿，`list.filter_apply` 才写进网址），`list.load_more` 追加下一页。两套控件都在页面里，由 `site.css` 按宽度只显示其一。
  - 商品卡：首张图片（`alt` 为空，旁边有名称）、名称、价格、当日售罄时 `list.out_of_stock` 并加 `acs-pcard--oos`。`has_multiple_variants` 为真时一律 `list.price_from`，否则 `common.price_myr`；不显示参考外币。卡片不是链接（商品详情不在路由表）。商品、分类、规格名称回退英文时给该文字加 `lang="en"`。
  - 页头：搜索框（`role="search"`，占位与读屏标签 `list.search_placeholder`，按钮 `common.search`，按钮只在桌面显示，手机回车提交）在品牌之后，手机上换到第二行；提交后跳到只带该搜索词的 `/products?q=…`，不用脚本时按普通 GET 表单提交到同一地址；在列表页上框里预先填入当前搜索词。`common.nav_shop` 因 `/products` 进了路由表而开始渲染（桌面导航与手机菜单）。
- 数据来源：分类 `GET /api/catalog/categories?lang=`；规格筛选项 `GET /api/catalog/options?lang=`；商品 `GET /api/catalog/products?lang=&q=&category=…&option=…&sort=&page=`（首页精选另带 `page_size=4`、`sort=newest`、`page=1`）。筛选、排序与分页全部交给接口，页面按返回顺序显示，不自行过滤或排序。请求只带语言与上述参数，不带 cookie 或任何个人资料。首页只为显示中的区块请求（主视觉图块与精选商品共用一个请求）。语言是请求地址的一部分，切换语言即按新语言重新请求，网址里的条件不变。请求失败时只在该区块（首页分类、精选商品）、列表位置、筛选栏或「加载更多」处显示 `common.error_retry`（`role="alert"`），页头页脚与其他区块照常；主视觉的装饰图块取不到时显示占位形状，不显示错误。
- 查询参数（`/products`）：参数名与接口相同——`q`（去首尾空白，最多 100 个字符，同接口上限）、可重复的 `category`（slug，非空、不超过 100 个字符、无首尾空白）、可重复的 `option`（`<规格名 code>:<规格值 code>`，两段都非空、各不超过 50 个字符、值里不再含冒号）、`sort`（`newest`、`price_asc`、`price_desc`）、`page`（不带前导零的正整数，最多 6 位）。非法的单项丢掉、其余照用，重复值只算一次，不认识的参数不读；生成时省略默认值（`newest`、第 1 页），全默认时没有查询串，同一组条件只有一种写法。换筛选或排序回到第 1 页。不放语言、订单号或电话。路由：已知路径保留查询串，未知路径连同查询串换成首页；只改查询串也各记一条历史（前进后退可用），同路径同查询串不重复记；只改查询串时不回到顶部，点页码时回到顶部。
- 偏离与取舍，请审阅：
  - UX 与视觉稿不一致处，按 UX 为准：视觉稿的商品卡是指向详情的链接，按路由规则在 P03 实现前不渲染链接；视觉稿主视觉三个图块是固定的示意图形，按验收标准改为精选商品的前三张图片；视觉稿 P01 桌面的分类块是带底色的横向卡片，这里沿用组件类 `acs-cat`（图块带底色，整块不带），只在布局上改为横排。
  - `site.css` 只放布局与显隐、不写颜色字体圆角（`acuven-shop.test.ts` 同样检查），视觉稿里几处行内的非布局样式没有搬过来：主视觉三个图块的 `--hero-shape` 圆角与三种底色（改用 `acs-cat__img`，统一为 `--radius-tile` 与 `--tile`）、「演示怎么玩」每步上方的 `--ink` 细线与编号的 `--accent-ink` 颜色、手机商品卡与分类名的 `acs-body-s` 字号（类名不能按宽度切换）、筛选勾选框的 `accent-color`、抽屉标题下与按钮栏上的分隔线和按钮栏底色。分类名在桌面用 `acs-display-s`、手机用普通字号，靠两个只显示其一的 `span` 实现（与演示横幅同样的做法）。全屏抽屉要盖住页面需要底色：给抽屉加了根元素的 `acs` 类（取 `--surface` 底色与正文字体），没有在 `site.css` 写底色。编号用 CSS 计数器的 `content`，属于生成内容而非颜色字体，请确认可接受。
  - 「清除筛选」的范围：UX 未写明，这里去掉搜索词、分类与规格，保留排序，立即生效（桌面侧栏、空结果与抽屉里的按钮相同；抽屉里按下后同时关闭抽屉）。理由是空结果文案 `list.empty` 说的是「没有符合搜索的商品」，只清分类与规格时搜索造成的空结果无法从这里恢复。
  - 页头搜索跳到只带搜索词的列表，不保留当前的分类、规格与排序（「跳到带搜索词的列表」按新的一次搜索处理）。
  - 手机「加载更多」追加的页只在内存里，不写进网址：写进 `page` 会让刷新或分享后只显示最后一页。因此手机上刷新后回到网址里的那一页；从带 `page` 的桌面网址在手机上打开时，从该页开始加载更多。
  - 精选商品：后台挑选尚未实现，取商品列表接口 `newest` 的前 4 件，即 UX P01「未挑选」时的规则，由店铺装修任务改为读取挑选。
  - UX-COPY「约定」说商品文案回退英文时显示「仅英文」标签 `detail.english_only`，但 P01、P02 线框与视觉稿的商品卡、分类与筛选项都没有这个标签；本任务未显示该标签，只给回退的文字加 `lang="en"`。是否在卡片上显示请决定。
  - UX-COPY 里没有、因而没有渲染的界面文字（不自行编写）：页码导航与筛选栏的整体读屏标签、抽屉的关闭按钮（抽屉只能经 `list.filter_apply`、`list.filter_clear` 或 Esc 关闭）、加载中的提示（加载中只给容器 `aria-busy`，不显示文字）。页头搜索框的读屏标签用的是与占位文字相同的 `list.search_placeholder`（视觉稿即如此）。
  - 已有测试的改动：`App.test.tsx` 只改首页那一条（改为验证默认设置下 ★ 提示在页头之后、四个区块之前，按顺序）；`i18n/language.test.tsx` 只改 `/privacy` 链接那一条（改为每个链接都在路由表里且不带查询参数）。另外两处因路由表加了 `/products` 必须调整，意图不变：`router.test.tsx` 的路由表断言改为三项、未知路径样例去掉 `/products`（加 `/products/`、`/products/tee`），「链接只指向路由表」那条允许商品列表的查询参数键（首页分类链接带 `?category=`）；`components/SiteFrame.test.tsx` 的「未实现页面不渲染导航项」去掉商品与搜索框（改由新测试验证它们出现），「没有联系表单」那条先去掉页头的 `role="search"` 表单再检查。其余测试未删减、未放宽。
  - `fetchCatalog` 对返回的 JSON 只做类型断言，不逐字段校验；字段由 SHOP-TASK-013 的接口测试守住。
- 验证到什么程度：人工逐条对照验收标准自查，并人工核对新增的 31 个字典键与 UX-COPY 原文（字典与文档的逐字比较由已有的 `i18n/copy.test.ts` 覆盖新键）。测试（vitest 与 `react-dom/server`，未加测试依赖，`fetch` 以 `vi.fn` 替身）每条注释写明依据：能对上 UX、UX-COPY、DESIGN 或验收原句的写出处（验收写成「SHOP-TASK-015 验收第 N 条」加短引文），没有直接需求原句的边界标为「派生实现约束（实现选择）」并写明它守住的上层条款，覆盖：金额格式化 0、5、100、2190、123456 仙与最大安全整数，非整数、负数、NaN、无穷、非安全整数被拒；三种语言下多规格一律「起」（数据为两个同价规格时接口标记为真）、单规格不带「起」，不显示参考外币；售罄标签与无图占位、卡片不是链接、回退英文的 `lang`；首页默认设置为四块全部显示、按设置顺序渲染（打乱顺序验证）、隐藏的不渲染、全部隐藏时 `<main>` 只剩 ★ 提示、★ 提示总在所有区块之前；主视觉 CTA 链到列表、装饰图块取前三件精选商品的图片（其中无图的保留位置显示占位形状，不取第四件）且不是链接、读屏忽略，未取到或失败时为三个占位形状且不显示错误；四步按顺序；分类链接带 `category`、分类图片为空时的占位；分类或精选请求失败时只在该区块显示 `common.error_retry`；精选按返回顺序；`App.test.tsx` 中默认首页 ★ 提示在页头之后、四个区块之前；查询参数生成与解析往返一致（含需转义的文字）、唯一写法、重复参数、各类非法值回退、合法部分保留、100 字符边界；分类与搜索链接、换条件回第 1 页、清除筛选保留排序、条件原样转成接口参数；请求地址与参数（语言、重复的 `category` 与 `option`、精选的 `page_size=4`）、请求为不带凭据的 GET、非 2xx 与网络错误被当作失败、传入取消信号；P02 标题与提示、结果数取接口总数、排序三项与当前项、按返回顺序显示、空结果文案与清除筛选、列表或筛选项失败时就地显示错误而其余照常、侧栏勾选状态来自网址、页码链接与读屏标签、首末页不可用、单页时无页码、抽屉默认关闭且有应用与清除、加载更多只在有下一页时出现、追加的页接在后面、追加失败时保留已显示商品；页头搜索表单、在列表页预填搜索词、`common.nav_shop` 出现并在列表页标为当前；路由表三项、查询串的保留与丢弃、只改查询串记历史、同查询串不重复记；`/products` 带查询串时渲染列表页。所有路由在三种语言下的页面文字仍只来自字典（已有测试，现含 `/products`）。浏览器相关的部分（实际请求与 effect、勾选与提交、抽屉的打开关闭与焦点、加载更多的追加、前进后退、媒体查询下的显隐与布局）未在 DOM 中执行，只以纯函数与服务端渲染的各状态验证。lint、类型检查、测试、构建与镜像构建只由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，也未与参考图对比）。检查命令结果由 Worker 另行记录。
- 最终审查后的补充（只改本任务测试注释与本段，不改实现）：逐条核对新增或修改的测试注释。原先写得像需求的若干边界没有直接需求原句，现标为「派生实现约束（实现选择）」，每条写明守住的上层条款：默认商品请求（newest、第 1 页；UX P02 搜索、排序与分页）、取消信号（验收第 6 条切换语言时按新语言重新请求，防止旧请求覆盖）、商品卡无图占位、主视觉图块未取到时的占位与无图不补位、列表页预填搜索词、首末页不可用、单页无页码（UX P02 桌面页码翻页）、加载到最后一页后隐藏、查询串唯一写法、非法单项丢弃与重复去重、换条件回第 1 页、清除筛选保留排序、`q` 的 100 字符边界（验收第 5 条搜索参数与第 8 条非法值回退的接口兼容约束，上限取自 SHOP-TASK-013 接口）、最大安全整数，其余各条的上层条款见测试注释。只写泛称「验收」的注释改为「SHOP-TASK-015 验收第 N 条」加短引文。补上出处：有货时无售罄标签（验收「售罄标签」与 UX P02 的反面）、重复 `category`/`option`（验收原句）、带查询串的 `/products` 渲染列表（验收「路径为 /products」）。上文「覆盖」所列各项按此理解，并非都是需求原句。本轮本地只做了 `git diff --check` 与逐条静态核对，前端测试仍只由 PR 的必需 CI 检查 frontend 执行。

### SHOP-TASK-016 每单限购：商品限购字段、迁移与计价接口校验

- [x] 按 `docs/DESIGN.md` 1.10（提交 `e3b3505`）「数据模型」的 Product / Variant 一行与「计价、优惠、积分与库存」第 7、8 条，给商品加每单限购件数并接入计价接口与商品详情：`app/models/catalog.py`、迁移 `alembic/versions/20261001_0008_purchase_limit.py`（revision `0008`，down_revision `0007`）、`app/services/catalog.py`、`app/services/checkout.py`、`app/api/checkout.py`（只改模块说明，件数上限取自服务层常量）；测试见 `tests/test_purchase_limit.py`，`tests/test_checkout_quote.py` 与 `tests/test_catalog_api.py` 按验收允许的范围调整。设计闸门：DESIGN 1.10（提交 `e3b3505`）。
- 新列与约束：`products.max_per_order` Integer，非空，服务端默认 `10`；检查约束 `ck_products_max_per_order_range`：`max_per_order >= 1 AND max_per_order <= 99`（只用比较与 AND，SQLite 与 MySQL 都支持）。只加在商品上，不按规格设置；其他列与约束未改。迁移 upgrade 先加列（已有商品由服务端默认值补成 10），再加检查约束；默认值保留，与模型一致；downgrade 先删检查约束再删列（MySQL 不许删除仍被检查约束引用的列）。`alembic check` 不比较检查约束与服务端默认值，这两项只经人工核对模型与迁移一致。
- 行状态与优先级（`POST /api/checkout/quote`）：
  - 每行件数上限由 10 改为 99（1 到 99 的整数，越界 422）；最多 20 行、SKU 不重复及其余请求校验不变。
  - 先把同一已发布商品的所有可购买行（正常或库存不足）的件数按商品合计；不可购买的行（SKU 不存在、停用或商品未发布）不计入任何商品的合计。合计超过该商品的 `max_per_order` 时，该商品的这些行都标为 `over_limit`，不拒绝整个请求。
  - 每行的状态按优先级取：`unavailable` → `over_limit` → `insufficient_stock` → `ok`。
  - 超出限购的行与库存不足的行一样返回商品信息、单价与行小计，并计入商品小计；`can_place_order` 仍要求全部行为 `ok` 且给了收货国家，所以有任何超出限购的行即不可下单。运费、合计与参考外币的计算不变。接口仍只发 SELECT、不需登录、不读写 cookie。
- 接口字段：
  - 计价接口：正常、库存不足、超出限购的行新增 `max_per_order`（该商品的每单限购件数）；`status` 新增取值 `over_limit`；不可购买的行仍只有 `sku`、`quantity`、`status`。
  - `GET /api/catalog/products/{slug}` 新增 `max_per_order`；商品列表与分类接口不变。
- 偏离：未改设计、需求、审阅稿、前端代码、CI 或部署配置；未发现设计本身的问题。说明几处取舍，请审阅：
  - `available_stock` 原先只在库存不足的行给出。按第 8 条「限购与库存各自独立生效」，现在凡是当日可用库存小于件数的行都给出可用库存，包括同时超出限购而标为 `over_limit` 的行；其余行仍为 null。页面据此可在用户减到限购之内前就提示缺货。
  - 下单时的整单校验（「下单时服务端重新校验，超出限购即拒绝整单，不部分下单」）留给游客下单任务（SHOP-TASK-020）；本任务不建订单、不预留库存。修改限购件数的后台接口（第 7 条只影响之后的计价与下单）留给管理后台业务接口任务。
  - 示例种子数据未改：已有的 30 件示例商品经迁移都取默认值 10。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并人工核对迁移中的列类型、可空性、服务端默认值、约束名与表达式同模型按命名约定生成的一致。`tests/test_purchase_limit.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）与 TestClient，每条测试的文档字符串写明它守住的设计或验收原句，覆盖：不给限购件数时库里为 10；0、100、-1 被检查约束拒绝，1 与 99 可写入；同一商品两个 SKU 合计等于限购两行都正常、超过限购两行都标为超出限购，另一件限购 1 的商品不受影响；默认限购 10 在计价中生效；单行超过限购；超出限购同时库存不足标为超出限购（仍给出可用库存），只缺货为库存不足；缺货的行计入商品合计；同一商品停用 SKU 与不存在的 SKU 不计入合计且只回 SKU、件数与状态；超出限购的行带商品信息、单价、行小计并计入商品小计，合计仍为小计加运费，不可下单，减到限购之内即可下单；三类有商品信息的行各带自己商品的 `max_per_order`；99 件接受、100 件 422；商品详情返回 `max_per_order`，商品列表与分类项没有。`tests/test_checkout_quote.py` 只把原 11 件被拒的用例改为 100 件被拒、件数上限的对照改为 99 件可接受（含两处文档字符串里的上限），并在 `_red_line` 的整体相等期望里补上 `max_per_order`；`tests/test_catalog_api.py` 只在商品详情的整体相等断言里补上 `max_per_order`。这些测试与迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 及 `alembic check` 只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；计价查询未在真 MySQL 上执行。检查命令结果由 Worker 另行记录。

### SHOP-TASK-017 商品详情 P03 与浏览器购物车

- [x] 在 SHOP-TASK-014、015 的框架、字典、路由、金额格式化与接口模块上，按 `docs/UX.md` 0.6 的 P03、`docs/UX-COPY.md` 0.5 与 `docs/design/pages/P03-*.html` 实现商品详情页与只存在本浏览器的购物车模块。不在浏览器计算金额，不调用计价接口，不装依赖（未改 `package.json`、锁文件），不改后端、CI、部署配置或设计。设计闸门：不适用（只展示目录接口返回的单价与库存，购物车只存 SKU、商品 slug 与件数，不涉及个人资料；限购只是提示，校验在服务端）。
- 文件结构（全部在 `frontend/src/`）：
  - `router.tsx`：路由表仍是字符串常量表，改为 `["/", "/products", "/products/:slug", "/privacy"]`；`RoutePattern` 是表里的模式，`RoutePath` 是由模式推出的实际路径类型（`/products/${string}`），`Link` 与 `navigate` 只收实际路径。`matchRoute` 按段匹配：静态段逐字相等，动态段用 `decodeURIComponent` 解码一次，解码失败、解码后为空、为 `.` 或 `..`、或含 `/` 时不匹配（按未知路径落到首页），其余值不按字符集校验。路由上下文新增 `pattern`、`params`（解码后的段值）与 `replace`（替换当前历史记录）；`path` 仍是实际路径（语言切换链接在详情页上即 `/products/…`）。`productPath(slug)` 把 slug 用 `encodeURIComponent` 编码一次生成链接。`components/SiteFrame.tsx` 未改。
  - `App.tsx`：页面表按模式穷举，加入详情页；当前页以实际路径为 key，换商品时规格、数量与提示重新开始。
  - `api/catalog.ts`：详情类型 `ProductDetail`、`VariantDetail`；`productDetailUrl(language, slug)`（slug 是解码后的值，在路径里只编码一次）；非 2xx 抛 `CatalogError`（带状态码）；`Remote` 新增 `not_found`，只在 404 时出现（`failedRemote`）。
  - `cart.ts`：浏览器购物车（P04 之后复用）。`pages/productOptions.ts`：规格可选性、选择与取消、价格文案、限购与 20 行上限的判断（纯函数）。`pages/ProductDetailPage.tsx`：页面（`ProductDetailView` 为可单独渲染的展示部分，`addSelection` 为加入逻辑）。
  - `components/ProductCard.tsx`：整张卡片改为指向 `/products/<slug>` 的链接（首页精选与列表共用，`pages/HomePage.tsx` 未改）。`i18n/copy.ts` 从 UX-COPY 原样抄入 16 个键：`common.a11y_qty_decrease`、`common.a11y_qty_increase` 与 `detail.*` 14 个（`options`、`quantity`、`stock_left`、`add_to_cart`、`added`、`view_cart`、`select_all_options`、`max_per_order`、`limit_reached`、`cart_full`、`description`、`english_only`、`a11y_image`、`demo_hint`）。`styles/site.css` 加 P03 的布局与显隐。
- 页面结构：
  - 接口返回之前（含服务端渲染与按路由表遍历的测试里把 `/products/:slug` 本身当路径时，slug 为 `:slug`）只有空的 `<main class="site-detail" aria-busy="true">`：没有文字、读屏标签、行内样式、表单或链接；请求失败时只显示 `common.error_retry`；接口 404 时用 `replace` 换成 `/products`（不新增历史记录），不另显示文字。
  - 数据返回后：面包屑 `common.nav_shop`（链到 `/products`）/ 分类（链到 `/products?category=<slug>`）/ 商品名称（`aria-current="page"`），分隔符为读屏忽略的图形，`<nav>` 不加读屏标签。左侧图片：桌面主图加缩略图（`<button aria-pressed>`，点选切换主图），手机为横向滚动吸附的一排主图（CSS scroll-snap，可左右滑动）；主图、缩略图按钮与手机每张图的读屏标签都是 `detail.a11y_image`（第 n 张，共 n 张）；没有图片时为占位形状、不渲染缩略图。右侧依线框顺序：名称（回退英文时旁边 `detail.english_only`，`acs-tag--outline`）、价格、`detail.options` 与各规格组（`acs-chip`，`aria-pressed`，组以规格名为标签）、`detail.quantity` 与数量步进器（(-)(+) 为图形，读屏标签 `common.a11y_qty_decrease`、`common.a11y_qty_increase`，值在 `<output>`）、`detail.max_per_order`、选全后的 `detail.stock_left`、购买区（提示与加入按钮）、★ `detail.demo_hint`。下方描述：桌面为 `detail.description` 标题加正文，手机为默认收起的 `<details>`。767px 以下购买区固定在屏幕底部，面包屑不显示（手机线框没有）。
  - 规格：多个启用 SKU 时初始不预选；只有一个启用 SKU 时直接选定（含无规格商品）。点一个值即选上（替换该组原值）；再次点已选的值取消该组（Kelvin 2026-10-03 决定），只有一个启用 SKU 时不取消。某值在其他组已选值下组不成任何启用 SKU 时禁用；禁用的值点了不改变选择。选全前价格按 SHOP-TASK-015 的起价规则：多于一个启用 SKU 一律 `list.price_from`（启用 SKU 中最低的单价），一个时 `common.price_myr`；选全后为所选 SKU 的 `common.price_myr` 与 `detail.stock_left`（`available_stock`）。只格式化服务端给的整数仙，取最低单价是比较、不是计算。
- 购物车存储格式与清洗规则（`cart.ts`）：`localStorage` 键 `acuven-shop.cart`，值为 JSON 数组，每项恰好 `{"sku", "slug", "quantity"}`，不存价格、名称或个人资料。读取时：不是 JSON 或不是数组为空购物车；丢弃 SKU 或 slug 不是非空字符串、件数不是 1 到 99 的整数的行，丢弃与前面重复的 SKU，只保留前 20 个合格行；多出的字段不带出。写入前同样清洗。读取抛错或没有存储时为空购物车，写入抛错或没有存储时返回 false 而不抛错。同一 SKU 再次加入时合并件数（位置不变），新 SKU 加在末尾；合并后超过 99 件或满 20 行时的新 SKU 不加入。页面经 `useSyncExternalStore` 订阅（同一份存储内容返回同一个数组；本页写入后通知，其他标签页改动经 `storage` 事件刷新；服务端渲染为空购物车）。
- 限购提示规则：`detail.max_per_order` 显示接口的 `max_per_order`。剩余件数 = `max_per_order` − 购物车中同一 slug 各行件数之和（不小于 0）；数量 (+) 在达到剩余件数时禁用，(-) 在 1 时禁用，显示的数量夹在 1 与剩余件数之间，加入的件数也不超过剩余件数。剩余为 0 时加入按钮禁用并显示 `detail.limit_reached`（`{count}` 为限购件数）；否则购物车已有 20 行且所选 SKU 不在其中时禁用并显示 `detail.cart_full`（`{count}` 为 20）；尚未选全时不判断购物车已满。未选全时按钮可点，点后显示 `detail.select_all_options`（`role="alert"`）。加入成功后显示 `detail.added`（`role="status"`，带视觉稿的勾选图形），数量回到 1；`detail.view_cart` 只在 `/cart` 进路由表后渲染，本任务不渲染。改选规格或数量时清除上一次的提示。两处禁用都只是提示，下单时由服务端再校验。
- 偏离与取舍，请审阅：
  - 视觉稿与 UX、验收不一致处按后者：视觉稿的数量 (-)(+) 是文字「−」「+」，按验收改为图形；视觉稿面包屑带 `aria-label="Breadcrumb"`，字典里没有，不加；视觉稿页头的购物车入口与提示条里的「View cart」按路由规则不渲染。
  - `site.css` 不写颜色、字体与圆角：视觉稿选中缩略图的 `--accent` 边框改为未选中的缩略图半透明；手机主图下方的圆点指示（需要底色与圆角）没有做；手机主图沿用 `acs-cat__img` 的圆角；固定底栏的阴影没有搬；固定底栏的底色与上一任务的筛选抽屉一样，给购买区加根元素的 `acs` 类取 `--surface`，分隔线用 `border-top: 1px solid var(--rule)`。手机加入按钮沿用桌面的 `acs-btn--lg`（类名不能按宽度切换）。
  - 「仅英文」标签在名称或描述任一回退英文时显示（UX 说「缺少商品文案、回退英文时」，标签放在名称旁）；回退的名称、分类、规格名与值、描述各自加 `lang="en"`。
  - 选全前的起价取全部启用 SKU 的最低价，不随已选的部分规格收窄（按 SHOP-TASK-015 的起价规则原样沿用）。
  - 当日库存为 0 的 SKU 不禁用加入（UX「库存剩余只作提示，加入购物车不预留库存」），由之后的计价提示缺货。
  - 写入本浏览器失败时（存储已满、隐私模式等）显示已有的 `common.error_retry`，购物车不变；UX-COPY 没有专门的文案，未自行编写。
  - 切换语言时按新语言重新请求，已选规格、数量与提示保留（规格以 code 记录）；新请求返回前页面主体短暂为空。
  - UX-COPY 里没有、因而没有渲染的界面文字（不自行编写）：面包屑与缩略图组的整体读屏标签、加载中的提示（加载中只给 `<main>` 的 `aria-busy`）。
  - 已有测试的改动：`router.test.tsx` 的路由表断言改为四项，未知路径样例去掉 `/products/tee`（它现在是详情页，改加 `/products/tee/`、`/products/a/b`），另加匹配与解码的用例；`components/SiteFrame.test.tsx` 只从「renders no navigation item for pages that do not exist yet」的 href 正则里去掉 `products\/` 并改写其上方注释；`components/ProductCard.test.tsx` 把「卡片不是链接」改为「整张卡片链到详情页」，两处根元素断言由 `<div` 改为带 `href` 的 `<a`，渲染时包上 `RouterProvider`；`pages/HomePage.test.tsx` 与 `pages/ProductListPage.test.tsx` 各只新增一条详情链接的断言。`App.test.tsx` 未改。其余测试未删减、未放宽。
- 验证到什么程度：人工逐条对照验收标准自查，并人工核对新增的 16 个字典键与 UX-COPY 原文（逐字比较由已有的 `i18n/copy.test.ts` 覆盖新键）。测试（vitest 与 `react-dom/server`，未加测试依赖，`fetch` 以 `vi.fn`、`localStorage` 以内存对象替身）每条注释写明它守住的 UX、DESIGN 或验收原句（取消选择的测试写明守住本任务规格选择那条验收标准，即 Kelvin 2026-10-03 的决定），没有直接原句的边界标为「派生实现约束（实现选择）」并写明上层条款，覆盖：路由表四项；`%2E`、`%2e`、`%2E%2E`、`.`、`..`、`%2F`、`a%2Fb` 与坏的百分号编码（`%`、`%E4%B8`、`%zz`）不匹配并落到首页；`%3A`、`a%20b`、`%E4%B8%AD`、`a%2520b` 匹配并分别得到「:」「a b」「中」「a%20b」；`/products/:slug` 本身匹配得到「:slug」；商品链接编码一次并解码回原值；详情请求路径只编码一次（含 `a%20b` 编码为 `a%2520b`）且只带语言；404 带状态码并映射为 `not_found`，其他失败为 `error`；替换历史记录；详情路径渲染空主体、语言链接指向实际路径，不匹配的详情路径渲染首页；购物车只存三个字段、非 JSON 与非数组、坏行与越界件数、重复 SKU、超过 20 行、读写抛错与没有存储、快照在内容不变时不变；合并件数、新行追加、20 行时拒绝新行但仍可合并、件数越界、同一商品各 SKU 合计；规格可选性（只有红/S 与蓝/M 时，选红与 S 后蓝和 M 都禁用，依次取消这两组后蓝与 M 可选）、只有一个 SKU 时预选且不取消、多 SKU 初始不选、无规格商品、禁用值点了不变、同组换值；选全前后的价格（含同价两个 SKU 仍为「起」）与库存；限购剩余按同一商品各 SKU 合计、为 0 时按钮禁用与 `detail.limit_reached`、购物车满时的禁用与 `detail.cart_full`、所选 SKU 已在其中时不禁用；`addSelection` 未选全、合并、不超过剩余件数、禁用时不加入、写入失败返回错误；页面三种语言下的面包屑、回退英文标签、图片读屏标签、线框顺序、★ 提示、桌面与手机描述、规格芯片的按下与禁用、数量步进器为带读屏标签的图形按钮、限购显示与 (+) 上限、未选全提示、已加入提示且不渲染 `detail.view_cart`、不显示参考外币，以及多个状态下页面文字只来自字典（商品数据与数量数字除外）；接口返回前的 `/products/:slug` 与实际详情路径主体为空、无行内样式；商品卡、首页精选与列表的详情链接。所有路由（现含 `/products/:slug`）在三种语言下的页面文字仍只来自字典、没有行内样式与表单（已有测试）。浏览器相关的部分（实际请求与 404 后的替换、effect、点击选择与加入、`localStorage` 的真实读写与 `storage` 事件、缩略图切换、手机滑动与折叠、固定底栏、媒体查询下的显隐与布局）未在 DOM 中执行，只以纯函数与服务端渲染的各状态验证。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，也未与参考图对比）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-018 购物车 P04

- [x] 在 SHOP-TASK-014、015、017 的字典、路由、框架、金额格式化与购物车模块上，按 `docs/UX.md` 0.6 的 P04、`docs/UX-COPY.md` 0.5 与 `docs/design/pages/P04-*.html` 实现购物车页与页头的购物车入口。不在浏览器计算任何金额，不给收货国家，不做结账，不装依赖（未改 `package.json`、锁文件），不改后端、CI、部署配置或设计。设计闸门：不适用（只显示计价接口返回的金额，不改金额计算；购物车只存 SKU、商品 slug 与件数，不涉及个人资料）。
- 文件结构（全部在 `frontend/src/`）：
  - `router.tsx`：路由表改为 `["/", "/products", "/products/:slug", "/cart", "/privacy"]`；`App.tsx` 的页面表加入 `CartPage`。
  - `api/checkout.ts`：计价接口的类型（正常、超出限购、库存不足的行与只有 SKU、件数、状态的不可购买行）、`quoteUrl(language)`（只带 `lang`）、`quoteRequestBody(lines)`（每行只有 `sku` 与 `quantity`）、`fetchQuote`（POST，`credentials: "omit"`，非 2xx 抛 `QuoteError`）与 `latestQuoter`（只采用最后一次请求的结果）。
  - `cart.ts`：新增 `cartItemCount`（页头件数）、`setLineQuantity`（只改一行件数，1 到 99 之外或 SKU 不在购物车中时不改）、`removeLine`；存储格式与清洗规则不变。
  - `pages/CartPage.tsx`：页面（`CartView` 为可单独渲染的展示部分；`quoteKey`、`quoteState`、`hasChangedLine`、`productTotal`、`canIncrease`、`changeQuantity`、`removeFromCart` 为纯函数或写回逻辑）。
  - `components/SiteFrame.tsx`：页头购物车入口。`i18n/copy.ts` 从 UX-COPY 原样抄入 12 个键：`common.nav_cart` 与 `cart.*` 11 个（`title`、`empty`、`remove`、`subtotal`、`shipping_later`、`price_recheck`、`item_changed`、`over_limit`、`checkout`、`continue`、`demo_hint`）。`styles/site.css` 加 P04 与页头购物车入口的布局与显隐。`pages/ProductDetailPage.tsx` 只改写「购物车页 P04 未实现」那句注释，代码未改。
- 页面结构：
  - 页头：`common.nav_cart`，`{count}` 为本浏览器购物车各行件数之和（经 `useCartLines` 订阅，服务端渲染与首次水合时为 0）。桌面在导航行右侧（视觉稿位置），手机在第一行语言按钮之后（手机线框位置），☰ 菜单里没有；在 `/cart` 上标 `aria-current="page"`。入口只在 `/cart` 在路由表里时渲染。
  - 购物车页：`cart.title` 与 ★ `cart.demo_hint` 在上方。购物车为空时显示 `cart.empty`、链到 `/products` 的 `cart.continue`（`acs-btn--secondary`，取自 P04-states）与其后的 `cart.shipping_later`、`cart.price_recheck`，不请求计价。非空时：首次结果返回前主体只有标题、★ 提示与 `cart.shipping_later`、`cart.price_recheck`（`aria-busy`）；请求失败时在这两条说明之前显示 `common.error_retry`，购物车不动；结果返回后，每行依桌面视觉稿为 图片（没有图片时为占位形状）| 名称（链到商品详情，回退英文时 `lang="en"`）与规格值（值之间是读屏忽略的斜线图形）| 数量步进器（(-)(+) 为图形，读屏标签 `common.a11y_qty_decrease`、`common.a11y_qty_increase`，件数在 `<output>`）| 行小计（`common.price_myr`）| `cart.remove`；各行之后为 `cart.item_changed` 提示条（`acs-alert--danger`，带 P04-states 的图形）。右侧小计栏（`acs-summary`）：`cart.subtotal` 与商品小计、`cart.shipping_later`、`cart.price_recheck`、`cart.continue`（链到 `/products`）。767px 以下为单列：每行 图片 | 名称、规格、步进器与行小计、移除；`cart.shipping_later`、`cart.price_recheck` 在各行之后；`cart.subtotal` 与商品小计固定在屏幕底部。`cart.checkout` 按路由规则在结账页实现前不渲染（桌面小计栏与手机底栏都只在 `/checkout` 进路由表后出现）。
- 计价调用时机：页面打开（读到非空购物车）、改件数、移除、切换语言、别的标签页改动购物车（`storage` 事件）后，用购物车各行的 SKU 与件数调用 `POST /api/checkout/quote?lang=<当前语言>`；请求体只有 `lines`，不带收货国家、价格或 slug；请求标识只由语言与 SKU、件数决定。新请求发出时中止上一个，上一个无论先后返回都不采用；离开页面或购物车清空时中止尚未返回的请求。新结果返回之前继续显示上一次的结果（主体标 `aria-busy`），其中已从购物车移除的行立即不再显示，步进器显示购物车里的件数；金额与状态等新结果返回后更新。金额只把接口的 `line_subtotal_sen`、`subtotal_sen` 交给 `formatSen` 格式化，不在浏览器相加或相乘，不显示单价、运费或参考外币。
- 状态提示规则：任何一行状态不是 `ok`（`unavailable`、`over_limit`、`insufficient_stock`）时显示 `cart.item_changed`。`over_limit` 的行下显示 `cart.over_limit`（`{count}` 为该行接口返回的 `max_per_order`）。不可购买的行只显示接口返回的 SKU 与件数（占位图形，没有名称链接、价格或步进器），只能移除。数量 (-) 在 1 件时禁用；(+) 在同一商品（按接口返回的 `product_slug`）各行在购物车里的件数合计达到该商品 `max_per_order`、或该行已到 99 件时禁用，超出限购的行因此也禁用。改件数与移除写回本浏览器购物车（`saveCart`，通知页头与本页重新计价）；写入失败时在各行上方显示 `common.error_retry`，购物车不变。
- 偏离与取舍，请审阅：
  - 视觉稿与 UX、验收不一致处按后者：视觉稿的数量 (-)(+) 是文字「−」「+」，沿用 P03 改为图形；规格「Black / M」的斜线改为读屏忽略的图形（「/」不是字典文字），与 P03 面包屑一致；视觉稿与线框的 `cart.checkout` 按路由规则不渲染，所以手机底栏现在只有商品小计。
  - `site.css` 不写颜色、字体与圆角：视觉稿名称链接的墨色、600 字重与去下划线没有搬，名称按普通链接显示；手机底栏的阴影没有搬，底色沿用 P03 的做法给底栏加根元素的 `acs` 类，分隔线用 `border-top: 1px solid var(--rule)`。手机小计栏与桌面小计栏、手机说明与桌面说明各是一份标记，按宽度只显示其一（与页头、P03 的做法相同）。手机线框非空时没有 `cart.continue`，手机上只在空购物车时显示。
  - UX P04 线框的空状态只写了「[cart.empty] ([cart.continue])」；验收第 5 条要求显示 `cart.shipping_later` 与 `cart.price_recheck`，UX P04 演示提示也列了 `cart.price_recheck`，所以空购物车、首次加载中与计价失败（没有小计栏）时这两条放在主体里，桌面与手机同一份标记。
  - 计价失败时只显示 `common.error_retry`，不显示上一次的结果（避免展示可能已过期的金额）；UX-COPY 没有「重试」按钮的文案，未自行编写，访客改动购物车、切换语言或重新打开页面时会再次计价。
  - 不可购买的行显示 SKU 原文与件数作为「接口返回的内容」；`insufficient_stock` 行的可用库存（`available_stock`）UX-COPY 没有对应文案，只以 `cart.item_changed` 提示，不显示数字。
  - UX-COPY 里没有、因而没有渲染的界面文字（不自行编写）：加载中的提示（只给 `<main>` 的 `aria-busy`）、列表与小计栏的整体读屏标签、计价失败后的重试按钮。
  - 已有测试的改动（只按验收允许的范围）：`components/SiteFrame.test.tsx`「renders no navigation item for pages that do not exist yet」只从 href 正则里去掉 `cart` 并改写其上方注释，另新增页头购物车入口的两条测试；`pages/ProductDetailPage.test.tsx`「confirms the item was added without a cart link」改为「confirms the item was added with a link to the cart」，断言提示条含链到 `/cart` 的 `detail.view_cart`，注释随之更新，该文件其他断言未动；`App.test.tsx`「shows only dictionary text」只为接受由字典模板替换变量得到的文字（页头 `common.nav_cart`）而改为同时按带变量条目的模板匹配，其余断言未删减、未放宽；`router.test.tsx` 的路由表断言改为五项、`/cart` 改为在表里（另断言 `/checkout` 不在表里），未知路径样例加 `/cart/`，另加 `/cart` 渲染购物车页的用例；`cart.test.ts` 只新增用例。
- 验证到什么程度：人工逐条对照验收标准自查，并人工核对新增的 12 个字典键与 UX-COPY 原文（逐字比较由已有的 `i18n/copy.test.ts` 覆盖新键）。测试（vitest 与 `react-dom/server`，未加测试依赖，`fetch` 以 `vi.fn`/`vi.stubGlobal`、`localStorage` 以内存对象替身）每条注释写明它守住的 UX 或验收原句，没有直接原句的边界标为「派生实现约束（实现选择）」并写明上层条款，覆盖：请求体每行只有 `sku` 与 `quantity`、实际请求只有 `lines`、不带收货国家、价格或 slug、地址只带语言、不带 cookie；非 2xx 抛错；先发的请求先返回或后返回都不采用并被中止、只报告最新请求的失败、取消后不采用；请求标识与 slug 无关；当前请求、等待中（显示上一次结果）、加载中与失败各状态；每行图片、名称链接、规格值与图形分隔、件数、行小计与 `cart.remove`；行小计与单价×件数不符、商品小计与各行之和不符时照样显示接口的数；不显示参考外币；★ 提示、`cart.shipping_later`、`cart.price_recheck` 在桌面小计栏与手机说明，空购物车、加载中与失败时也显示这两条；线框顺序；`cart.continue` 链到列表、不渲染 `cart.checkout`；移除的行在新结果前不显示；三种非正常状态各自显示 `cart.item_changed`，全部正常时不显示；`cart.over_limit` 只在超出限购的行下且 `{count}` 为接口的限购件数；同一商品各行合计达到限购时这些行的 (+) 禁用、另一商品不受影响、按购物车件数合计、99 件上限；1 件时 (-) 禁用；不可购买行只有 SKU、件数与移除；空购物车（含经 `App` 渲染 `/cart`）；首次加载中不显示行与金额；请求失败显示 `common.error_retry` 且（网络错误与非 2xx 两种）本浏览器购物车不变；写入失败的提示；改件数与移除写回购物车、件数低于 1 不写、写入失败返回 false；`cartItemCount` 为各行件数之和、`setLineQuantity` 与 `removeLine`；多个状态下页面文字只来自字典（商品数据、SKU、件数与金额模板除外）；页头 `common.nav_cart` 桌面与手机各一个链到 `/cart` 的入口、在购物车页标为当前、不在 ☰ 菜单里；P03 加入后的提示条含链到 `/cart` 的 `detail.view_cart`；路由表五项、`/cart` 渲染购物车页。所有路由（现含 `/cart`）在三种语言下的页面文字仍只来自字典（含模板替换）、没有行内样式与表单（已有测试）。浏览器相关的部分（实际请求、effect 中的重新计价与中止、点击 (+)(-) 与移除、`localStorage` 的真实读写与 `storage` 事件、页头件数随购物车变化、固定底栏、媒体查询下的显隐与布局）未在 DOM 中执行，只以纯函数与服务端渲染的各状态验证；服务端渲染下页头件数恒为 0，各行件数之和只由 `cartItemCount` 的测试守住。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，也未与参考图对比）。检查命令结果由 Worker 另行记录。

### SHOP-TASK-019 订单访问授权：服务端会话、授权记录、cookie 与 CSRF

- [x] 按 `docs/DESIGN.md` 1.10（提交 `e3b3505`）「权限与资料保护」第 6 条（游客短期凭据与查单授权）与第 7 条（日志）建立订单访问会话与按订单的授权记录：`app/models/order_access.py`（`app/models/__init__.py` 导出 `OrderAccessSession`、`OrderAccessGrant`）、迁移 `alembic/versions/20261001_0009_order_access.py`（revision `0009`，down_revision `0008`）、`app/services/order_access.py`；测试见 `tests/test_order_access.py`。不加接口、不改 `app/main.py`、不建订单、不做查单或支付逻辑、不做页面。设计闸门：DESIGN 1.10（提交 `e3b3505`）。
- 两张表（都显式 InnoDB、utf8mb4；时间是不带时区的 UTC；外键一律 RESTRICT；除 `revoked_at` 外全部非空；约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字）：
  - `order_access_sessions`：`id`（`pk_order_access_sessions`）、`token_hash` String(64)（唯一 `uq_order_access_sessions_token_hash`）、`created_at`、`expires_at`、`revoked_at`（可空）。检查约束：`ck_order_access_sessions_token_hash_length`：`LENGTH(token_hash) = 64`；`ck_order_access_sessions_expires_after_created`：`expires_at > created_at`。不存令牌原文、IP、浏览器标识或任何个人资料。
  - `order_access_grants`：`id`（`pk_order_access_grants`）、`session_id` → `order_access_sessions.id`（`fk_order_access_grants_session_id_order_access_sessions`，RESTRICT）、`order_id` → `orders.id`（`fk_order_access_grants_order_id_orders`，RESTRICT；索引 `ix_order_access_grants_order_id`）、`scope` String(16)、`created_at`、`expires_at`、`revoked_at`（可空）；唯一 `uq_order_access_grants_session_id_order_id_scope`。检查约束：`ck_order_access_grants_scope_valid`：`scope IN ('guest_checkout', 'lookup')`；`ck_order_access_grants_expires_after_created`：`expires_at > created_at`。
  - 迁移 upgrade 先建会话表再建授权表与订单 ID 索引；downgrade 先删授权表（索引随表删除；不单独先删索引，因为 MySQL 不许删除外键仍需要的索引），再删会话表。`alembic check` 不比较检查约束与表选项，这两项只经人工核对模型与迁移一致。
- 令牌与签发规则（`issue_grant(db, cookie_value, order_id, scope, now)`）：cookie 值须恰好 43 个 URL 安全字符（`secrets.token_urlsafe(32)`，256 位随机）；格式合法且对应会话未撤销、未到期（`expires_at > now`）时沿用，否则新建会话。授权有效期固定 30 分钟；同一会话、订单与范围已有授权时把到期时间更新为 `now + 30 分钟` 并清空撤销时间（创建时间不变），否则新建；会话到期时间取它与新授权到期时间中较晚者。返回 `IssuedGrant(new_token, csrf_token, expires_at)`，沿用会话时 `new_token` 为空。只 flush、不提交。库里只存令牌的 SHA-256 小写十六进制摘要。未知范围或带时区的时间抛 `ValueError`。
- 校验规则：`verify_grant(db, cookie_value, order_id, scope, now)` 只在会话存在、未撤销、未到期，且该会话对该订单、该范围的授权存在、未撤销、未到期时返回真；格式不合法的 cookie 值不查库直接返回假；`guest_checkout` 与 `lookup` 互不替代，授权不跨订单。
- CSRF 规则：CSRF 令牌 = SHA-256(`shop-order-access-csrf:` + 令牌原文) 的十六进制，不入库，与入库摘要不同，持有 cookie 时随时可重新算出（`csrf_token_for`）。`verify_csrf(db, cookie_value, csrf_header, now)` 在请求头缺失或为空、cookie 格式不合法或会话不存在、已撤销、已到期时返回假，否则用 `hmac.compare_digest` 对字节做常量时间比较。其 docstring 写明支付、取消、确认收货与退款申请等写操作须同时通过 `verify_grant` 与 `verify_csrf`。
- cookie 属性（`set_order_access_cookie(response, token)`）：只一个 cookie `__Host-shop_order_access`，`HttpOnly`、`Secure`、`SameSite=Lax`、`Path=/`、`Max-Age=1800`，不设 `Domain` 与 `Expires`；令牌格式不合法时抛 `ValueError`（消息不含该值）。模块不写日志；令牌、CSRF 令牌与 cookie 值不出现在异常消息里。
- 偏离：未改设计；未发现设计本身的问题。说明几处取舍，请审阅：
  - Starlette 的 `set_cookie` 把 SameSite 原样写出，本实现传 `"lax"`，响应头里是 `SameSite=lax`；属性值按 RFC 6265bis 不区分大小写，浏览器按 Lax 处理。
  - 沿用会话时不返回新令牌，之后的接口任务应把原 cookie 值再传给 `set_order_access_cookie`，让 cookie 的 Max-Age 与新授权同时到期（写在 `issue_grant` 的 docstring 里）。
  - 并发对同一会话、订单与范围签发时，后提交的一方会违反唯一约束，由调用方回滚重试；本任务不加重试。
  - `__Host-` 前缀要求 `Secure`，浏览器在非 HTTPS 的本地开发环境里不会存这个 cookie；之后的接口与页面任务须在 HTTPS 下验证。
  - 按订单撤销授权（订单取消、被认领等）与过期行的清理都留给之后的任务；这里只提供可撤销的列与订单 ID 索引。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并人工核对迁移中的列类型、可空性、约束名与表达式、外键删除行为与表选项同模型按命名约定生成的一致。`tests/test_order_access.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表、自建游客订单，每条测试的文档字符串写明它守住的设计原句，覆盖：合法会话与授权的对照；两张表每个检查约束的反例；必填列为空被拒；重复令牌摘要与重复（会话、订单、范围）被唯一约束拒绝；授权引用不存在的订单或会话、删除被引用的订单或会话被外键拒绝，外键均为 RESTRICT；只有撤销时间可空；会话表没有令牌原文、IP 或浏览器标识列；订单 ID 索引；表选项；首次签发新建会话、令牌为 43 个 URL 安全字符、库里只有摘要且查不到令牌原文与 CSRF 令牌；两次新建令牌不同；签发不提交（回滚后无行）；带有效 cookie 沿用会话且不返回新令牌；cookie 对应会话已到期、已撤销、格式不合法或查不到时新建会话；同一会话两张订单的授权各自到期；重复签发延长原授权、清空撤销时间且不新增行；会话到期时间取较晚者；带时区时间与未知范围被拒；授权到期、撤销、会话撤销或到期、换订单、换范围（两个方向）都不通过；格式不合法的 cookie 不发任何 SQL；cookie 名称与属性（含没有 Domain）；CSRF 令牌对同一会话稳定、与入库摘要不同，错误、缺失、空、非 ASCII、他人会话的令牌及 cookie 无效时都不通过；校验函数说明写明写操作须两项校验；模块不写日志；签发失败的异常消息不含令牌、cookie 值或 CSRF 令牌。这些测试与迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 及 `alembic check` 只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；签发与校验的查询未在真 MySQL 上执行。检查命令结果由 Worker 另行记录。
