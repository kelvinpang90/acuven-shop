# SHOP-TASK-061 后台店铺装修保存时放行原已在精选里的下架商品

- [x] 按 `docs/UX.md` 0.10 的 A08「之后下架的商品仍留在列表中，前台不显示它」与 `docs/HANDOFF.md` 0.40 记录的 Kelvin 2026-10-08 决定（精选里原已挑选、之后才下架的商品，保存时放行，只有新加入的商品须此刻上架；取代 SHOP-TASK-051 合同里「任一精选商品此刻不满足 published() 即 featured_unavailable」），修改 SHOP-TASK-051 的保存校验。改动：`app/services/store_design.py`、`tests/test_admin_store_design.py`、本文件。未改接口字段与错误码、读取、公开接口与前台、`app/api/`、模型与迁移、`.platform/`、CI 或部署配置，未加表或迁移，未装依赖，未改设计。设计闸门：不适用（只改前台外观设置的保存校验，不涉及金额计算与个人资料）。拆分：业务规则层，不拆。
- 规则变化（`save_store_design`）：锁定设置单例行之后先读取当前精选各行的商品 ID，再校验请求的精选商品——每个 ID 要么此刻满足 `published()`（沿用 `_published_ids`，只对不在当前精选里的 ID 查询），要么已在当前精选里；两者都不满足（含不存在的商品 ID）时照旧回滚并抛 `featured_unavailable`，不写入任何东西。放行只看保存前的当前精选：已下架的商品被移出并保存后再加回、或从未在精选里的已下架商品加入，都按新加入处理而为 `featured_unavailable`；已在精选里的已下架商品可以调整位置、与其他设置的改动一起保存，保存后仍在精选里，前台照旧按 `published()` 不显示它。因此与当前完全相同、含已下架商品的请求现在是 200（返回当前设置、不写入、不写审计），不再是 422。主题与主色的校验与顺序、锁定与事务、相同时回滚、审计操作名与新旧值格式、接口字段与错误码都不变。模块说明与 `save_store_design` 文档字符串里关于精选校验的句子随之改写（步骤 3 改为先读当前精选再校验，步骤 4 读取其余部分）。
- 已有测试的改动：`test_keeping_a_delisted_featured_product_is_422`（守住 SHOP-TASK-051 的「原样再提交已下架的精选商品为 422」，与 Kelvin 2026-10-08 决定相反）改名为 `test_keeping_a_delisted_featured_product_is_allowed`，改为验证放行：GET 里它的 `published` 为假；原样再提交为 200 且库里与审计都不变；只改主题时 200、它留在原位置、审计新值含它的 ID；调整它的位置可以保存；公开接口的 `featured_slugs` 仍不含它。新增 `test_re_adding_a_removed_delisted_product_is_422`（移出并保存后再加回为 `featured_unavailable` 且数据不变）。测试模块说明补上依据的 HANDOFF 0.40 决定。`test_unavailable_featured_product_is_422`（从未在精选里的不存在、已下架、分类停用、资料不全的商品）与其余现有测试未改。
- 偏离与取舍，请审阅：无偏离。并发方面与 SHOP-TASK-051 相同，商品行不加锁：保存提交前并发下架的新加入商品仍可能写入精选，前台按 `published()` 不显示它。「已在当前精选里」以锁定后读到的精选各行为准，等锁期间别人提交的移出会被看到。
- 验证到什么程度：人工逐条对照验收标准与 A08、Kelvin 2026-10-08 决定原句自查；新增与改写的测试文档字符串写明守住的是哪一句。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。未覆盖真 MySQL 上的锁等待与并发保存。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/store_design.py 的 save_store_design 在锁定设置单例行之后先读取当前精选各行的商品 ID，再校验请求的精选商品；每个 ID 要么此刻满足 published()（沿用 _published_ids），要么已在当前精选里；两者都不满足（含不存在的商品 ID）时照旧回滚并抛 featured_unavailable，不写入任何东西
      - 放行只看保存前的当前精选；已下架的商品被移出并保存后再加回、或从未在精选里的已下架商品加入，都按新加入处理而为 featured_unavailable；已在精选里的已下架商品可以调整位置、与其他设置的改动一起保存，保存后仍在精选里
      - 主题与主色的校验与顺序、锁定与事务、与当前完全相同时回滚并返回当前设置不写审计（现在含已下架商品的相同请求也是 200）、审计操作名与新旧值格式、接口字段与错误码都不变；模块说明与 save_store_design 文档字符串里关于精选校验的句子随之改写
      - tests/test_admin_store_design.py 只把 test_keeping_a_delisted_featured_product_is_422 改为验证放行（改名并写明原因）——原样再提交含已下架商品的精选为 200 且库里与审计都不变；只改主题时 200、已下架商品留在原位置、审计新值含它的 ID；调整它的位置可以保存；并新增——移出并保存后再加回为 featured_unavailable 且数据不变；现有 test_unavailable_featured_product_is_422（从未在精选里的商品）保持通过；每条测试写明守住的是 A08 原句或 Kelvin 2026-10-08 决定的哪一句；其余现有测试不改；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-061 记录段（规则变化、改动的现有测试、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置、app/api/ 与前端；不加表或迁移；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```
