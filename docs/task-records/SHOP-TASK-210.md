# SHOP-TASK-210 图片处理的像素尺寸与存储的键前缀改为可传参（供店铺标志图使用）

- [x] 依据：`docs/DESIGN.md` 1.20（提交 `a870f78`）「计价、优惠、积分与库存」第 15 条（标志的像素尺寸为宽与高各 40 到 4,000、且长边至少 200；展示图存为键为 `store-logos/<文件名>.webp` 的对象；其余检查、处理与存储照第 13 条）与第 13 条；`docs/HANDOFF.md` 0.84 的落实写法（两处都是只限关键字的参数，默认值即商品图片现在的取值；标志与商品图片共用同一把处理锁与同一组 R2 client；日志内容不变）。设计闸门：DESIGN 1.20（提交 `a870f78`）第 13、15 条与 HANDOFF 0.84。不做服务层、数据库、接口与前端，不改设计，不改调用方。
- [x] `app/services/image_processing.py`：
  - 新签名 `process_image(data: bytes, *, min_side: int = MIN_SIDE, min_long_side: int = MIN_SIDE) -> ProcessedImage`。
  - 尺寸判断（仍在 `Image.open` 之后、解码之前）：宽与高都在 `min_side` 到 `MAX_SIDE`（4,000）之间（含两端），且 `max(宽, 高) >= min_long_side`，否则 `ImageRejected`。
  - 新增模块常量 `LOGO_MIN_SIDE = 40`、`LOGO_MIN_LONG_SIDE = 200`；`MIN_SIDE`（200）、`MAX_SIDE`、`DISPLAY_MAX_SIDE`、`WEBP_QUALITY` 不变。
  - 其余判断与处理（格式、单帧、PNG 文本块上限、方向、颜色、透明、去元数据、长边 1,600、WebP 质量 85、模块级 `_process_lock`、异常一律转成 `ImageRejected`）不变；模块说明加上 DESIGN 1.20 第 15 条与 HANDOFF 0.84。
- [x] `app/services/image_store.py`：
  - 新签名 `put_image(settings, file_name, data, *, prefix: str = KEY_PREFIX) -> None`、`get_image(settings, file_name, *, prefix: str = KEY_PREFIX) -> bytes`、`delete_image(settings, file_name, *, prefix: str = KEY_PREFIX) -> None`；对象键为 `f"{prefix}{file_name}.webp"`。
  - 新增模块常量 `LOGO_KEY_PREFIX = "store-logos/"`；`KEY_PREFIX = "product-images/"` 不变。
  - 两个共用 client、超时与尝试次数、未配置时的行为（写入与读取抛 `ImageStoreUnavailable`，删除记 info 日志后返回）、日志内容（只记操作名、文件名与异常类名，不记前缀与键）不变；模块与三个函数说明里写死 `product-images/` 的句子改为按前缀说明。
- [x] 供 SHOP-TASK-211 至 213 的调用写法：
  - 处理：`process_image(data, min_side=LOGO_MIN_SIDE, min_long_side=LOGO_MIN_LONG_SIDE)`（常量取自 `app.services.image_processing`）。
  - 存储：`put_image(settings, file_name, data, prefix=LOGO_KEY_PREFIX)`、`get_image(settings, file_name, prefix=LOGO_KEY_PREFIX)`、`delete_image(settings, file_name, prefix=LOGO_KEY_PREFIX)`（常量取自 `app.services.image_store`）。
  - 商品图片的调用方（`app/services/admin_product_images.py`、`app/api/admin_product_images.py`、`app/api/product_images.py`）不传参数，行为不变，本任务没有改它们。
- [x] 测试（现有测试不改，每条新测试的文档字符串写明它守住的 DESIGN 1.20 第 15 条的句子）：
  - `tests/test_image_processing.py` 新增：`test_logo_constants`（40、200、4,000）；`test_logo_sizes_are_accepted`（传标志参数时 40×200 → 40×200、200×40 → 200×40、40×4000 → 16×1600、4000×40 → 1600×16）；`test_logo_out_of_range_sizes_are_rejected`（传标志参数时 39×200、100×199、40×4001 被拒，异常不带文本与来源）；`test_logo_sizes_without_parameters_follow_product_range`（不传参数时上面七个尺寸都被拒，含 40×200）。
  - `tests/test_image_store.py` 新增：`test_key_prefix_constants`；`test_logo_prefix_put_get_delete_use_logo_key`（传 `prefix=LOGO_KEY_PREFIX` 时 Stubber 核对写入、读取与删除的键为 `store-logos/<文件名>.webp`，写入与读取走读写 client、删除走删除 client）；`test_default_prefix_put_get_delete_use_product_key`（不传时三者的键为 `product-images/<文件名>.webp`）；`test_logo_prefix_failure_log_has_no_key`（标志前缀写入失败的日志只含操作名、文件名与异常类名，不含 `store-logos/`）。
- 偏离与取舍：
  - 长边下限单独一条判断，写在宽高范围判断之后，两者都在解码之前；商品图片的默认值 `min_long_side = MIN_SIDE` 在宽高都不小于 200 时恒成立，所以商品图片的结果不变。
  - 不对 `min_side`、`min_long_side`、`prefix` 的取值做校验，取值由调用方用模块常量传入。
  - 存储测试另加了标志前缀写入失败时的日志核对与两个前缀常量的取值核对。
- 验证范围：上面列出的是本次新增的测试代码，样本都在内存里生成、R2 用 Stubber 替代，不访问网络；结果由 PR 的必需 CI 检查 backend 执行。

## 登记时的验收标准（原文）

2026-11-06 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 图片处理——process_image(data, *, min_side=MIN_SIDE, min_long_side=MIN_SIDE)；尺寸判断改为宽与高都在 min_side 到 MAX_SIDE（4,000）之间、且 max(宽, 高) 不小于 min_long_side，仍在解码之前判断；新增模块常量 LOGO_MIN_SIDE = 40 与 LOGO_MIN_LONG_SIDE = 200；其余判断与处理（格式、单帧、PNG 文本块上限、方向、颜色、透明、去元数据、长边 1,600、WebP 质量 85、模块级处理锁、异常一律转成 ImageRejected）不变；模块说明引用 DESIGN 1.20 第 15 条
      - 存储——put_image、get_image、delete_image 各加只限关键字的 prefix 参数，默认值为 KEY_PREFIX（product-images/），对象键为 prefix 加文件名加 .webp；新增模块常量 LOGO_KEY_PREFIX = store-logos/（写成 Python 字符串）；两个共用 client、超时与尝试次数、未配置时的行为、日志内容不变；函数与模块说明里写死 product-images/ 的句子改为按前缀说明
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——tests/test_image_processing.py 新增——用标志的参数时 40×200、200×40、40×4000、4000×40 合格（并核对缩放后的展示图尺寸，长边超过 1,600 等比缩到 1,600）；39×200、100×199（长边不足 200）、40×4001 不合格；同样这些图不传参数时按商品图片的 200 到 4,000 判断（40×200 不合格）；现有测试不改。tests/test_image_store.py 新增——传 prefix=LOGO_KEY_PREFIX 时写入、读取、删除用的键都以 store-logos/ 开头，不传时仍为 product-images/；现有测试不改。每条新测试写明守住的 DESIGN 1.20 第 15 条的句子
      - docs/task-records/SHOP-TASK-210.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明两个函数的新签名与常量，供 SHOP-TASK-211 至 213 使用；验证到什么程度，只写实际存在的测试）
      - 改动只落在 allowed_change_paths；不改调用方（app/services/admin_product_images.py、app/api/admin_product_images.py、app/api/product_images.py）、模型与迁移、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.20 提交 a870f78 与 HANDOFF 0.84），不合并、不直推
```
