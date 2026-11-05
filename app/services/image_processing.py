"""图片的校验与处理：按内容判断格式、帧数与尺寸，转正、去元数据、缩放并编码为 WebP 展示图。

商品图片与店铺标志图共用本模块（同一把处理锁），两者只有尺寸下限不同。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条「上传」一段里按文件
内容的判断（格式须为 JPEG（含 MPO，只取主图）、PNG 或 WebP；只收单帧静态图；宽与高都在 200 到
4,000 像素之间，先读尺寸、合格再解码；PNG 文本块解压后的总量上限 1 MB）与「处理」一段（JPEG 先按
目标尺寸缩小解码；按 EXIF 方向转正；转成 8 位 RGB 或 RGBA，带 ICC 配置文件的按它转成 sRGB；去掉
全部元数据；长边超过 1,600 像素时等比缩到 1,600；编码为有损 WebP、质量 85）。以及 docs/HANDOFF.md
0.79 的落实写法：同一进程同时只处理一张（模块级的 threading.Lock 包住整个处理）；文本块总量上限在
模块导入时设定；16 位灰度按除以 256 换算成 8 位；ICC 配置文件无法解析时按图片不合格处理；处理中
抛出的任何异常都转成图片不合格。

店铺标志图依据 docs/DESIGN.md 1.20（提交 a870f78）同一节第 15 条：像素尺寸为宽与高都在 40 到
4,000 像素之间、且长边至少 200 像素，其余判断与处理同第 13 条（同一个「同一进程同时只处理一张」
的排队）。按 docs/HANDOFF.md 0.84 的落实写法，尺寸下限是 process_image 只限关键字的参数，默认值
即商品图片的取值；标志的调用方传 LOGO_MIN_SIDE 与 LOGO_MIN_LONG_SIDE。

process_image 是同步函数，由调用方放进线程；本模块不写磁盘，不碰存储、数据库与接口
（SHOP-TASK-189、190、211 至 213）。
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass

from PIL import Image, ImageCms, ImageMath, ImageOps, PngImagePlugin

# PNG 文本块解压后的总量上限（单块上限 MAX_TEXT_CHUNK 沿用默认）；Pillow 在读块时按模块变量判断。
PngImagePlugin.MAX_TEXT_MEMORY = 1_048_576

# Image.open 给出的格式 → 记录的上传格式；MPO 只取第一帧，按 JPEG 记录。
_ACCEPTED_FORMATS = {"JPEG": "jpeg", "MPO": "jpeg", "PNG": "png", "WEBP": "webp"}

MIN_SIDE = 200
MAX_SIDE = 4000
# 店铺标志图：宽与高各至少 40，长边至少 200（横条标志的高度常不足 200）。
LOGO_MIN_SIDE = 40
LOGO_MIN_LONG_SIDE = 200
DISPLAY_MAX_SIDE = 1600
WEBP_QUALITY = 85

_GRAY16_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N"})
# 2 位与 4 位灰度：Pillow 把像素放大到 0–255，透明值仍是原位深的值。
_LOW_BIT_GRAY_FACTORS = {"L;2": 85, "L;4": 17}
# 颜色部分的模式 → ICC 配置文件须有的色彩空间。
_PROFILE_SPACES = {"RGB": "RGB", "CMYK": "CMYK", "L": "GRAY"}

_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


class ImageRejected(Exception):
    """图片不合格（格式、帧数、尺寸，或打开、解码、转换、编码出错）。不带异常文本。"""


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes  # 展示图的 WebP 字节
    source_format: str  # jpeg、png 或 webp
    width: int  # 展示图的像素宽
    height: int  # 展示图的像素高


# 同一进程同时只处理一张，其余在这把锁上排队。
_process_lock = threading.Lock()


def process_image(
    data: bytes, *, min_side: int = MIN_SIDE, min_long_side: int = MIN_SIDE
) -> ProcessedImage:
    """校验并处理上传的图片字节，返回展示图；不合格抛 ImageRejected。

    宽与高都须在 min_side 到 MAX_SIDE 之间、且长边不小于 min_long_side；默认值即商品图片的取值。
    """
    with _process_lock:
        try:
            return _process(data, min_side, min_long_side)
        except Exception:
            # 含本模块自己抛的 ImageRejected：一律换成不带文本、不带来源的新异常。
            raise ImageRejected from None


def _process(data: bytes, min_side: int, min_long_side: int) -> ProcessedImage:
    with Image.open(io.BytesIO(data)) as opened:
        source_format = _ACCEPTED_FORMATS.get(opened.format or "")
        if source_format is None:
            raise ImageRejected
        if opened.format != "MPO" and getattr(opened, "n_frames", 1) > 1:
            raise ImageRejected
        width, height = opened.size
        if not (min_side <= width <= MAX_SIDE and min_side <= height <= MAX_SIDE):
            raise ImageRejected
        if max(width, height) < min_long_side:
            raise ImageRejected
        rawmode = _tile_rawmode(opened)
        if opened.format in ("JPEG", "MPO") and max(width, height) > DISPLAY_MAX_SIDE:
            # draft 只选不小于请求尺寸的缩小比例，之后仍按下面的缩放规则缩到长边 1,600。
            opened.draft(None, _fit_size(width, height))
        opened.load()
        image = ImageOps.exif_transpose(opened)

    icc_profile = image.info.get("icc_profile")
    image = _fix_transparency(image, rawmode)
    color, alpha = _split_alpha(image)
    display = _to_srgb(color, icc_profile)
    if alpha is not None:
        display = Image.merge("RGBA", (*display.split(), alpha))
    if max(display.size) > DISPLAY_MAX_SIDE:
        display = display.resize(_fit_size(*display.size), Image.Resampling.LANCZOS)

    # 复制像素到不带任何 info 的新图上再编码：展示图不含 EXIF、ICC 与 XMP。
    clean = Image.frombytes(display.mode, display.size, display.tobytes())
    buffer = io.BytesIO()
    clean.save(buffer, format="WEBP", quality=WEBP_QUALITY, lossless=False)
    return ProcessedImage(
        data=buffer.getvalue(),
        source_format=source_format,
        width=clean.width,
        height=clean.height,
    )


def _tile_rawmode(image: Image.Image) -> str | None:
    """解码前 tile 的 rawmode（PNG 的 args 是字符串，JPEG 的是以它开头的元组）。"""
    if not image.tile:
        return None
    args = image.tile[0][3]
    if isinstance(args, tuple):
        args = args[0] if args else None
    return args if isinstance(args, str) else None


def _round_half_up(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


def _fit_size(width: int, height: int) -> tuple[int, int]:
    """等比缩到长边 1,600，短边四舍五入、至少 1。"""
    if width >= height:
        return DISPLAY_MAX_SIDE, max(1, _round_half_up(height * DISPLAY_MAX_SIDE, width))
    return max(1, _round_half_up(width * DISPLAY_MAX_SIDE, height)), DISPLAY_MAX_SIDE


def _fix_transparency(image: Image.Image, rawmode: str | None) -> Image.Image:
    """在任何模式转换之前，把 info 里的透明值换算成与解码后像素同一刻度。"""
    transparency = image.info.get("transparency")
    if rawmode == "RGB;16B" and isinstance(transparency, tuple):
        # 16 位 RGB：像素取了高 8 位，透明色仍是 16 位值。
        image.info["transparency"] = tuple(value >> 8 for value in transparency)
    elif rawmode in _LOW_BIT_GRAY_FACTORS and isinstance(transparency, int):
        image.info["transparency"] = transparency * _LOW_BIT_GRAY_FACTORS[rawmode]
    if image.mode == "1":
        # Pillow 转 L 时一并换算 transparency，不另修正。
        image = image.convert("L")
    return image


def _split_alpha(image: Image.Image) -> tuple[Image.Image, Image.Image | None]:
    """分出颜色部分（RGB、CMYK 或 L）与透明通道（L，没有时为 None）。"""
    mode = image.mode
    has_transparency = "transparency" in image.info

    if mode in _GRAY16_MODES:
        gray = image if mode == "I" else image.convert("I")
        alpha = None
        if has_transparency:
            # 按原 16 位值逐像素比较；point 在 I 模式只做线性映射，做不出比较。
            transparent = image.info["transparency"]
            alpha = ImageMath.lambda_eval(
                lambda args: args["convert"]((args["a"] != transparent) * 255, "L"),
                a=gray,
            )
        # I 模式的整数除法，65535 → 255。
        color = ImageMath.lambda_eval(lambda args: args["convert"](args["a"] / 256, "L"), a=gray)
        return color, alpha

    if has_transparency and mode in ("P", "RGB"):
        image, mode = image.convert("RGBA"), "RGBA"
    elif has_transparency and mode == "L":
        image, mode = image.convert("LA"), "LA"

    if mode in ("RGBA", "PA"):
        return image.convert("RGBA").convert("RGB"), image.getchannel("A")
    if mode == "LA":
        return image.getchannel("L"), image.getchannel("A")
    if mode == "P":
        return image.convert("RGBA").convert("RGB"), None
    if mode in ("RGB", "CMYK", "L"):
        return image, None
    return image.convert("RGB"), None


def _to_srgb(color: Image.Image, icc_profile: bytes | None) -> Image.Image:
    """带 ICC 配置文件的按它直接转到 sRGB；不带的按 Pillow 默认换算转成 RGB。"""
    if icc_profile is None:
        return color.convert("RGB")
    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
    # 嵌入的配置文件只能作用于对应的原模式（如 CMYK 配置文件配 CMYK 像素）。
    if profile.profile.xcolor_space.strip() != _PROFILE_SPACES.get(color.mode):
        raise ImageRejected
    converted = ImageCms.profileToProfile(color, profile, _SRGB, outputMode="RGB")
    if converted is None:
        raise ImageRejected
    return converted
