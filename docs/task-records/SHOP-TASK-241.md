# SHOP-TASK-241 后台促销活动的发布、撤回、提前结束、删除与复制（服务层）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 1、2 条：活动何时生效（发布、撤回、提前结束），决定之后的计价；只写后台审计，不碰个人资料。依据 UX 0.20 A12 的状态表。依赖 SHOP-TASK-233（测试用 `active_promotion`）、249（检查函数与异常）与 240（`POSTER_KEY_PREFIX` 与 `PromotionEnded`）。不改接口、计价、下单、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 在 `app/services/admin_promotions.py` 249 的部分之后加上状态操作（模块文档字符串随之更新）；新测试 `tests/test_admin_promotion_status.py`。
- [x] 新异常 `PromotionStateInvalid(state)`：当前状态不允许这个操作，`state` 为当前状态（draft、scheduled、running、ended 之一）；已有订单引用的 scheduled 活动撤回时 `state` 为 running。其余沿用 249：`PromotionChanged`、`PromotionNotFound`、`PromotionInvalid(problems)`、`PromotionOverlap(other_name)`。`version` 不是 int（布尔值不算）抛 TypeError；`now` 不是不带时区的 datetime 抛 ValueError。
- [x] 公开函数（`now` 为不带时区的 UTC；`admin_account_id` 须在调用前取出，函数开头先回滚此前的事务）：

  ```python
  def publish_promotion(db, admin_account_id, promotion_id, version, now) -> PromotionDetail
  def withdraw_promotion(db, admin_account_id, promotion_id, version, now) -> PromotionDetail
  def end_promotion(db, admin_account_id, promotion_id, version, now) -> PromotionDetail
  def delete_promotion(db, settings, admin_account_id, promotion_id, version, now) -> None
  def copy_promotion(db, admin_account_id, promotion_id, now) -> PromotionDetail
  ```

  | 函数 | 允许的状态 | 加锁 | 抛出 | 结果 |
  | --- | --- | --- | --- | --- |
  | `publish_promotion` | draft | `lock_publish_row` → 活动行 → 装饰行 | `PromotionChanged`（不存在或版本不符）、`PromotionStateInvalid`、`PromotionInvalid`（`publish_problems` 的 publish_missing 与 `scope_overlap_problems` 的 scope_overlap，一起列出）、`PromotionOverlap`（`ensure_no_overlap`） | status 为 published，版本加一；开始时间已过的发布即为 running、即生效 |
  | `withdraw_promotion` | scheduled | 活动行；再以共享锁查 `orders.promotion_id` | `PromotionChanged`、`PromotionStateInvalid`（有订单引用时 state 为 running） | status 改回 draft，版本加一，时间与内容保留 |
  | `end_promotion` | running | 活动行 | `PromotionChanged`、`PromotionStateInvalid` | `ended_early_at` 为 `now` 截到整秒，版本加一，返回的 state 为 ended |
  | `delete_promotion` | draft | 活动行 → 装饰行 | `PromotionChanged`、`PromotionStateInvalid` | 删除规则的商品与分类范围、满额档、规则、装饰行与活动行，返回 None；提交后删海报对象 |
  | `copy_promotion` | 任何状态 | 不加锁 | `PromotionNotFound`（不存在） | 新草稿的 `PromotionDetail` |

  四个带 `version` 的函数都在加锁后核对版本、按 `now` 算状态；状态不允许时不改库、不写审计。发布、撤回与提前结束的 `updated_at` 取 `now`；删除与复制不改原活动的版本。任何异常都先回滚再抛出。
- [x] 常量：`ADMIN_PROMOTION_PUBLISHED`（admin_promotion_published）、`ADMIN_PROMOTION_WITHDRAWN`（admin_promotion_withdrawn）、`ADMIN_PROMOTION_ENDED`（admin_promotion_ended）、`ADMIN_PROMOTION_DELETED`（admin_promotion_deleted）、`ADMIN_PROMOTION_COPIED`（admin_promotion_copied）。
- [x] 审计：每种操作一条，`target_type` 为 promotion。发布、撤回、提前结束的 `target_id` 为活动 ID，没有旧值与新值；删除的 `target_id` 为被删的活动 ID，旧值为内部名称；复制的 `target_id` 为新草稿 ID，旧值为原活动 ID（十进制字符串）。都没有新值，不含个人资料。
- [x] 删除的海报：提交前读出桌面与手机两张图的文件名，提交后对有图的位置依次调用 `image_store.delete_image(settings, file_name, prefix=image_store.POSTER_KEY_PREFIX)`（只尝试 1 次；存储未配置或失败由 `delete_image` 只记日志）。没有图时不调用存储。
- [x] 复制：新草稿沿用原活动的内部名称、两个开关、规则（按原排序重写，含范围与满额档，规则 ID 是新的）、装饰的素材包、配色与六个装饰位，以及标题、副标题、角标短语与横条文字的三语文案。开始、结束与提前结束时间为空，`poster_mode` 改为 template，两张图的五列与 `poster_alt_en`、`poster_alt_zh`、`poster_alt_ms` 都为空。版本号 1，`created_at` 与 `updated_at` 取 `now`。
- [x] 测试 `tests/test_admin_promotion_status.py`（SQLite 内存库，StaticPool，打开外键检查；已发布的活动以 `create_promotion` 建成后直接改状态列；`monkeypatch` 替换 `image_store.delete_image`，假函数的 `prefix` 只限关键字且没有默认值）覆盖：四个带版本的操作在不允许的 12 种状态组合下抛 `PromotionStateInvalid` 且不改库；版本不符、活动不存在与版本为布尔值；发布草稿；开始时间已过的发布立即生效（`active_promotion` 返回它）；发布条件逐项缺失（规则、英文标题、开始、结束、结束已过、桌面图、手机图、英文替代文字），以及上传图模式齐全时可发布；草稿保存后商品改了分类，发布报 scope_overlap；与已发布活动相接可以、重叠抛 `PromotionOverlap`；对方提前结束后从结束时刻起的活动可以发布；撤回为草稿与已有订单引用时不能撤回；提前结束的时刻截到整秒、不晚于结束时间、之后不再生效，也不能再提前结束或撤回；删除连同规则、范围、满额档与装饰，提交之后才删两张海报对象（以替身记录 commit 与删除的先后），没有图时不调用存储，撤回后的活动可删除；四种状态都可复制，复制的新草稿没有时间与海报、模式为模板、替代文字清空、规则与原活动相同但 ID 不同；复制不存在的活动；五种审计的写法。每条测试的文档字符串写明它守住的第 1 或 2 条原句。
- 偏离与落实细节：
  - 删除也带版本号并核对（验收的签名如此），版本不符同样抛 `PromotionChanged`。
  - 发布与删除在活动行之后另锁装饰行（与 249 的保存、240 的海报一样先活动行后装饰行），发布条件读到的海报文件名、删除读出的文件名都不会过时；撤回与提前结束只读装饰行来组成返回值，不锁它。
  - 发布不另查规则引用的商品与分类是否存在：规则范围对商品与分类的外键为 RESTRICT，已保存的引用一定存在。
  - 复制不检查商品级规则重叠，也不检查发布条件：新草稿本来就不要求，发布时由 `publish_promotion` 按当时的商品与分类检查。
  - 提前结束的时刻满足模型的 `ended_early_within`：running 意味着 `starts_at <= now < 实际结束`，开始与结束都是整分钟，所以截到整秒后仍落在开始（含）与结束（不含）之间。
  - 删除照 249 的 `_replace_rules` 以 `synchronize_session="fetch"` 批量删除，不依赖外键的 CASCADE；删除时撞上外键（撤回已保证不会被订单引用）不另转成别的异常，回滚后原样抛出。
  - 审计的旧值与新值只写验收列出的两项（删除的内部名称、复制的原活动 ID），其余都为空；提前结束的时刻已在活动行与 `PromotionDetail` 里，不重复写进审计。
  - 在验收之外另导出五个审计操作名常量，供 242 与测试共用。
- 供 SHOP-TASK-242 的调用写法：接口在会话里取出管理员 ID，以请求里的 `version` 调用对应函数；`now` 取服务端当前的不带时区 UTC（提前结束的时刻即由它截到整秒）；删除另传 `settings`，函数会调用存储，应放进线程。需转成回答的异常：`PromotionChanged`（「活动已被改动」）、`PromotionStateInvalid`（带 `state`）、`PromotionInvalid`（问题码 publish_missing、scope_overlap 及其 `field`，见 SHOP-TASK-249 的记录）、`PromotionOverlap`（`other_name`）、复制的 `PromotionNotFound`。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 1、2 条、UX A12 的状态表，以及 `app/models/promotion.py` 的约束（`ended_early_within`、`published_has_times`、海报五列同为空或同不为空）与 `app/models/order.py` 的 `orders.promotion_id`；手算核对测试里的时间换算，例如马来西亚 2026-11-13 12:00 为 UTC 04:00，UTC 04:00:30.75 截到整秒为 04:00:30。SQLite 不生成 FOR UPDATE，MySQL 上的加锁顺序、撤回与下单共享锁的互斥没有在这里验证。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 函数——publish_promotion、withdraw_promotion、end_promotion、delete_promotion 的签名为 (db, settings 或省略, admin_account_id, promotion_id, version, now)（只有 delete 带 settings），copy_promotion(db, admin_account_id, promotion_id, now) 返回新草稿的 PromotionDetail；其余返回操作后的 PromotionDetail（删除返回 None）。版本不符或不存在抛 PromotionChanged（复制时不存在抛 PromotionNotFound）
      - 状态限制——发布只对 draft；撤回只对 scheduled；提前结束只对 running；删除只对 draft；复制对任何状态。不满足即抛新异常 PromotionStateInvalid（带当前 state），不改库
      - 发布——不满足发布条件或商品级规则按当时的商品与分类有重叠抛 PromotionInvalid（publish_missing 与 scope_overlap 问题），与其他已发布活动时间重叠抛 PromotionOverlap；通过后 status 为 published、版本加一。撤回——status 改回 draft、版本加一；已有订单引用时抛 PromotionStateInvalid（state 为 running）。提前结束——ended_early_at 为截到整秒的当前时刻，版本加一，之后 active_promotion 不再返回它
      - 审计——发布、撤回、提前结束、删除、复制各写一条：admin_promotion_published、admin_promotion_withdrawn、admin_promotion_ended、admin_promotion_deleted（旧值为内部名称）、admin_promotion_copied（target 为新草稿，旧值为原活动 ID）；target_type 为 promotion；不含个人资料
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_admin_promotion_status.py：每种操作在允许与不允许的状态下；发布条件逐项缺失；草稿保存后商品改了分类、发布时报 scope_overlap；两个活动的时间相接可以、重叠不行；开始时间已过的发布立即生效（active_promotion 返回它）；提前结束后不再生效且 ended_early_at 不晚于结束时间；已有订单引用的 scheduled 活动不能撤回；删除连同规则、范围、满额档与装饰，提交后删海报对象（以替身记录调用）；复制的新草稿没有时间与海报、模式为模板、规则与原活动相同但 ID 不同；版本不符；审计写法。每条测试的文档字符串写明它守住的第 1 或 2 条哪一句
      - docs/task-records/SHOP-TASK-241.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明函数签名与异常，供 242 引用）；改动只落在 allowed_change_paths；不改接口、计价、下单、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```
