# SHOP-TASK-180 后台分类的读取、新建与保存规则（含 slug 生成）

- [x] 依据：`docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 14 条（后台维护分类）与第 11 条「代码」一段的基本写法、截短与去重；`docs/HANDOFF.md` 0.76 的落实写法。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 14 条与 HANDOFF 0.76，后台维护分类。版本号列已由 SHOP-TASK-177 加好；本任务不改模型与迁移、`app/services/catalog.py`、现有测试、前端与设计。接口由 SHOP-TASK-181 接上，页面 A10 另行登记。
- [x] 代码生成（新增 `app/services/admin_codes.py`，两个函数都不碰数据库，后台商品的任务沿用）：
  - `code_base(english_name: str, fallback: str) -> str`：只保留 ASCII 字母与数字并转小写，其余连续字符（含空白、标点与非 ASCII 字符）换成一个连字符，去掉首尾连字符；结果为空时返回 `fallback`。不截短。
  - `unique_code(base: str, limit: int, taken: Collection[str]) -> str`：依次试后缀（无、`-2`、`-3`……），每次先把 `base` 截到 `limit` 减后缀长度、去掉截短后末尾的连字符、再接后缀，返回第一个不在 `taken` 里的结果。同一次保存里先生成的由调用方加进 `taken`。
- [x] 服务层（新增 `app/services/admin_categories.py`）：
  - `AdminCategory`（冻结的 dataclass）：`id`、`slug`、`name_en`、`name_zh`、`name_ms`（空为 None）、`is_active`、`version`、`visible_products`。
  - `load_admin_categories(db) -> list[AdminCategory]`：全部分类（含停用的）按 id 从小到大；`visible_products` 为该分类下满足 `app/services/catalog.py` 的 `published()` 的商品件数（关联子查询，与前台同一规则）。只查列值、只发 SELECT，不写库、不写审计。
  - `lock_categories_statement()`：`SELECT … FOR UPDATE` 锁住现有全部分类行，按 id 排序，带 `populate_existing`（照 `lock_site_setting_statement`）。新建与保存共用这一条。
  - `create_category(db, admin_account_id, name_en, name_zh, name_ms, is_active, now) -> AdminCategory`：第一步不发 SQL——三个名称须为 str，去掉首尾空白后不超过 200 个字符，空串存为 None，英文名称必填；`is_active` 须为 bool。第二步回滚后锁住全部分类行再计数（已有 30 个即 `CategoryInvalid`）、比较英文名称（同名即 `CategoryInvalid`）；slug 为 `unique_code(code_base(英文名称, "category"), 100, 现有全部 slug)`，插入版本号 1，flush 撞上唯一约束即 `CategoryChanged`；写审计后提交。
  - `save_category(db, admin_account_id, category_id, version, name_en, name_zh, name_ms, is_active, now) -> AdminCategory`：第一步检查名称（同新建）、`is_active` 为 bool、为真时英文名称必填（停用时可为空）、`version` 为整数（布尔值不算）。第二步回滚后执行同一条锁定查询，该分类不在其中或版本号不同即 `CategoryChanged`，英文名称与其他分类相同即 `CategoryInvalid`。按 `name_en`、`name_zh`、`name_ms`、`is_active` 逐项与当前值比较，有改动的写新值、版本号加一后提交；都未变时回滚、不加版本、不写审计。slug 不改。返回保存后的分类。
  - 英文名称的比较口径：去掉首尾空白并转小写，在应用层比较（取回锁住的行后比对），为空的不参与。
  - 异常：`CategoryInvalid`（取值不合格）与 `CategoryChanged`（分类已被改动）只带固定的英文说明，不回显提交的值。锁行之后出现的任何异常都先回滚再抛出。
- [x] 审计（经 `record_audit`，对象类别 `category`、对象 ID 为分类 ID）：
  - 新建：`admin_category_created`，没有旧值，新值为 slug。
  - 保存：每改一项一条 `admin_category_changed`，按 `name_en`、`name_zh`、`name_ms`、`is_active` 的顺序；旧值与新值写成「字段 值」，文案取前 100 个字符，为空时只写字段名（如 `name_zh`），启用状态写 `is_active true` 或 `is_active false`。
- [x] 测试：新增 `tests/test_admin_categories.py`（SQLite 内存库按模型建表，每条测试的文档字符串写明它守住的第 14 条或第 11 条原句）。覆盖 `code_base` 的例子（`Kitchen & Dining` 为 `kitchen-dining`、首尾空白与标点、非 ASCII 字母当作分隔、全是符号时为 fallback）；`unique_code` 的截短去重（100 个字符的基本部分接 `-2` 时截到 98 并去掉末尾连字符、`kitchen-3`）；读取的顺序、停用分类、`visible_products` 与前台发布规则一致（停用分类、下架商品、缺英文描述的商品不计），读取只发 SELECT；新建与保存每类不合格都不发 SQL、不写库；第 31 个被拒；英文名称大小写与首尾空白不同也算重复（新建与保存）；版本号不符与分类不存在为 `CategoryChanged`；新建时 slug 撞上唯一约束为 `CategoryChanged`；改名不改 slug；每改一项一条审计、未变不写；停用后前台分类列表与商品列表不再有它们，重新启用后恢复；锁定查询按 MySQL 方言编译带 `FOR UPDATE` 并按 id 排序，新建与保存回滚之后的第一条语句都是它。
- 偏离与取舍：
  - 新建时传给 `unique_code` 的已有 slug 先转小写。生成的 slug 本来全为小写，转小写只在库里留有大写 slug 时起作用：MySQL 不区分大小写的排序规则下，这样的 slug 照字面比较会漏检、每次都撞上唯一约束。
  - 读取时三语名称为 NULL 或空串都返回 None（第 14 条「空串存为空」；旧数据里的空串也照此显示）。保存时空串与 NULL 视为不同，旧数据里存的空串被保存成 None 时算一项改动并写审计。
  - `save_category` 不检查 `category_id` 的类型：不是该分类的 ID（含类型不对）一律是「不在锁住的行里」，抛 `CategoryChanged`。
  - 未变时的返回值在回滚之前、于同一事务里读出；有改动时在提交之前读出，与 `save_admin_rates` 的写法一致。
  - 测试比验收多了几条对照：审计被拒（时间带时区）时整次回滚、不留新分类；改自己英文名称的大小写不算重复；别人先保存后带旧版本号被拒；超长的基本部分不带后缀时也截到上限。
- 验证范围：上面列出的是本次新增的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。SQLite 不复现 MySQL 的 `FOR UPDATE` 等锁，所以两个会话同时新建或保存时的等待行为不在这里覆盖，只检查锁定查询的写法与它在事务里的位置。

## 登记时的验收标准（原文）

2026-10-30 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 代码生成——新增 app/services/admin_codes.py，code_base(english_name, fallback) 只保留 ASCII 字母与数字并转小写，其余连续字符（含空白、标点与非 ASCII 字符）换成一个连字符，去掉首尾连字符，结果为空时返回 fallback；unique_code(base, limit, taken) 依次试后缀（无、-2、-3……），每次先把 base 截到 limit 减后缀长度、去掉截短后末尾的连字符、再接后缀，返回第一个不在 taken 里的结果（taken 为已有代码的集合，调用方负责把同一次保存里先生成的加进去）；两个函数都不碰数据库
      - 读取——新增 app/services/admin_categories.py，load_admin_categories(db) 返回全部分类（含停用的）按 id 从小到大，每项为 id、slug、三语名称（空为 None）、is_active、version 与 visible_products（该分类下满足 app/services/catalog.py 的 published() 的商品件数，与前台同一规则）；只发 SELECT，不写库、不写审计
      - 新建——create_category(db, admin_account_id, name_en, name_zh, name_ms, is_active, now)（now 为不带时区的 UTC 时间，交给 record_audit；admin_account_id 须在调用前取出）。第一步不碰数据库：三个名称须为 str，去掉首尾空白后不超过 200 个字符，空串存为 None；英文名称必填；is_active 须为 bool；不通过抛 CategoryInvalid、不发任何 SQL。第一步通过后先回滚此前的事务，第二步在新事务里以 SELECT … FOR UPDATE 锁住现有全部分类行（按 id 排序，锁定查询以函数给出并带 populate_existing，照 lock_site_setting_statement）后计数，已有 30 个即 CategoryInvalid；英文名称与任一现有分类相同（去掉首尾空白并转小写后在应用层比较，为空的不参与）即 CategoryInvalid；slug 为 unique_code(code_base(英文名称, category), 100, 现有全部 slug)；插入版本号 1，flush 时撞上唯一约束即回滚、抛 CategoryChanged；写一条审计 admin_category_created（对象类别 category、对象 ID 为新行 ID、新值为 slug）后提交，返回该分类（同读取的一项）
      - 保存——save_category(db, admin_account_id, category_id, version, name_en, name_zh, name_ms, is_active, now)。第一步检查三个名称（须为 str，去掉首尾空白后不超过 200 个字符，空串存为 None）与 is_active 须为 bool；英文名称只在 is_active 为真时必填（停用时可为空，DESIGN 1.18 第 14 条只在新建与启用时要求英文名称）；version 须为整数（布尔值不算）；不通过抛 CategoryInvalid、不发任何 SQL。第二步回滚后执行新建所用的同一条锁定查询，锁住现有全部分类行（按 id 排序，带 populate_existing），该分类不在其中或版本号与当前不同即回滚、抛 CategoryChanged；英文名称与其他任一分类相同（口径同上）即回滚、抛 CategoryInvalid。逐项与当前值比较——有改动的写新值、版本号加一、每改一项写一条审计 admin_category_changed（对象类别 category、对象 ID 为分类 ID），旧值与新值写成「字段 值」（字段为 name_en、name_zh、name_ms、is_active；文案取前 100 个字符，为空时只写字段名；启用状态写 true 或 false），按这个字段顺序写；都未变时回滚、不加版本、不写审计。slug 不变。返回保存后的分类（同读取的一项）
      - 异常——CategoryInvalid 与 CategoryChanged 只带英文说明，不回显提交的值；写入途中出现任何异常都先回滚再抛出
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_admin_categories.py（SQLite 内存库按模型建表），每条测试的文档字符串写明它守住的 DESIGN 1.18 第 14 条或第 11 条原句——code_base 的例子（Kitchen & Dining 为 kitchen-dining，首尾空白与标点去掉，非 ASCII 字母当作分隔，全是符号时为 fallback）与 unique_code 的截短去重（100 个字符的基本部分接 -2 时截到 98 并去掉末尾连字符、已有 kitchen 与 kitchen-2 时为 kitchen-3）；读取的顺序、停用分类、visible_products 与前台发布规则一致（停用分类、下架商品、缺英文描述的商品不计）；新建与保存的每类不合格都不写库；第 31 个被拒；英文名称大小写与首尾空白不同也算重复；版本号不符与分类不存在为 CategoryChanged；改名不改 slug；每改一项一条审计、未变不写；停用后 app/services/catalog.py 的前台分类列表不再有它、其下商品不再出现在前台商品列表，重新启用后恢复；锁定查询按 MySQL 方言编译带 FOR UPDATE 并按 id 排序，新建与保存回滚之后的第一条语句都是它
      - docs/task-records/SHOP-TASK-180.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明函数签名、审计写法与偏离取舍）
      - 改动只落在 allowed_change_paths；不改模型与迁移、app/services/catalog.py、现有测试、前端、.platform/、CI 与部署配置，不装依赖，不改设计
```
