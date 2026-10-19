# SHOP-TASK-006 示例商品目录种子数据

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

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.8「数据模型」Product / Variant 一行及 SHOP-TASK-004 的表结构一致；发现设计或表结构本身有问题时停下并在 PR 里说明，不自行修改
      - 新迁移文件 alembic/versions/20260930_0003_demo_catalog.py，revision 为 0003、down_revision 为 0002；数据写成迁移文件内的常量，用迁移内的轻量表定义写入，不 import app 的模型；upgrade 写入全部示例数据，downgrade 按 slug 与 SKU 删除这批数据及其图片、规格与关联行。部署只执行 upgrade、从不降级；downgrade 只保证在写入后未经后台编辑、未被其他数据引用的库上成立（CI 与开发库），不承诺生产库在后台编辑示例商品后仍可回退
      - 6 个启用的分类，slug 依次为 apparel、bags、home、kitchen、stationery、gadgets；共 28 到 32 件启用的商品，每个分类至少 4 件；slug、SKU 全部唯一、确定、小写
      - 全部为虚构的通用实物商品，不出现真实品牌、商标、真实人物、联系方式或任何网址
      - 每件商品与分类都有英文名称，商品都有英文描述，所有规格名与规格值都有英文名称，满足发布规则；除刻意的一件外，中文与马来文文案齐全；刻意的那一件只有英文，用来演示「仅英文」标签；马来文未经母语审校，在 PR 里如实说明
      - 规格形态三种都有：无规格（一个 SKU）、单规格（如颜色）、双规格（如颜色与尺寸），每种至少 3 件商品；双规格商品至少有一个颜色与尺寸组合没有 SKU，用来演示筛选须由同一个 SKU 同时满足
      - 单价是整数仙，范围 RM5 到 RM300，不全相同；每个 SKU 的当日可用库存等于每日初始库存；至少一件商品所有 SKU 的库存都为 0，用来演示今日售罄；同一件商品内的 SKU 价格可以不同
      - 商品创建时间各不相同、确定，且按 SHOP-TASK-004 的约定一律是 UTC，供按最新排序稳定演示
      - frontend/public/demo-images/ 下新增 apparel.svg、bags.svg、home.svg、kitchen.svg、stationery.svg、gadgets.svg 六张占位图：纯静态几何图形、不含文字，不含 script、foreignObject、事件属性、外链 href、CSS 的 url() 或 @import，每张不超过 4 KB；每件商品有且只有一张图片，引用写成以斜杠开头的站点根路径（如 /demo-images/kitchen.svg，页面可原样用作图片地址），指向所属分类的占位图
      - frontend/Dockerfile 只在复制 src 的那一行旁加一行复制 public 目录，使占位图进入前端镜像；前端镜像构建由 PR 的必需 CI 检查 frontend 执行并通过
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行
      - tests/test_demo_catalog_seed.py 用 SQLite 内存库（开启外键检查）按模型建表，通过 Alembic 的 Operations 执行该迁移的 upgrade，再查询实际写入的行验证上面的数量、唯一性、英文齐全、只有一件缺中文与马来文、规格形态与缺失组合、价格范围与整数类型、库存相等、售罄商品、创建时间互不相同、每件商品恰好一张图片且引用指向存在的占位图文件；再执行 downgrade 验证这批数据及其关联行全部删除；另读取六张 SVG 验证大小上限与上面列出的禁用元素和属性都不存在；每条测试写明它守住的是哪一句规则；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-006 记录段（分类与商品清单、图片引用格式、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；除 frontend/Dockerfile 这一行外不改 .platform/、CI、部署配置；不装依赖；不改表结构、不加接口、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.8，提交 2b68ad0），不合并、不直推
```
