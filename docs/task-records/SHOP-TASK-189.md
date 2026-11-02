# SHOP-TASK-189 商品图片的上传入库、调序与移除规则（服务层）

- [x] 依据：`docs/DESIGN.md` 1.19（提交 `6e8f655`）「计价、优惠、积分与库存」第 13 条「存放」「排列与移除」两段与「上传」一段的「图片已满」；`docs/HANDOFF.md` 0.79 的落实写法（三个改动函数都先回滚此前的事务，再以 SHOP-TASK-184 的 `lock_product_statement` 锁商品行；存储经 `image_store` 模块属性调用；审计对象类别为 product、以商品 ID 记录；调序时顺序未变不写库、不写审计；移除或调序时请求的图片不属于该商品即「图片已被改动」；移除后其余图片的排列序号不重排）。设计闸门：DESIGN 1.19（提交 `6e8f655`）第 13 条与 HANDOFF 0.79。本任务只做服务层，把 SHOP-TASK-188 的处理结果经 SHOP-TASK-187 的存储写入 R2 并记入 `ProductImage`；不做接口与前端，不改模型、迁移与设计。
- [x] 新增 `app/services/admin_product_images.py`：
  - `load_product_images(db: Session, product_id: int) -> list[AdminProductImage] | None`：该商品的图片（SHOP-TASK-183 的 `AdminProductImage`，按 `sort_order`、再按 `id`）；商品不存在返回 None；只发 SELECT。
  - `add_image(db: Session, settings: Settings, admin_account_id: int, product_id: int, image: ProcessedImage, now: datetime) -> list[AdminProductImage]`：文件名 `secrets.token_hex(16)`；`db.rollback()` 后在任何事务之外调用 `image_store.put_image`（失败即 `ImageStoreUnavailable` 原样抛出，不插入行）；再锁商品行（不存在 `ProductMissing`）、数图片（已有 8 张 `ImagesFull`）；插入 `ProductImage`（`storage_ref` 为 `/api/product-images/<文件名>.webp`，`sort_order` 为现有最大值加一、没有图片时为 0，`file_name`、`source_format`、`width`、`height` 取自处理结果，`byte_size` 为 `len(image.data)`）与一条审计，提交。锁定、计数、插入、审计与提交途中的任何异常都先回滚，再调用 `image_store.delete_image` 删除刚写入的对象（`delete_image` 只尝试 1 次、失败只记日志），然后重新抛出。
  - `reorder_images(db: Session, admin_account_id: int, product_id: int, image_ids: Sequence[int], now: datetime) -> list[AdminProductImage]`：回滚、锁商品行（不存在 `ProductMissing`）；`image_ids` 不恰好是该商品现有的全部图片 ID（缺、多、重复、属于别的商品）即 `ImagesChanged`；顺序未变即回滚并返回列表；否则把这些行的 `sort_order` 依次改为 -1、-2…并 flush，再改为 0、1…并 flush，写一条审计后提交。不调用 `image_store`。
  - `remove_image(db: Session, settings: Settings, admin_account_id: int, product_id: int, image_id: int, now: datetime) -> list[AdminProductImage]`：回滚、锁商品行（不存在 `ProductMissing`）；该 ID 的图片不存在或不属于该商品即 `ImagesChanged`；否则删除行、写一条审计、提交；提交之后，该行的 `file_name` 不为空时调用 `image_store.delete_image`，为空（示例数据的占位图）时不调用。其余图片的 `sort_order` 不重排。
  - 异常类 `ProductMissing`、`ImagesFull`、`ImagesChanged` 定义在本模块；存储不可用沿用 `image_store.ImageStoreUnavailable`。三个改动函数成功时都返回提交后的图片列表（在提交前于同一事务里读出），都不改商品的 `version`；写入途中的任何异常都先回滚再抛出。
- [x] 审计写法：都经 `record_audit`，`occurred_at` 取传入的 `now`，`admin_account_id` 取传入值，对象类别 `product`（`admin_products.AUDIT_TARGET_PRODUCT`）、对象 ID 为商品 ID：
  - `admin_product_image_added`：新值为新图片的 ID。
  - `admin_product_images_reordered`：旧值与新值为调序前后的图片 ID 序列，英文逗号分隔、不带空格（如 `1,2,3` → `3,1,2`）；8 个 INT 范围的 ID 至多 87 个字符，在审计值的 255 字符上限之内。
  - `admin_product_image_removed`：旧值为被移除图片的 ID。
- [x] 测试：新增 `tests/test_admin_product_images.py`，写法照 `tests/test_admin_products.py`（SQLite 内存库、StaticPool，经 `create_product` 造商品，占位图直接插入）；`ProcessedImage` 直接构造，不经 Pillow；以 monkeypatch 替换 `app.services.image_store` 的 `put_image` 与 `delete_image` 并记录调用。每条测试的文档字符串写明它守住的第 13 条原句（或 HANDOFF 0.79 的写法）。覆盖：读取的排序、商品不存在为 None、只发 SELECT；上传插入一行（字段与 `storage_ref`、文件名为 32 位小写十六进制）、写一条审计、排列序号为最大值加一（含没有图片时为 0、现有序号有空缺时）、商品 version 不变；写入失败不插入行；第 9 张为 `ImagesFull`、删除了刚写入的对象、库里仍为 8 张；商品不存在、提交失败与审计被拒时都删除了该对象；调序成功后的顺序、排列序号与审计的旧值新值，且不调用存储；列出的 ID 缺、多、重复、属于别的商品或为空都是 `ImagesChanged` 且顺序不变；顺序未变不写库、不写审计；移除删除行、写审计，替换的 `delete_image` 里另开会话查库时该行已不存在；占位图移除不调用 `delete_image`；移除后第一张为排列序号最小者、其余序号不重排；移除别的商品的图片或不存在的 ID 为 `ImagesChanged`；调序与移除时商品不存在为 `ProductMissing`；锁定查询按 MySQL 方言编译带 `FOR UPDATE`，三个改动函数回滚之后的第一条语句都是它，之后不再加锁。
- 偏离与取舍：
  - `add_image` 的提交也放在 try 里：设计要求「提交失败即删除该对象」，提交抛出时同样先回滚、再删对象后重新抛出。返回的列表在提交前于同一事务里读出（与 `create_product` 的写法一致），这样提交之后不再有可能失败的查询把已入库的图片当成失败去删对象。
  - 设计写「数一次图片」：计数与最大序号取自同一条查询（该商品全部图片的 `sort_order`），不另发一条 COUNT。
  - 商品 ID 与图片 ID 不是 INT 范围内的整数（含 bool）时直接按不存在处理（商品为 `ProductMissing`，移除的图片为 `ImagesChanged`），不拿去比较，沿用 `admin_products` 的 `MAX_ROW_ID` 口径；`add_image` 里这一判断仍在写入 R2 之后、按「商品不存在即删除对象」处理，保持契约规定的先写存储再锁商品行的顺序。
  - 调序时 `image_ids` 里有非整数（含 bool）也按「不恰好是现有的全部 ID」处理为 `ImagesChanged`，免得 `True` 与 ID 1 在集合比较时相等。
  - 图片行不另加锁：图片只经本模块改动，商品行已锁，改动在商品行上排队；读图片行时带 `populate_existing`，免得会话里留着旧值。
  - 移除时 `ImagesChanged` 之前不调用存储；`delete_image` 在提交之后、try 之外调用，它本身不抛出（未配置或失败都只记日志）。
- 验证范围：上面列出的是本次新增的测试代码，全部替换存储函数、不访问 R2；结果以 PR 的必需 CI 检查 backend 为准。

## 登记时的验收标准（原文）

2026-11-02 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 读取——load_product_images(db, product_id) 返回该商品的图片列表（SHOP-TASK-183 的 AdminProductImage，按排列序号、再按 id 排序），商品不存在返回 None；不写库。三个改动函数成功时都返回提交后的这个列表。异常类 ProductMissing（商品不存在）、ImagesFull（已有 8 张）、ImagesChanged（图片已被改动）定义在本模块，存储不可用沿用 image_store.ImageStoreUnavailable
      - 上传——add_image(db, settings, admin_account_id, product_id, image, now)，image 为 ProcessedImage。依次为——文件名用 secrets.token_hex(16)；先 db.rollback() 结束此前的读事务，在任何事务之外调用 image_store.put_image（失败即抛出 ImageStoreUnavailable，不插入行）；再锁商品行，商品不存在即回滚、删除刚写入的对象、抛 ProductMissing；数该商品的图片，已有 8 张即回滚、删除该对象、抛 ImagesFull；否则插入 ProductImage（storage_ref 为 /api/product-images/<文件名>.webp，sort_order 为现有最大值加一、没有图片时为 0，file_name、source_format、width、height 取自处理结果，byte_size 为展示图的字节数）与一条审计，提交；插入、审计或提交途中出现任何异常即回滚、删除该对象后重新抛出。删除对象都经 image_store.delete_image（只尝试 1 次，失败只记日志）
      - 调序——reorder_images(db, admin_account_id, product_id, image_ids, now)。回滚、锁商品行，商品不存在抛 ProductMissing；image_ids 不是恰好该商品现有的全部图片 ID（缺、多、重复或属于别的商品）即回滚、抛 ImagesChanged；顺序未变即回滚、返回列表；否则先把这些行的 sort_order 依次改为 -1、-2…并 flush，再改为 0、1…并 flush（避开唯一约束的中间冲突），写一条审计后提交；不碰 R2
      - 移除——remove_image(db, settings, admin_account_id, product_id, image_id, now)。回滚、锁商品行，商品不存在抛 ProductMissing；该 ID 的图片不存在或不属于该商品即回滚、抛 ImagesChanged；否则删除行、写一条审计、提交；提交之后，该行的 file_name 不为空时调用 image_store.delete_image（存储未配置时它只记日志），为空（示例数据的占位图）时不调用
      - 三个改动函数都不改商品的 version；写入途中出现任何异常都先回滚再抛出；审计的 occurred_at 取传入的 now，admin_account_id 取传入值
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_admin_product_images.py（写法照 tests/test_admin_products.py，经 create_product 造商品；ProcessedImage 直接构造，不经 Pillow；用 monkeypatch 替换 app.services.image_store 的 put_image 与 delete_image 并记录调用），每条测试的文档字符串写明它守住的 DESIGN 1.19 第 13 条原句——上传成功插入一行（字段与 storage_ref）、写一条审计、排列序号为最大值加一、商品 version 不变；写入失败不插入行；第 9 张时 ImagesFull 且删除了刚写入的对象、库里仍为 8 张；商品不存在时删除了该对象；提交失败时删除了该对象；调序成功后顺序与审计的旧值新值；列出的 ID 缺、多、重复或属于别的商品都是 ImagesChanged 且顺序不变；顺序未变不写审计；移除删除行、写审计、提交后才调用 delete_image（替换的 delete_image 里查库，该行已不存在）；占位图移除不调用 delete_image；移除后第一张为排列序号最小者；锁定查询按 MySQL 方言编译带 FOR UPDATE，三个改动函数回滚之后的第一条语句都是它
      - docs/task-records/SHOP-TASK-189.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明函数签名、审计写法与偏离取舍）
      - 改动只落在 allowed_change_paths；不改模型与迁移、app/services/admin_products.py、app/services/image_store.py、app/services/image_processing.py、现有测试、前端、.platform/、CI 与部署配置，不装依赖，不改设计
```
