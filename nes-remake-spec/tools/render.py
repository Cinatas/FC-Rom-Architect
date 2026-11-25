"""CHR 图集与调色板候选渲染。

CHR 格式
--------
每个 8x8 图块 16 字节：
  byte 0-7  平面 0（低位）
  byte 8-15 平面 1（高位）
每字节的 bit7 是最左边像素。像素值 = (平面1 << 1) | 平面0，范围 0-3，
再经 NES 调色板的 4 个颜色索引映射成 RGB。

证据等级
--------
  - CHR 字节与图块结构：[已验证]
  - 灰度渲染：[已验证]
  - 彩色渲染所用的调色板：[推断] 调色板表是静态扫描的候选，
    真实调色板由代码在运行时写入 $3F00，需 Mesen2 抓 PPU 调色板确认。
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

# 2C02 通用近似调色板（64 项）。不同模拟器/电视渲染会有差异，
# 这里够用于肉眼辨认图块内容，不作为颜色标准。
_NES_HEX = (
    "7C7C7C 0000FC 0000BC 4428BC 940084 A80020 A81000 881400 "
    "503000 007800 006800 005800 004058 000000 000000 000000 "
    "BCBCBC 0078F8 0058F8 6844FC D800CC E40058 F83800 E45C10 "
    "AC7C00 00B800 00A800 00A844 008888 000000 000000 000000 "
    "F8F8F8 3CBCFC 6888FC 9878F8 F878F8 F85898 F87858 FCA044 "
    "F8B800 B8F818 58D854 58F898 00E8D8 787878 000000 000000 "
    "FCFCFC A4E4FC B8B8F8 D8B8F8 F8B8F8 F8A4C0 F0D0B0 FCE0A8 "
    "F8D878 D8F878 B8F8B8 B8F8D8 00FCFC F8D8F8 000000 000000"
)
NES_PALETTE: list[tuple[int, int, int]] = [
    (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    for h in _NES_HEX.split()
]
assert len(NES_PALETTE) == 64

GRAY4 = [(0, 0, 0), (74, 74, 74), (148, 148, 148), (232, 232, 232)]


def tile_pixels(chr_data: bytes, tile_index: int) -> list[list[int]]:
    """返回 8x8 的像素值矩阵（0-3）。"""
    base = tile_index * 16
    t = chr_data[base:base + 16]
    if len(t) < 16:
        return [[0] * 8 for _ in range(8)]
    rows = []
    for y in range(8):
        p0 = t[y]
        p1 = t[y + 8]
        row = []
        for x in range(7, -1, -1):
            bit = 1 << x
            v = (1 if (p0 & bit) else 0) | (2 if (p1 & bit) else 0)
            row.append(v)
        rows.append(row)
    return rows


def _load_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        try:
            return ImageFont.load_default()
        except Exception:
            return None


def render_tileset(
    chr_data: bytes,
    path: str,
    cols: int = 16,
    scale: int = 4,
    gap: int = 1,
    palette4: list[tuple[int, int, int]] | None = None,
    show_index: bool = False,
    font_size: int = 9,
) -> tuple[int, int]:
    """把整块 CHR 渲染成 PNG 图集，返回 (宽, 高)。"""
    pal = palette4 or GRAY4
    n = len(chr_data) // 16
    rows = (n + cols - 1) // cols
    cell = 8 * scale
    step = cell + gap
    w = cols * step + gap
    h = rows * step + gap

    img = Image.new("RGB", (w, h), (22, 22, 26))
    px = img.load()

    font = _load_font(font_size) if show_index else None
    draw = ImageDraw.Draw(img) if show_index else None

    for idx in range(n):
        gx = idx % cols
        gy = idx // cols
        ox = gap + gx * step
        oy = gap + gy * step
        pixels = tile_pixels(chr_data, idx)
        for y in range(8):
            for x in range(8):
                c = pal[pixels[y][x] & 3]
                for sy in range(scale):
                    for sx in range(scale):
                        px[ox + x * scale + sx, oy + y * scale + sy] = c
        if show_index and draw and font:
            label = f"{idx:03X}"
            draw.text((ox + 1, oy + 1), label, fill=(255, 80, 80), font=font)
    img.save(path)
    return w, h


def render_palette_candidates(
    prg: bytes,
    candidates: list[dict],
    path: str,
    swatch: int = 18,
    label_w: int = 96,
) -> tuple[int, int]:
    """把每个候选调色板表渲染成一条色带，便于肉眼挑出游戏真实配色。"""
    if not candidates:
        img = Image.new("RGB", (240, 32), (22, 22, 26))
        ImageDraw.Draw(img).text((6, 10), "no palette candidate", fill=(200, 200, 200))
        img.save(path)
        return img.size

    font = _load_font(11)
    max_colors = max(c["length"] for c in candidates)
    max_colors = min(max_colors, 32)
    w = label_w + max_colors * (swatch + 2) + 8
    h = len(candidates) * (swatch + 6) + 8

    img = Image.new("RGB", (w, h), (22, 22, 26))
    draw = ImageDraw.Draw(img)

    y = 6
    for c in candidates:
        off = int(c["offset"], 16) if isinstance(c["offset"], str) else c["offset"]
        n = min(c["length"], max_colors)
        if font:
            draw.text((4, y + 3), f"0x{off:04X} n={c['length']}", fill=(200, 200, 200), font=font)
        for i in range(n):
            v = prg[off + i] & 0x3F
            r, g, b = NES_PALETTE[v]
            x = label_w + i * (swatch + 2)
            draw.rectangle([x, y, x + swatch - 1, y + swatch - 1], fill=(r, g, b))
        y += swatch + 6
    img.save(path)
    return img.size


def palette_from_table(prg: bytes, off: int, start: int = 0, count: int = 4):
    """取调色板表里连续的 4 个颜色作为 4 色组。"""
    out = []
    for i in range(count):
        v = prg[off + start + i] & 0x3F
        out.append(NES_PALETTE[v])
    return out
