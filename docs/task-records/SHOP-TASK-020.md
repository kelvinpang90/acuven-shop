# SHOP-TASK-020 游客下单 API

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 1、2、3、6、8 条，「数据模型」的 Order / OrderItem、OrderRecipient、PaymentAttempt / OrderEvent 三行，「失败、并发与重试」第 1 条，「边界与原则」第 4 条与「权限与资料保护」第 1、2、6、7 条，提供游客下单接口：`app/services/ordering.py`（下单与重放）、`app/api/orders.py`（接口，`app/main.py` 挂上路由）；`app/services/checkout.py` 只把 `_purchasable`、`_selected_options` 改名为 `purchasable`、`selected_options` 供下单复用（并改写模块说明里「整单校验留给游客下单任务」那句），计价接口与目录接口的行为未改，`tests/test_catalog_api.py`、`tests/test_checkout_quote.py`、`tests/test_purchase_limit.py` 未改；`app/services/catalog.py` 未改。测试见 `tests/test_guest_order.py`。未加表或迁移，未改前端、CI、部署配置或设计。设计闸门：DESIGN 1.11（提交 `2d13250`）。
- 请求（`POST /api/orders/guest`，不需登录，不读会员会话）：
  - 请求头 `Idempotency-Key`：必填，16 到 64 个字母、数字、连字符或下划线；只能出现一次。
  - 请求体（JSON，多出字段 422）：`lines`（每项 `sku`、`quantity`，规则与计价接口相同：1 到 20 行、每行 1 到 99 件、SKU 不重复，行模型直接复用计价接口的 `QuoteLineIn`）；`phone`（结账第 1 步的号码原文）与 `phone_region`（页面国家码选择对应的两位地区代码，如 `MY`）；`name`、`address`、`postal_code`（去掉首尾空白后非空）；`country_code`（两位大写字母，与 `app/services/shipping.py` 相同）；`state_code`（马来西亚必填且为 `MY-01` 到 `MY-16`，其他国家必须不给或为 null）；`region`（只有其他国家可给，去掉首尾空白后为空串时按没有地区保存；马来西亚给了即 422，含空串）。姓名、地址、邮编、地区的长度上限取自 `OrderRecipient` 的列长度（`OrderRecipient.__table__.c[...].type.length`），按去掉首尾空白后的字符数比较。请求里没有任何金额、会员或短信验证标记字段。
- 响应：新订单 201，重放 200；字段恰好为 `order_number`、`status`、`subtotal_sen`、`shipping_fee_sen`、`total_sen`、`payment_expires_at`（UTC，带 `Z`）、`csrf_token`（本次签发了授权时为由该 cookie 算出的 CSRF 令牌，否则为 null）；带 `Cache-Control: no-store`。不含收货资料、会话令牌或幂等键。签发授权时以 SHOP-TASK-019 的 `set_order_access_cookie` 设置 `__Host-shop_order_access`；请求带来的 cookie 对应有效会话时沿用该会话，并以原值重设 cookie 刷新 Max-Age（按 SHOP-TASK-019 的说明）。
- 处理顺序与错误码：
  1. 逐块读请求体，超过 8 KB（取自计价接口的 `MAX_BODY_BYTES`）即停止读取并返回 413，先于一切校验（含 Content-Type 与幂等键）。
  2. Content-Type 的媒体类型不是 `application/json`（允许带 `charset` 等参数）即 415。
  3. 幂等键与请求体一并校验，有错即 422；手机号用 `normalize_phone(phone, phone_region)` 规范化（以加号开头时以输入为准，不按收货国家重新解析），不成立时 422 且该项 `type` 为 `phone_invalid`、`loc` 为 `["body", "phone"]`。422 的 `detail` 每项只有 `type`、`loc`、`msg`（固定消息），不回显提交的内容。
  4. 按幂等键查订单：存在时指纹相同即重放（200），不同即 409 `{"detail": "idempotency_conflict"}`；都不重新计价、不动库存。
  5. 当次请求里用 `is_sms_verification_enabled` 读开关：关闭时一律放行；开启时用 `is_sms_whitelisted` 判定，白名单号码只有在 `verification_attempts` 里该号码有创建时间在最近 30 分钟内、用途 `checkout`、状态 `undeliverable` 或 `suspended` 的记录时放行，否则 403 `{"detail": "sms_verification_required"}`。
  6. 用 `quote_cart` 按收货国家与州属重新计价：运费行缺失 503 `{"detail": "shipping is unavailable"}`（与计价接口相同）；有任何不可购买、超出限购或库存不足的行即不可下单。
  7. 按 SKU 规格 ID 从小到大逐行 `UPDATE product_variants SET available_stock = available_stock - n WHERE id = ? AND available_stock >= n`，影响行数不是 1 即失败。
  8. 6 判定不可下单或 7 失败时回滚，再按幂等键查一次：有则按指纹重放或报冲突，没有才 409 `{"detail": "order_not_placeable"}`。
- 事务内步骤（全部成功才提交）：扣库存；写订单（状态 `awaiting_demo_payment`、会员 ID 空、认领状态 `open`、券折扣与积分抵扣与获得积分 0、运费与所用运费区代码和版本号、应付 = 小计 + 运费、幂等键与请求指纹、创建时间、支付到期时间 = 创建时间 + 15 分钟、支付时间空；订单号用 `generate_order_number`）；写入订单时撞上幂等键唯一约束即回滚并按 4 的规则重放或报冲突；订单行（按请求顺序的行序、SKU 规格外键与 SKU 快照、三语商品名称与规格说明、单价、件数、行小计）；`price_order`（不传券与积分）的逐件分摊快照；收货资料（姓名、规范化电话、国家、地区：马来西亚为州属代码、其他国家为地区文本或空、地址、邮编）；下单事件（迁移前状态空、迁移后 `awaiting_demo_payment`、操作者 `guest`）；`issue_order_access(..., "guest_checkout", now)`；提交后才设置 cookie。
- 幂等规则：请求指纹为下列内容的 JSON（键排序、无空白、非 ASCII 转义）的 SHA-256 十六进制：`lines`（按请求顺序的 `[sku, quantity]`）、规范化后的 `phone`、去掉首尾空白后的 `name`、`address`、`postal_code`、`country_code`、`state_code`、`region`（空串按 null）。手机号原文与 `phone_region` 不进指纹，所以同一号码换一种写法仍是同一请求。指纹用常量时间比较。重放时订单仍为 `awaiting_demo_payment` 且当前时间早于支付到期时间才签发新的 `guest_checkout` 授权并设 cookie；否则只返回订单信息，不设 cookie，`csrf_token` 为 null。
- 偏离与取舍，请审阅（未改设计，未发现设计本身必须停下的问题）：
  - 逐件分摊快照的「获得积分」写 0，没有照抄 `price_order` 算出的值：`price_order` 的 `points_earned` 是「模拟支付成功后应给的积分」，而第 3 条「游客不积累积分」，验收也要求订单的获得积分为 0；若逐件写非零值，逐件之和与订单的 0 不一致，之后的退款追回积分也会对游客订单误算。逐件的原价、券额、积分额与现金实付照 `price_order` 的结果写入。
  - 先按幂等键查订单，再做短信开关判定：按「失败、并发与重试」「相同键相同请求返回原结果」与「网络中断时先查询原订单」，已创建的订单在开关后来被打开时仍能重放；新订单照常在当次请求里读开关判定。
  - 「最近 30 分钟」按短信验证记录的创建时间判断（`created_at >= now - 30 分钟`，与表上的手机号 + 创建时间索引一致），不看更新时间。
  - 规格说明格式为「规格名: 规格值」按规格名的排列序号以「 / 」连接（中文用全角冒号，如「颜色：红色 / 尺码：M」），各部分按目录的回退英文规则取文案；无规格商品为空串；超过列长度（300）时截断，不让订单因说明过长而失败。商品名称直接按回退规则取（列长与商品表相同，不截断）。
  - 邮编按验收要求去掉首尾空白后不得为空；SHOP-TASK-010 模型说明里「没有邮编的国家存空串」的情形因此不会由本接口写入。
  - 错误码以 `{"detail": "<错误码>"}` 返回；`phone_invalid` 与其他请求格式错误一起放在 422 的 `detail` 列表里（`type` 为 `phone_invalid`），幂等键缺失或不合法也在该列表里（`loc` 为 `["header", "Idempotency-Key"]`）。
  - 413 先于 415：验收要求超限「先于一切校验」，所以先读完请求体（逐块判断）再看 Content-Type。
  - 校验通过后计价仍抛 `InvalidDestination` 时（理论上不会发生）返回 422，与计价接口一致。
  - UX P05 没有为字段定名；字段名按设计与模型取（`phone`、`phone_region`、`name`、`address`、`postal_code`、`country_code`、`state_code`、`region`），未发现与设计冲突之处。
  - 限流不在本任务：本接口目前没有任何限流，留给已登记的「公开接口限流（计价、下单）」任务。支付超时取消与库存释放、模拟支付与取消留给 SHOP-TASK-021 与 SHOP-TASK-031；会员下单（含优惠券与积分、认领状态 `not_claimable`）留给会员与优惠券积分任务。
  - 接口与服务都不写日志；异常与错误响应不含个人资料、订单号、幂等键或令牌。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查。`tests/test_guest_order.py` 用 TestClient 与 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开，会话依赖换成每个请求一个会话）按模型建表，自建目录、运费、汇率、站点设置与短信验证记录，每条测试的文档字符串写明它守住的设计原句或验收约定，覆盖：新订单 201 且响应只有七个字段、不含姓名、地址、电话、幂等键与令牌；订单各列（含应付 = 小计 + 运费、支付到期 = 创建 + 15 分钟、指纹等于按规范化请求算出的值）；订单行三语名称与规格说明（含回退英文）、逐件分摊（原价与现金实付之和等于商品小计，券、积分、获得积分为 0）；收货资料去空白、库存按件数扣减、下单事件一条；其他国家的地区文本与兜底运费、空白地区存空；Set-Cookie 的名称与属性、授权通过 `check_order_access`、CSRF 令牌由 cookie 算出；收货电话按请求地区规范化且不随收货国家改变（含加号优先）；开关关闭（含没有站点设置行）时马来西亚号码无记录也能下单；开关开启时无记录、记录超过 30 分钟、状态 sent 或 approved、用途不是 checkout、别的号码的记录都 403，最近 30 分钟内 undeliverable 或 suspended 放行，白名单外号码放行；不可购买、超出限购、库存不足都 409 且不建订单、不动库存、不签发凭据；两行中规格 ID 较大的第二行在扣库存前被并发请求扣到不够时，第一行库存不变；运费行缺失 503；同键同请求（电话写法与空白不同）重放同一订单、不再扣库存与写事件并重新设 cookie；同键不同请求 409；重放已过支付到期时间的订单不设 cookie、不新增授权；用第二个数据库会话在计价前先提交同键订单，使插入撞上唯一约束时回滚、库存不变、不多建订单，指纹相同重放该单、不同报 idempotency_conflict；同键的第一个请求已提交并把库存扣到不够时，第二个请求在计价或扣库存时失败都返回第一个请求的订单；415（缺失、text/plain、表单）与带 charset 的 JSON 接受；413（合法 JSON 带多余字段、缺幂等键、非 JSON）；422（缺幂等键、过短、过长、含非法字符、多余字段、马来西亚缺州属或州属不合法、其他国家给州属、马来西亚给地区、小写国家码、姓名地址邮编为空白、姓名地址邮编地区超过列长度、非法电话与非法地区代码、缺电话、重复 SKU、100 件）且响应不含提交的姓名、电话、地址；下单不产生 `app` 日志记录。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；下单的查询与带条件的 UPDATE 未在真 MySQL 上执行，真并发（两个连接、MySQL 的可重复读）只以上述 SQLite 交错模拟。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11 上述各条一致；接口字段参照 docs/UX.md 0.7 的 P05，与设计冲突时以设计为准并在 PR 里说明；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - 只有一个接口 POST /api/orders/guest；只接受 JSON（其他 Content-Type 返回 415）；请求体上限与计价接口相同（8 KB，按实际读到的字节逐块判断，超过即停止读取并返回 413，先于一切校验）；多出的字段一律 422；422 响应只指出出错的字段，不回显提交的姓名、电话、地址等内容
      - 请求头 Idempotency-Key 必填，16 到 64 个字符，只允许字母、数字、连字符与下划线，缺失或不合法 422
      - 请求体包含购物车行（规则与计价接口相同，经 SHOP-TASK-016 后每行 1 到 99 件、最多 20 行、SKU 不重复）、结账第 1 步的手机号原文与页面国家码选择对应的地区代码、收货人姓名、地址、邮编、收货国家；马来西亚时必填州属代码、其他国家时不给州属；其他国家时可选地区自由文本，马来西亚时不接受；国家与州属代码规则沿用 app/services/shipping.py；姓名、地址、邮编、地区去掉首尾空白后，姓名、地址、邮编不得为空，长度上限从 SHOP-TASK-010 订单收货资料模型的列长度读取，不另写数字
      - 手机号用 SHOP-TASK-011 的规范化函数按请求里的地区代码规范化为 E.164（以加号开头时以输入为准），不按收货国家重新解析；不成立返回 422（错误码 phone_invalid）；随后在当次请求里用 SHOP-TASK-022 的读取函数读取短信验证开关：开关关闭时不论号码是否属于白名单都放行（马新号码也以游客下单）；开关开启时，规范化结果用 SHOP-TASK-011 的白名单函数判定，属于白名单（马来西亚、新加坡）时，只有 SHOP-TASK-012 的短信验证记录里该号码最近 30 分钟内有用途为 checkout、状态为 undeliverable 或 suspended 的记录才放行，否则返回 403（错误码 sms_verification_required）；这一判定只在服务端做，不信任请求里的任何标记
      - 幂等的请求指纹是经校验与规范化后的请求内容（行按请求顺序、规范化后的电话、去掉首尾空白后的收货资料、国家、州属、地区）按固定规则序列化后的 SHA-256；先按幂等键查订单，指纹相同即重放、不同即 409（错误码 idempotency_conflict），都不重新计价、不动库存；并发插入同一幂等键时以唯一约束为准，回滚后按同样规则重放或报冲突
      - 新订单用计价接口的同一套服务（app/services/checkout.py）按收货国家与州属重新计价；不可下单（有任何不可购买、超出限购或库存不足的行）时返回 409（错误码 order_not_placeable），不建订单；再按 SKU 规格 ID 从小到大逐行用带条件的 UPDATE 扣减当日可用库存（可用库存不小于件数才扣），任何一行扣不到即回滚整个事务，不部分下单；超出限购按 DESIGN 第 8 条拒绝整单；无论是计价判定不可下单还是扣库存失败，回滚后都先按幂等键重新查一次订单（同键的并发请求可能已经提交），有则按指纹重放或报 idempotency_conflict，没有才返回 order_not_placeable
      - 同一事务里写入订单（状态 awaiting_demo_payment、会员 ID 为空、认领状态 open、券折扣与积分抵扣与获得积分都为 0、运费与所用运费区代码和版本号、应付、幂等键与请求指纹、创建时间、支付到期时间为创建时间加 15 分钟、支付时间为空）；订单行（按请求顺序的行序、SKU 规格外键与 SKU 快照、按 SHOP-TASK-005 回退英文规则取好的英文、中文、马来文商品名称与规格说明、单价、件数、行小计）；用 app/services/pricing.py 的 price_order（不传券与积分）算出的逐件分摊快照；收货资料（姓名、规范化电话、国家、地区即马来西亚的州属代码或其他国家的地区自由文本或空、地址、邮编）；下单事件（迁移前状态为空、迁移后 awaiting_demo_payment、操作者 guest）；再用 SHOP-TASK-019 的签发函数签发该单的 guest_checkout 授权并设置 cookie；全部成功才提交
      - 新订单返回 201，重放返回 200；响应只含订单号、状态、商品小计、运费、应付、支付到期时间与 CSRF 令牌，不含收货资料、会话令牌或幂等键；重放时订单仍为 awaiting_demo_payment 且未过支付到期时间才签发新的 guest_checkout 授权与 cookie，否则只返回订单信息、不设 cookie
      - 接口不需要登录，不读会员会话；不写日志，或日志只记录不含个人资料、订单号、幂等键与令牌的事件；错误响应不含内部细节；运费行缺失时与计价接口一样返回 503；限流不在本任务，PR 里如实说明
      - 如需复用 app/services/catalog.py 或 app/services/checkout.py 里的函数，只可把它们改为可供其他模块导入（改名或导出），不改目录与计价接口的任何行为，tests/test_catalog_api.py、tests/test_checkout_quote.py 与 tests/test_purchase_limit.py 不改动并照常通过
      - tests/test_guest_order.py 用 TestClient 与 SQLite 内存库（开启外键检查，覆盖会话依赖）按模型建表并自建目录、运费、汇率与短信验证记录数据，验证：成功下单后各表写入的值（含逐件分摊之和等于商品小计、应付等于小计加运费、支付到期时间为创建加 15 分钟、库存按件数扣减、事件一条）；收货电话按请求的地区代码规范化且不随收货国家改变；开关关闭（含站点设置表无行）时马新号码无短信验证记录也能下单；开关开启时，白名单号码无记录、记录超过 30 分钟、记录状态为 sent 或 approved、记录用途不是 checkout 时都 403，最近 30 分钟内有 undeliverable 或 suspended 的 checkout 记录时放行；不可购买、超出限购、库存不足都 409 且不建订单、不动库存；两行中第二行扣不到库存时第一行的库存也不变；同键同请求重放返回同一订单、不再扣库存，同键不同请求 409；插入订单时遇到同一幂等键的唯一约束冲突（用第二个数据库会话先写入同键订单，或可控地抛出 IntegrityError 模拟并发）时回滚、不重复扣库存、不多建订单，并按指纹返回重放结果或 idempotency_conflict；同键并发请求的另一交错（第一个请求已提交并用尽库存，第二个请求在计价或扣库存时失败）返回第一个请求的订单而不是 order_not_placeable；Set-Cookie 带 SHOP-TASK-019 规定的名称与属性，签发的授权可通过校验函数；重放已过支付到期时间的订单不设 cookie；415、413（含带多余字段的超限请求体）、422（缺幂等键、幂等键过短或含非法字符、多余字段、马来西亚缺州属、其他国家给州属、马来西亚给地区、姓名为空白、超过列长度、非法电话）且 422 响应不含提交的姓名、电话、地址；运费行缺失 503；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-020 记录段（请求与响应字段、错误码、事务内步骤、幂等规则、偏离、验证到什么程度，并写明支付超时取消、会员下单与限流留给之后的任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表、不加迁移、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```
