# SHOP-TASK-243 节日促销活动的公开展示接口（文案、装饰、海报、倒计时与规则说明的数据）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 10、12 条：只给展示数据，不返回内部名称、版本与任何个人资料。依赖 SHOP-TASK-233（`active_promotion`）与 240（`poster_path`）。不改计价、商品目录、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `app/services/promotion_display.py`：
  - `promotion_display(db, promotion_id, lang) -> PromotionDisplay | None`：按活动 ID 组装，不判断是否生效（之后的管理员预览按活动 ID 复用）；活动不存在或缺装饰行返回 `None`。只发 SELECT，读完 `db.rollback()` 结束读事务（调用方须先提交要读到的数据）。
  - 数据都是 frozen 的 Pydantic 模型（`extra="forbid"`），可直接作接口的响应模型。
- [x] 新增 `app/api/promotion.py`：`GET /api/promotion?lang=`，路由类 `_NoStoreRoute`（全部响应含 422 与 503 带 `Cache-Control: no-store`），`lang` 用 `app/api/pay.py` 的 `LanguageDep`（en、zh、ms，默认 en，不合法 422 不回显）。先以 `active_promotion(db, _utcnow())` 取生效活动的 ID，再以同一 ID 调用 `promotion_display`；每次请求从数据库读、不缓存，不接受预览参数。`_utcnow` 为模块级函数，测试以 monkeypatch 固定。
- [x] `app/main.py`：在公开段 `promotion_posters_router` 之后注册。
- [x] JSON 字段（供前台任务引用）：
  - 响应：`{"promotion": PromotionDisplay | null, "server_time": "<UTC 时间>"}`。时间一律带 UTC 时区，如 `"2026-11-13T04:00:00Z"`；前台倒计时用 `promotion.ends_at − server_time`。
  - `PromotionDisplay`：`promotion_id`；文案 `title`、`subtitle`、`badge`、`strip`（所选语言，缺译回退英文，英文也空即 `null`）；装饰 `pack`、`palette`、`ambient`、`home_poster`、`list_strip`、`card_badge`、`detail_ribbon`、`cart_progress`（清单 ID 原样）；`poster_mode`（`template` / `image`）；`poster_desktop`、`poster_phone`（上传图模式下为 `/api/promotion-posters/<文件名>.webp`，没有图为 `null`）、`poster_alt`（上传图模式下所选语言，缺译回退英文）——模板模式时三者都为 `null`；`ends_at`（实际结束：提前结束时为提前结束的时刻，草稿没有时间时为 `null`）；`coupon_combinable`、`points_combinable`；`rules`。
  - `rules[]`（`RuleDisplay`，按 `position` 升序）：`rule_id`、`position`、`kind`（`item_discount` / `buy_x_get_y` / `spend_discount` / `free_shipping`）、`discount_mode`（`percent` / `amount` / `null`）、`percent`、`amount_sen`、`buy_quantity`、`get_quantity`、`get_percent`、`threshold_sen`（不属于该类别的为 `null`）、`tiers`、`categories`、`has_products`。
  - `tiers[]`：`{threshold_sen, amount_sen, percent}`，按档的排序；只有满额减或满额折有，其余为 `[]`。
  - `categories[]`：`{slug, name}`，只列启用且有英文名称的分类，按分类 ID 升序，`name` 为所选语言（缺译回退英文）；整单级规则为 `[]`。
  - `has_products`：该规则显式选的商品中有满足 `published()`（上架）的。单件商品不逐一列出（UX 0.20 P15）。分类都停用且没有上架的显式商品时 `categories` 为 `[]`、`has_products` 为 `false`，规则照常列出。
  - 不含内部名称、版本号与任何个人资料。
- [x] 测试 `tests/test_promotion_display.py`（SQLite 内存库，StaticPool，打开外键检查；数据以模型行写入并提交；接口每次请求新开会话，`_utcnow` 固定为 2026-11-13 04:00 UTC）：三种语言的文案回退与空即 `null`；第 8 条算例四条规则的类别与参数（家居分类的中文名称、A 的规则 `has_products` 为真、满额减两档）；分类按 ID 排序、停用与无英文名称的不列、显式商品已下架与只剩停用分类的规则；海报模板与上传图两种模式；活动或装饰行不存在、草稿没有时间；接口在进行中、将提前结束、未开始、已结束与已提前结束时的 `promotion` 与 `server_time`；没有活动时为 `null`；响应不含内部名称与版本；不缓存且带 no-store；`lang` 不合法 422 不回显且带 no-store。每条测试的文档字符串写明它守住的第 10 或 12 条原句。
- 偏离与落实细节：
  - 文案「空」按去掉首尾空白后为空串判断（写入方已把空串存为空，这里对只有空白的值同样回退）。
  - 分类名称取法复用 `app/services/catalog.py` 的 `localized`；「有英文名称」的判断与目录的 `_present` 相同（TRIM 后非空），在本模块内另写一次，没有引用目录的私有函数。
  - 接口里 `active_promotion` 与 `promotion_display` 各读一次活动行（照落实写法先取 ID 再调用）；两次读取在同一请求里，中间不提交。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 10、12 条与 `app/models/promotion.py` 的列与约束。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 数据——frozen 或 Pydantic 的 PromotionDisplay(promotion_id, title, subtitle, badge, strip, pack, palette, ambient, home_poster, list_strip, card_badge, detail_ribbon, cart_progress, poster_mode, poster_desktop, poster_phone, poster_alt, ends_at, coupon_combinable, points_combinable, rules)，rules 为 RuleDisplay(rule_id, position, kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent, threshold_sen, tiers, categories, has_products) 按 position 升序，tiers 为对象 {threshold_sen, amount_sen, percent} 的列表，categories 为对象 {slug, name} 的列表（只列启用且有英文名称的分类：停用分类下的商品按现有发布规则本来就不显示）；categories 按分类 ID 从小到大（与前台分类列表相同）；has_products 为该规则显式选的商品中有满足现有发布规则（上架）的；分类都已停用且没有上架的显式商品时，categories 为空、has_products 为 false，规则照常列出（前台只显示规则说明，不附范围）；海报图地址为 SHOP-TASK-240 的 poster_path 或 null；不含内部名称与版本
      - 函数——promotion_display(db, promotion_id, lang) 返回 PromotionDisplay，活动不存在返回 None（每个活动都有装饰行，由 SHOP-TASK-249 新建时建好；万一缺少也返回 None）；海报为模板模式时 poster_desktop、poster_phone 与 poster_alt 都为 null（已上传的图不在前台显示）；只读、结束读事务；ends_at 为实际结束（提前结束时为提前结束的时刻，草稿没有时间时为 null）
      - 接口——新文件 app/api/promotion.py：GET /api/promotion?lang=，响应为 {"promotion"：PromotionDisplay 或 null，"server_time"：服务端当前时间}；每次请求从数据库读、不缓存；带 no-store；lang 不合法为 422 且不回显；在 app/main.py 公开段照现有顺序注册
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_promotion_display.py：三种语言的回退（中文缺译回退英文、空即 null）；第 8 条算例的四条规则给出的类别与参数（家居分类的名称、A 的规则 has_products 为 true）；海报模板与上传图两种模式；接口在活动生效、未开始、已结束与提前结束后的 promotion 与 server_time（固定 _utcnow）；响应不含内部名称；no-store 与 422。每条测试的文档字符串写明它守住的第 10 或 12 条哪一句
      - docs/task-records/SHOP-TASK-243.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明 JSON 字段名，供前台任务引用）；改动只落在 allowed_change_paths；不改计价、商品目录、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
