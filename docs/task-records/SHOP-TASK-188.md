# SHOP-TASK-188 商品图片的校验与处理（Pillow，按内容判断、转正、去元数据、缩放、转 WebP）

- [x] 依据：`docs/DESIGN.md` 1.19（提交 `6e8f655`）「计价、优惠、积分与库存」第 13 条「上传」一段里按文件内容的判断与「处理」一段；`docs/HANDOFF.md` 0.79 的落实写法（同一进程同时只处理一张，用模块级的 `threading.Lock` 包住整个处理；PNG 文本块总量上限在模块导入时设为 1 MB；16 位灰度按除以 256 换算成 8 位；ICC 配置文件无法解析时按图片不合格处理；处理中抛出的任何异常都转成图片不合格；函数是同步函数，由调用方放进线程）。设计闸门：DESIGN 1.19（提交 `6e8f655`）第 13 条与 HANDOFF 0.79。本任务只做校验与处理，供 SHOP-TASK-189、190 的上传调用；不做存储、数据库与接口，不改设计。
- [x] 新增 `app/services/image_processing.py`：
  - `process_image(data: bytes) -> ProcessedImage`：`ProcessedImage` 为 frozen dataclass，字段 `data`（展示图的 WebP 字节）、`source_format`（`jpeg`、`png` 或 `webp`）、`width` 与 `height`（展示图的像素宽高）。不合格抛 `ImageRejected`（不带异常文本、`raise … from None`，本模块自己抛的也一并换成新的）。整个处理在模块级的 `_process_lock`（`threading.Lock`）内进行。
  - 模块导入时 `PngImagePlugin.MAX_TEXT_MEMORY = 1_048_576`，`MAX_TEXT_CHUNK` 不改。
  - 判断（顺序照设计，前一项不通过即拒绝）：`Image.open` 的 `format` 须为 JPEG、MPO、PNG 或 WEBP（MPO 记为 `jpeg`，只取第一帧）；MPO 以外 `n_frames` 大于 1 即拒绝；用 `Image.open` 给出的宽与高判断都在 200 到 4,000 之间（含两端），之后才解码。
  - 处理：解码前记下 tile 的 rawmode；JPEG 与 MPO 长边超过 1,600 时以 `draft(None, 目标尺寸)` 缩小解码（目标尺寸按未转正的宽高等比缩到长边 1,600 算出）；`load()` 后 `ImageOps.exif_transpose`；修正透明值、分出透明通道与颜色部分、按 ICC 转到 sRGB 或直接转 RGB、有透明通道时 `Image.merge` 成 RGBA；长边超过 1,600 时以 LANCZOS 等比缩到 1,600（短边四舍五入、至少 1，按整数运算）；用 `Image.frombytes` 把像素复制到不带 info 的新图，`save(format="WEBP", quality=85, lossless=False)`。
  - 不写磁盘；`Image.open`、解码、转换与编码中抛出的任何 `Exception` 都转成 `ImageRejected`。
- [x] 各色彩模式的转换取舍：
  - 透明值修正（在任何模式转换之前）：rawmode 为 `RGB;16B` 时把 `transparency` 三个值各右移 8 位；为 `L;2`、`L;4` 时分别乘 85 与 17。
  - 模式 `1`：先 `convert("L")`（Pillow 一并换算 `transparency`，不另修正），之后按 L 处理。
  - 带透明通道的：RGBA、LA、PA 取自带的 A；P、RGB 带 `transparency` 时先转 RGBA，L 带 `transparency` 时先转 LA，再取 A。
  - 16 位灰度（`I;16`、`I;16B`、`I;16L`、`I;16N` 先转成 `I`，以及 `I`）：带 `transparency` 时用 `ImageMath.lambda_eval` 逐像素比较原 16 位值（等于该值为 0、其余为 255）；灰度用 `ImageMath` 的 I 模式整数除法除以 256 再转 L（65535 → 255）。`point` 在 I 模式只做线性映射，比较做不出来，所以两处都用 `ImageMath`。
  - 颜色部分：RGBA、P、PA 经 RGBA 转 RGB；LA 取 L 通道；RGB、CMYK、L 原样。上面没列到的模式（三种格式解码后不会出现）直接转 RGB。
  - ICC：取转正后 info 里的 `icc_profile`（`None` 即没有；有值就解析，空字节解析失败即不合格）。配置文件的色彩空间（`profile.xcolor_space`）须与颜色部分的模式对应（RGB ↔ `RGB`、CMYK ↔ `CMYK`、L ↔ `GRAY`），否则不合格；对应时 `ImageCms.profileToProfile(颜色部分, 配置文件, sRGB, outputMode="RGB")`。没有 ICC 的直接 `convert("RGB")`（CMYK 用 Pillow 默认换算）。
- [x] 测试：新增 `tests/test_image_processing.py`，样本都用 Pillow 现场生成，16 位 RGB 与 2、4 位灰度 PNG 用 `struct` 与 `zlib` 拼出字节，I;16 样本用 `Image.frombytes` 生成；每条测试的文档字符串写明它守住的第 13 条原句。覆盖任务给出的全部期望值（EXIF 方向 6 加 GPS 的 3000×1000 JPEG → 533×1600 且结果 info 没有 exif、icc_profile、xmp；RGBA、调色板带透明、LA、I;16、不带 ICC 的 CMYK、带 sRGB ICC 的 PNG；两帧 MPO 记为 jpeg；4000×200 → 1600×80；4000×4000 → 1600×1600；结果为 WEBP 且不含 VP8L；RGB 带透明与 ICC、L、I;16、模式 1、16 位 RGB、2 位与 4 位灰度的透明测试；GIF、BMP、TIFF，两帧的动画 WebP 与 APNG，199×300 与 4001×300（另加 300×199、300×4001）被拒、200×200 被接受；两个 700 KiB zTXt 被拒、一个被接受；截断的 PNG 与空字节被拒；先持有 `_process_lock` 时另一线程 0.2 秒内不结束，释放后正常返回）。颜色各分量允许差 2，透明度须相等。
- 偏离与取舍：
  - ICC 配置文件与颜色部分的模式不符时，先按 `xcolor_space` 显式判断为不合格，不只依赖 littlecms 建转换时报错。
  - 缩放尺寸按缩放时图像的实际宽高算（draft 缩小过的 JPEG 按缩小后的宽高等比算），与任务「之后仍按下面的缩放规则缩到长边 1,600」的写法一致；draft 的缩小比例是整数，算出的短边与按原图算的至多差 1 像素。
  - 测试另加：已能接受 WebP（记为 `webp`）；用替身包住 `JpegImageFile.draft`，核对 4000×4000 请求 1600×1600、缩小解码为 2000×2000，3000×1000 请求 1600×533、仍为 3000×1000，长边不超过 1,600 的 JPEG 不调用 draft；`MAX_TEXT_MEMORY` 为 1,048,576；拒绝时异常不带文本、不带 `__cause__`。调色板与 MPO 的测试只核对模式、透明度与格式，不核对有损编码后纯色的颜色值。
- 验证范围：上面列出的是本次新增的测试代码，全部在内存里生成样本、不写磁盘；结果由 PR 的必需 CI 检查 backend 执行。

## 登记时的验收标准（原文）

2026-11-02 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 入口——process_image(data) 返回 ProcessedImage（frozen dataclass，字段 data 为展示图的 WebP 字节、source_format 为 jpeg、png 或 webp、width 与 height 为展示图的像素宽高），不合格抛 ImageRejected（不带异常文本）；整个处理在模块级的 _process_lock（threading.Lock）内进行，同一进程同时只处理一张
      - 判断（顺序照设计，前一项不通过即抛 ImageRejected，不往下）——Image.open 后 format 须为 JPEG、MPO、PNG 或 WEBP（GIF、BMP、TIFF 等一律拒绝），MPO 只取第一帧、记为 jpeg；MPO 以外 n_frames 大于 1 即拒绝（动画 WebP 与 APNG）；在解码像素之前，用 Image.open 给出的宽与高判断都在 200 到 4,000 之间（含两端）；模块导入时把 PngImagePlugin.MAX_TEXT_MEMORY 设为 1,048,576
      - 处理——JPEG 与 MPO 的长边超过 1,600 时，解码前以 draft 按缩到 1,600 后的尺寸缩小解码（请求尺寸按 Image.open 给出的未转正宽高等比算出；draft 只选不小于请求尺寸的缩小比例，之后仍按下面的缩放规则缩到长边 1,600，例如 3000×1000 的样本 draft 后仍为 3000×1000）；解码后按 EXIF 方向转正（ImageOps.exif_transpose）；透明与颜色分开处理——先修正透明值（在下面任何模式转换之前）：Image.open 后、解码前记下 tile 的 rawmode，为 RGB;16B（16 位 RGB，Pillow 把像素取高 8 位而透明色仍是 16 位值）时把 info 里 transparency 的三个值各右移 8 位（与透明色高 8 位相同的像素都变透明），为 L;2 或 L;4（2 位与 4 位灰度，Pillow 把像素放大到 0 到 255 而透明值仍是原位深的值）时把 transparency 分别乘 85 与 17；1 位黑白（模式 1）先转成 L（Pillow 一并换算 transparency，不另修正），之后按 L 处理；再取透明通道：RGBA、LA、PA 取自带的；P、RGB、L（含上面修正过的）的 info 里有 transparency 时先分别转成 RGBA、RGBA、LA 再取；16 位灰度（I;16 一类与 I）带 transparency 时按原 16 位值算（等于该值的像素为 0、其余为 255，用 ImageMath 逐像素比较，point 在 I 模式只做线性映射），灰度再按除以 256 换算成 L；其余没有透明通道。颜色部分为 RGB（RGBA、P、PA 经 RGBA 取颜色）、L（LA 取灰度）或原样的 RGB、CMYK、L；带 ICC 配置文件的经 ImageCms.profileToProfile 把颜色部分按它直接转到 sRGB、输出 RGB（嵌入的 CMYK 或灰度配置文件只能作用于对应的原模式；配置文件无法解析或与颜色部分的模式不符即不合格），不带的直接转成 RGB（CMYK 用 Pillow 默认换算）；有透明通道时接回成 RGBA，否则为 RGB；长边超过 1,600 时以 LANCZOS 等比缩到 1,600（短边四舍五入、至少 1）；把像素复制到不带任何 info 的新图上再编码，展示图不含 EXIF、ICC 与 XMP；编码为有损 WebP、quality 85
      - 不合格——Image.open、解码、转换或编码中抛出的任何异常（截断的数据、空字节、文本块超限、无法解析的 ICC 等）都转成 ImageRejected，不留任何文件（本模块不写磁盘）
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_image_processing.py，样本在测试里用 Pillow 现场生成，每条测试的文档字符串写明它守住的 DESIGN 1.19 第 13 条原句。以下期望值已由运营者用 Pillow 13 实跑核对——3000×1000 的 JPEG 带 EXIF 方向 6 与 GPS 信息，结果为 533×1600、source_format 为 jpeg、结果的 info 里没有 exif、icc_profile 与 xmp；300×300 的 RGBA PNG 像素 (0, 0, 255, 100)，结果为 RGBA 且该像素仍为 (0, 0, 255, 100)；带 transparency 的调色板 PNG 与 LA 图的结果为 RGBA；300×300、值全为 32768 的 I;16 PNG，结果转成 RGB 后像素为 (128, 128, 128)；不带 ICC 的 CMYK JPEG 结果为 RGB；带 sRGB ICC 的 PNG 结果的 info 没有 icc_profile；两帧的 MPO 被接受、记为 jpeg；4000×200 的 PNG 结果为 1600×80；4000×4000 的 JPEG 结果为 1600×1600；结果字节的格式为 WEBP 且不含 VP8L（有损）；透明与 ICC——300×300、左半为 (1, 2, 3)、右半为 (10, 20, 30) 的 RGB PNG 以 (1, 2, 3) 为 transparency 并带 sRGB ICC，结果为 RGBA，(20, 150) 的透明度为 0、(280, 150) 为 (10, 20, 30, 255)；左半 7、右半 50 的 L PNG 以 7 为 transparency，(20, 150) 为 (7, 7, 7, 0)、(280, 150) 为 (50, 50, 50, 255)；左半 1000、右半 32768 的 I;16 PNG（用 Image.frombytes 生成，paste 对 I;16 不可靠）以 1000 为 transparency，(20, 150) 为 (3, 3, 3, 0)、(280, 150) 为 (128, 128, 128, 255)；(60, 100) 的 LA 图结果为 RGBA、像素为 (60, 60, 60, 100)；左半 0、右半 1 的模式 1 PNG 以 0 为 transparency，(20, 150) 的透明度为 0、(280, 150) 为 (255, 255, 255, 255)；左半 (1000, 2000, 3000)、右半 (40000, 50000, 60000) 的 16 位 RGB PNG（Pillow 写不出 16 位 RGB，测试按 PNG 格式用 struct 与 zlib 拼出字节）以 (1000, 2000, 3000) 为 transparency，(20, 150) 的透明度为 0、(280, 150) 为 (156, 195, 234, 255)；左半 1、右半 3 的 2 位灰度 PNG 以 1 为 transparency，与左半 5、右半 15 的 4 位灰度 PNG 以 5 为 transparency（同样用 struct 与 zlib 拼出字节），(20, 150) 都为 (85, 85, 85, 0)、(280, 150) 都为 (255, 255, 255, 255)。颜色比较时各分量与期望差不超过 2（有损编码），透明度须相等
      - 测试验证意图（续）——拒绝的样本都抛 ImageRejected——GIF、BMP、TIFF；两帧的动画 WebP 与 APNG；199×300 与 4001×300 的 PNG（200×200 被接受）；两个各 700 KiB 的 zTXt 文本块（总量超过 1 MB）的 PNG（只有一个 700 KiB 文本块的被接受）；截断的 PNG 字节与空字节。并发——测试先持有 _process_lock，在另一线程调用 process_image，0.2 秒内不结束，释放后正常返回
      - docs/task-records/SHOP-TASK-188.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明各种色彩模式的转换取舍与偏离）
      - 改动只落在 allowed_change_paths；不改模型与迁移、现有测试、前端、.platform/、CI 与部署配置，不装依赖（Pillow 已在 pyproject.toml），不改设计
```
