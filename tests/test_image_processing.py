"""商品图片的校验与处理（app/services/image_processing.py，SHOP-TASK-188）。

样本都在测试里用 Pillow 现场生成；Pillow 写不出的 16 位 RGB 与 2、4 位灰度 PNG 按 PNG 格式
用 struct 与 zlib 拼出字节。每条测试的文档字符串写明它守住的 docs/DESIGN.md 1.19
「计价、优惠、积分与库存」第 13 条原句。展示图是有损 WebP，
颜色比较时各分量与期望差不超过 2，透明度须相等。
"""

from __future__ import annotations

import io
import random
import struct
import threading
import zlib

import pytest
from PIL import ExifTags, Image, ImageCms, JpegImagePlugin, PngImagePlugin

from app.services import image_processing
from app.services.image_processing import ImageRejected, ProcessedImage, process_image

SIDE = 300
LEFT = (20, 150)
RIGHT = (280, 150)
RIGHT_HALF = (150, 0, SIDE, SIDE)
COLOR_TOLERANCE = 2

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _encode(image: Image.Image, fmt: str, **params: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _result_image(result: ProcessedImage) -> Image.Image:
    image = _open(result.data)
    assert image.format == "WEBP"
    assert image.size == (result.width, result.height)
    return image


def _srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _assert_pixel(actual: tuple[int, ...], expected: tuple[int, ...]) -> None:
    assert len(actual) == len(expected), (actual, expected)
    for got, want in zip(actual[:3], expected[:3], strict=True):
        assert abs(got - want) <= COLOR_TOLERANCE, (actual, expected)
    assert actual[3:] == expected[3:], (actual, expected)


def _assert_transparent(actual: tuple[int, ...]) -> None:
    assert len(actual) == 4, actual
    assert actual[3] == 0, actual


def _halves(mode: str, left: object, right: object) -> Image.Image:
    image = Image.new(mode, (SIDE, SIDE), left)
    image.paste(right, RIGHT_HALF)
    return image


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload)
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _raw_png(bit_depth: int, color_type: int, row: bytes, trns: bytes) -> bytes:
    """SIDE × SIDE、每行内容相同、不交错的 PNG（每行前加滤波类型 0）。"""
    header = struct.pack(">IIBBBBB", SIDE, SIDE, bit_depth, color_type, 0, 0, 0)
    pixels = (b"\x00" + row) * SIDE
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"tRNS", trns)
        + _png_chunk(b"IDAT", zlib.compress(pixels))
        + _png_chunk(b"IEND", b"")
    )


def _pack_bits(values: list[int], depth: int) -> bytes:
    per_byte = 8 // depth
    packed = bytearray()
    for start in range(0, len(values), per_byte):
        byte = 0
        for index, value in enumerate(values[start : start + per_byte]):
            byte |= value << (8 - depth * (index + 1))
        packed.append(byte)
    return bytes(packed)


def _i16_png(left: int, right: int, **params: object) -> bytes:
    # paste 对 I;16 不可靠，用 frombytes 按小端 16 位逐行拼出像素。
    half = SIDE // 2
    row = struct.pack(f"<{half}H", *[left] * half) + struct.pack(f"<{half}H", *[right] * half)
    image = Image.frombytes("I;16", (SIDE, SIDE), row * SIDE)
    return _encode(image, "PNG", **params)


# ---------------------------------------------------------------- 处理


def test_jpeg_is_turned_upright_and_metadata_removed() -> None:
    """「按 EXIF 方向转正」「去掉全部元数据（含拍摄地点）」。

    以及「长边超过 1,600 像素时等比缩到 1,600」。
    样本是带 EXIF 方向 6 与 GPS 信息的 3000×1000 JPEG。
    """
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.IFD.GPSInfo] = {
        ExifTags.GPS.GPSLatitudeRef: "N",
        ExifTags.GPS.GPSLatitude: (3.0, 8.0, 0.0),
    }
    data = _encode(Image.new("RGB", (3000, 1000), (200, 100, 50)), "JPEG", exif=exif)
    assert _open(data).getexif().get_ifd(ExifTags.IFD.GPSInfo)

    result = process_image(data)

    assert (result.width, result.height) == (533, 1600)
    assert result.source_format == "jpeg"
    image = _result_image(result)
    assert "exif" not in image.info
    assert "icc_profile" not in image.info
    assert "xmp" not in image.info


def test_rgba_png_keeps_alpha() -> None:
    """「带透明的转成 RGBA」「保留透明通道」。"""
    data = _encode(Image.new("RGBA", (SIDE, SIDE), (0, 0, 255, 100)), "PNG")

    result = process_image(data)

    assert result.source_format == "png"
    image = _result_image(result)
    assert image.mode == "RGBA"
    _assert_pixel(image.getpixel(LEFT), (0, 0, 255, 100))


def test_palette_png_with_transparency_becomes_rgba() -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」。"""
    image = _halves("P", 0, 1)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * (256 * 3 - 6))
    data = _encode(image, "PNG", transparency=0)

    result = _result_image(process_image(data))

    assert result.mode == "RGBA"
    _assert_transparent(result.getpixel(LEFT))
    assert result.getpixel(RIGHT)[3] == 255


def test_la_png_becomes_rgba() -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」「保留透明通道」。"""
    data = _encode(Image.new("LA", (SIDE, SIDE), (60, 100)), "PNG")

    result = _result_image(process_image(data))

    assert result.mode == "RGBA"
    _assert_pixel(result.getpixel(LEFT), (60, 60, 60, 100))


def test_16_bit_gray_png_becomes_8_bit() -> None:
    """「先转成 8 位 RGB（……调色板、灰度与 16 位图同样转换）」。"""
    data = _i16_png(32768, 32768)

    result = _result_image(process_image(data))

    _assert_pixel(result.convert("RGB").getpixel(LEFT), (128, 128, 128))


def test_cmyk_jpeg_without_icc_becomes_rgb() -> None:
    """「不带 ICC 的 CMYK 图按 Pillow 的默认换算转成 RGB」。"""
    data = _encode(Image.new("CMYK", (SIDE, SIDE), (0, 128, 255, 0)), "JPEG")
    sample = _open(data)
    assert sample.mode == "CMYK"
    assert "icc_profile" not in sample.info

    result = _result_image(process_image(data))

    assert result.mode == "RGB"


def test_icc_png_is_converted_and_profile_removed() -> None:
    """「带 ICC 配置文件的按它转成 sRGB」「去掉全部元数据」。"""
    data = _encode(Image.new("RGB", (SIDE, SIDE), (10, 20, 30)), "PNG", icc_profile=_srgb_icc())

    result = _result_image(process_image(data))

    assert result.mode == "RGB"
    assert "icc_profile" not in result.info
    _assert_pixel(result.getpixel(LEFT), (10, 20, 30))


def test_mpo_is_accepted_as_jpeg() -> None:
    """「解码出的格式须为 JPEG（含手机常见的 MPO，只取主图，按 JPEG 记录）、PNG 或 WebP」。"""
    first = Image.new("RGB", (SIDE, SIDE), (200, 0, 0))
    second = Image.new("RGB", (SIDE, SIDE), (0, 0, 200))
    data = _encode(first, "MPO", save_all=True, append_images=[second])
    assert _open(data).format == "MPO"

    result = process_image(data)

    assert result.source_format == "jpeg"
    assert (result.width, result.height) == (SIDE, SIDE)


def test_webp_is_accepted() -> None:
    """「解码出的格式须为 JPEG（……）、PNG 或 WebP」。"""
    data = _encode(Image.new("RGB", (SIDE, SIDE), (10, 20, 30)), "WEBP", lossless=True)

    result = process_image(data)

    assert result.source_format == "webp"
    assert (result.width, result.height) == (SIDE, SIDE)


def test_wide_png_is_scaled_to_long_side_1600() -> None:
    """「长边超过 1,600 像素时等比缩到 1,600」。"""
    data = _encode(Image.new("RGB", (4000, 200), (10, 20, 30)), "PNG")

    result = process_image(data)

    assert (result.width, result.height) == (1600, 80)


def test_large_jpeg_is_scaled_to_1600() -> None:
    """「长边超过 1,600 像素时等比缩到 1,600」。"""
    data = _encode(Image.new("RGB", (4000, 4000), (10, 20, 30)), "JPEG")

    result = process_image(data)

    assert (result.width, result.height) == (1600, 1600)
    assert _result_image(result).size == (1600, 1600)


@pytest.mark.parametrize(
    ("size", "requested", "drafted"),
    [
        ((4000, 4000), (1600, 1600), (2000, 2000)),
        ((3000, 1000), (1600, 533), (3000, 1000)),
    ],
)
def test_jpeg_is_decoded_reduced_toward_target(
    monkeypatch: pytest.MonkeyPatch,
    size: tuple[int, int],
    requested: tuple[int, int],
    drafted: tuple[int, int],
) -> None:
    """「JPEG 先按目标尺寸缩小解码以省内存」。"""
    calls: list[object] = []
    original = JpegImagePlugin.JpegImageFile.draft

    def spy(self: JpegImagePlugin.JpegImageFile, mode: str | None, request: object) -> object:
        calls.append(request)
        outcome = original(self, mode, request)
        calls.append(self.size)
        return outcome

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    data = _encode(Image.new("RGB", size, (10, 20, 30)), "JPEG")

    result = process_image(data)

    assert calls == [requested, drafted]
    assert max(result.width, result.height) == 1600


def test_jpeg_within_1600_is_not_drafted(monkeypatch: pytest.MonkeyPatch) -> None:
    """「JPEG 先按目标尺寸缩小解码以省内存」：长边不超过 1,600 的不缩小解码。"""
    calls: list[object] = []
    monkeypatch.setattr(
        JpegImagePlugin.JpegImageFile, "draft", lambda self, mode, size: calls.append(size)
    )
    data = _encode(Image.new("RGB", (1600, 900), (10, 20, 30)), "JPEG")

    result = process_image(data)

    assert calls == []
    assert (result.width, result.height) == (1600, 900)


def test_output_is_lossy_webp() -> None:
    """「编码为 WebP（有损、质量 85）作为展示图」。"""
    data = _encode(Image.new("RGB", (SIDE, SIDE), (10, 20, 30)), "PNG")

    result = process_image(data)

    assert _result_image(result).format == "WEBP"
    assert result.data[:4] == b"RIFF"
    assert result.data[8:12] == b"WEBP"
    assert b"VP8L" not in result.data


# ---------------------------------------------------------------- 透明与 ICC


def test_rgb_png_with_transparency_and_icc() -> None:
    """「带透明的转成 RGBA」「带 ICC 配置文件的按它转成 sRGB」「保留透明通道」。"""
    image = _halves("RGB", (1, 2, 3), (10, 20, 30))
    data = _encode(image, "PNG", transparency=(1, 2, 3), icc_profile=_srgb_icc())

    result = _result_image(process_image(data))

    assert result.mode == "RGBA"
    _assert_transparent(result.getpixel(LEFT))
    _assert_pixel(result.getpixel(RIGHT), (10, 20, 30, 255))


def test_gray_png_with_transparency() -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」。"""
    data = _encode(_halves("L", 7, 50), "PNG", transparency=7)

    result = _result_image(process_image(data))

    _assert_pixel(result.getpixel(LEFT), (7, 7, 7, 0))
    _assert_pixel(result.getpixel(RIGHT), (50, 50, 50, 255))


def test_16_bit_gray_png_with_transparency() -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」。"""
    data = _i16_png(1000, 32768, transparency=1000)

    result = _result_image(process_image(data))

    _assert_pixel(result.getpixel(LEFT), (3, 3, 3, 0))
    _assert_pixel(result.getpixel(RIGHT), (128, 128, 128, 255))


def test_1_bit_png_with_transparency() -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」。"""
    data = _encode(_halves("1", 0, 255), "PNG", transparency=0)
    assert _open(data).mode == "1"

    result = _result_image(process_image(data))

    _assert_transparent(result.getpixel(LEFT))
    _assert_pixel(result.getpixel(RIGHT), (255, 255, 255, 255))


def test_16_bit_rgb_png_with_transparency() -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」。"""
    half = SIDE // 2
    left = struct.pack(">3H", 1000, 2000, 3000)
    right = struct.pack(">3H", 40000, 50000, 60000)
    data = _raw_png(16, 2, left * half + right * half, left)

    result = _result_image(process_image(data))

    _assert_transparent(result.getpixel(LEFT))
    _assert_pixel(result.getpixel(RIGHT), (156, 195, 234, 255))


@pytest.mark.parametrize(("depth", "left", "right"), [(2, 1, 3), (4, 5, 15)])
def test_low_bit_gray_png_with_transparency(depth: int, left: int, right: int) -> None:
    """「带透明的转成 RGBA，调色板、灰度与 16 位图同样转换」。"""
    half = SIDE // 2
    row = _pack_bits([left] * half + [right] * half, depth)
    data = _raw_png(depth, 0, row, struct.pack(">H", left))

    result = _result_image(process_image(data))

    _assert_pixel(result.getpixel(LEFT), (85, 85, 85, 0))
    _assert_pixel(result.getpixel(RIGHT), (255, 255, 255, 255))


# ---------------------------------------------------------------- 不合格


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_other_formats_are_rejected(fmt: str) -> None:
    """「解码出的格式须为 JPEG（……）、PNG 或 WebP」「不合格即回答「图片不合格」」。"""
    data = _encode(Image.new("RGB", (SIDE, SIDE), (10, 20, 30)), fmt)

    with pytest.raises(ImageRejected) as exc_info:
        process_image(data)

    assert exc_info.value.args == ()
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("fmt", ["WEBP", "PNG"])
def test_animated_images_are_rejected(fmt: str) -> None:
    """「只收单帧静态图（MPO 以外的多帧图拒绝）」：两帧的动画 WebP 与 APNG。"""
    first = Image.new("RGB", (SIDE, SIDE), (200, 0, 0))
    second = Image.new("RGB", (SIDE, SIDE), (0, 0, 200))
    data = _encode(first, fmt, save_all=True, append_images=[second])
    assert getattr(_open(data), "n_frames", 1) == 2

    with pytest.raises(ImageRejected):
        process_image(data)


@pytest.mark.parametrize("size", [(199, 300), (300, 199), (4001, 300), (300, 4001)])
def test_out_of_range_sizes_are_rejected(size: tuple[int, int]) -> None:
    """「宽与高都在 200 到 4,000 像素之间，先读尺寸、合格再解码（防止解压炸弹）」。"""
    data = _encode(Image.new("RGB", size, (10, 20, 30)), "PNG")

    with pytest.raises(ImageRejected):
        process_image(data)


def test_smallest_size_is_accepted() -> None:
    """「宽与高都在 200 到 4,000 像素之间」（含两端）。"""
    data = _encode(Image.new("RGB", (200, 200), (10, 20, 30)), "PNG")

    result = process_image(data)

    assert (result.width, result.height) == (200, 200)


def test_png_text_memory_limit_is_1_mb() -> None:
    """「PNG 文本块解压后的总量上限设为 1 MB（单块上限沿用默认）」。"""
    assert PngImagePlugin.MAX_TEXT_MEMORY == 1_048_576


def _png_with_text_chunks(count: int) -> bytes:
    info = PngImagePlugin.PngInfo()
    for index in range(count):
        info.add_text(f"note{index}", "x" * (700 * 1024), zip=True)
    return _encode(Image.new("RGB", (SIDE, SIDE), (10, 20, 30)), "PNG", pnginfo=info)


def test_png_text_chunks_over_1_mb_are_rejected() -> None:
    """「PNG 文本块解压后的总量上限设为 1 MB」：两个各 700 KiB 的 zTXt 块。"""
    with pytest.raises(ImageRejected):
        process_image(_png_with_text_chunks(2))


def test_png_text_chunk_under_1_mb_is_accepted() -> None:
    """「PNG 文本块解压后的总量上限设为 1 MB（单块上限沿用默认）」：一个 700 KiB 的 zTXt 块。"""
    result = process_image(_png_with_text_chunks(1))

    assert (result.width, result.height) == (SIDE, SIDE)


def test_truncated_png_is_rejected() -> None:
    """「不合格即回答「图片不合格」，不留文件」：截断的数据。"""
    noise = random.Random(188).randbytes(SIDE * SIDE * 3)
    data = _encode(Image.frombytes("RGB", (SIDE, SIDE), noise), "PNG")

    with pytest.raises(ImageRejected):
        process_image(data[: len(data) // 2])


def test_empty_bytes_are_rejected() -> None:
    """「最后按文件内容判断，不信文件名、扩展名与请求头」「不合格即回答「图片不合格」」。"""
    with pytest.raises(ImageRejected) as exc_info:
        process_image(b"")

    assert exc_info.value.args == ()
    assert exc_info.value.__cause__ is None


# ---------------------------------------------------------------- 并发


def test_one_image_at_a_time() -> None:
    """「同一进程同时只处理一张，其余排队等待」。"""
    data = _encode(Image.new("RGB", (SIDE, SIDE), (10, 20, 30)), "PNG")
    results: list[ProcessedImage] = []

    def work() -> None:
        results.append(process_image(data))

    image_processing._process_lock.acquire()
    try:
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(0.2)
        assert worker.is_alive()
        assert results == []
    finally:
        image_processing._process_lock.release()
    worker.join(30)

    assert not worker.is_alive()
    assert len(results) == 1
    assert (results[0].width, results[0].height) == (SIDE, SIDE)
