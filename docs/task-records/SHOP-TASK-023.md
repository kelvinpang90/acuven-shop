# SHOP-TASK-023 结账参考数据：地区与国家码列表、马来西亚州属列表

- [x] 提供结账页 P05 所需的只读参考数据接口：`app/services/regions.py`（数据）、`app/api/regions.py`（接口，`app/main.py` 挂上路由）；测试见 `tests/test_regions.py`。不读写数据库，未加表或迁移，未改运费与手机号规则（`app/services/shipping.py`、`app/services/phone.py` 未改），未做页面，未改前端、设计、CI、部署配置或 `pyproject.toml`。设计闸门：不适用（只读的地区参考数据，不涉及金额计算，不收集、存储或展示个人资料）。
- 接口 `GET /api/checkout/regions`：不需登录，不读写 cookie，不用会话依赖（未配置数据库时也照常 200）；其他方法 405。响应带 `Cache-Control: public, max-age=86400`，字段恰好为：
  - `regions`：`[{"code": "AC", "calling_code": 247}, ...]`，phonenumbers 支持的全部两位地区代码（`phonenumbers.SUPPORTED_REGIONS`，非地理编码如 +800、+882 不在其中）与 `country_code_for_region` 给出的国家呼叫码（整数），按地区代码排序。不含国家名称，前端按界面语言本地化。
  - `my_states`：`[{"code": "MY-01", "name": "Johor"}, ...]`，MY-01 到 MY-16 依次为 Johor、Kedah、Kelantan、Melaka、Negeri Sembilan、Pahang、Pulau Pinang、Perak、Perlis、Selangor、Terengganu、Sabah、Sarawak、Wilayah Persekutuan Kuala Lumpur、Wilayah Persekutuan Labuan、Wilayah Persekutuan Putrajaya；三种界面语言都用这一马来文官方名称（Kelvin 2026-10-01 决定），接口不看 `lang`。
  - `default_phone_region`：`"MY"`（结账第 1 步国家码默认 +60，DESIGN 1.11「权限与资料保护」），取自 `app/services/shipping.py` 的 `MALAYSIA`。
- 数据来源：地区与呼叫码来自已安装的 phonenumbers 元数据，随其版本变化；州属代码取自 `app/services/shipping.py` 的 `MY_STATE_CODES`（排序后与名称表按位置配对，不另立一套代码），名称表写在 `app/services/regions.py`；两者数目不一致时模块导入即失败。两份列表进程内各算一次。
- 偏离与取舍，请审阅（未改设计，未发现设计本身的问题）：
  - 验收标准只说「可按静态数据缓存」，缓存时长取一天（`public, max-age=86400`）；数据只在部署新代码或升级 phonenumbers 时变化。
  - 州属名称表放在新的 `app/services/regions.py`，而不是 `app/services/shipping.py`：后者不在本任务的可改路径内；代码仍只从 `MY_STATE_CODES` 取。
  - 接口路径在 `/api/checkout` 下，但用独立的路由模块，`app/api/checkout.py` 未改。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_regions.py` 用 TestClient（会话依赖换成一调用就抛错的替身），每条测试的文档字符串写明它守住的验收原句或设计约定，覆盖：响应只有三个字段、地区项不含名称；MY 与 SG 的呼叫码为整数 60 与 65；每项代码是两位大写字母、已排序、不重复，呼叫码都是整数；代码集合等于 `phonenumbers.SUPPORTED_REGIONS` 且呼叫码与 phonenumbers 相同；州属恰好 16 项、顺序与名称逐项相同；州属代码集合等于 `MY_STATE_CODES`；带 `lang=en|zh|ms` 时州属名称不变；默认地区为 MY 且在列表中、呼叫码 60；会话替身下与未配置数据库时都 200；带不带 cookie 结果相同、不设 cookie、写方法 405；`Cache-Control` 可公开缓存、不是 `no-store`。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 只有一个接口 GET /api/checkout/regions，不需要登录，不读写 cookie，不访问数据库；响应可按静态数据缓存
      - 响应的 regions 列出 phonenumbers 支持的全部两位地区代码（不含非地理编码），每项为地区代码与国家呼叫码（整数），按地区代码排序；国家名称不由接口提供（前端按界面语言本地化）
      - 响应的 my_states 按 MY-01 到 MY-16 的顺序列出马来西亚州属代码与马来文官方名称，三种界面语言都用这一名称（Kelvin 2026-10-01 决定）：Johor、Kedah、Kelantan、Melaka、Negeri Sembilan、Pahang、Pulau Pinang、Perak、Perlis、Selangor、Terengganu、Sabah、Sarawak、Wilayah Persekutuan Kuala Lumpur、Wilayah Persekutuan Labuan、Wilayah Persekutuan Putrajaya；代码集合与 app/services/shipping.py 的 MY_STATE_CODES 一致（从它取代码，名称表写在同一服务模块里，不另立一套代码）
      - 响应的 default_phone_region 为 MY（结账第 1 步国家码默认 +60，DESIGN 1.11「权限与资料保护」）
      - 代码放在 app/services/regions.py 与 app/api/regions.py，并在 app/main.py 注册；只 import 标准库、phonenumbers 与项目已有模块，不改 pyproject.toml
      - tests/test_regions.py 用 TestClient 验证：MY 与 SG 在列表中且呼叫码分别为 60 与 65；每项代码都是两位大写字母且已排序、不重复；州属列表恰好 16 项、顺序与名称如上、代码集合等于 MY_STATE_CODES；默认地区为 MY；接口不访问数据库（覆盖会话依赖为会抛错的替身）；每条测试写明它守住的是哪一句规则；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-023 记录段（响应字段、数据来源、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```
