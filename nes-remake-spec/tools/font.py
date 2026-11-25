"""定位 CHR 字体表基址，验证「图块号 = 基址 + ASCII 码」的假设。

假设来源
--------
PRG 里的字幕串直接就是 ASCII 字节（"CONGRATULATIONS"= 43 4F 4E ...），
且用 ':' (0x3A) 代替空格。若引擎用 图块号 = base + ASCII 直接查 CHR，
那么必然满足：
  - CHR[base + 0x41 .. base + 0x5A] 是 A-Z 字形
  - CHR[base + 0x30 .. base + 0x39] 是 0-9 字形
  - CHR[base + 0x3A] 为**空块**（这正是他们用 ':' 当空格的原因）
  - CHR[base + 0x20] 大概率非空（所以不能用空格）

本脚本枚举 base，用上述 4 条打分，输出最高分候选。
证据等级：若 4 条全部命中，字体映射可记为 [已验证]。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
CHR = GAME / "L3-data" / "chr.bin"

import render  # noqa: E402


def glyph_stats(chr_data: bytes, idx: int) -> dict:
    px = render.tile_pixels(chr_data, idx)
    flat = [v for row in px for v in row]
    vals = sorted(set(flat))
    on = sum(1 for v in flat if v != 0)
    return {"values": vals, "on": on, "blank": on == 0}


def is_glyph(st: dict) -> bool:
    # 字形：只用到 1~2 种像素值，点亮像素在 6~46 之间（8x8=64）
    return (not st["blank"]) and len(st["values"]) <= 2 and 6 <= st["on"] <= 46


def score_base(chr_data: bytes, base: int) -> tuple[int, dict]:
    n = len(chr_data) // 16
    if base + 0x5A >= n:
        return -1, {}

    detail = {}
    pts = 0

    # 1) ':' 必须为空块 —— 最强判据
    st_colon = glyph_stats(chr_data, base + 0x3A)
    if st_colon["blank"]:
        pts += 4
    detail["colon_blank"] = st_colon["blank"]

    # 2) space(0x20) 应该非空（否则没理由用 ':' 代替空格）
    st_space = glyph_stats(chr_data, base + 0x20)
    if not st_space["blank"]:
        pts += 2
    detail["space_not_blank"] = not st_space["blank"]

    # 3) A-Z 字形
    ok_az = sum(1 for c in range(0x41, 0x5B) if is_glyph(glyph_stats(chr_data, base + c)))
    pts += ok_az
    detail["az_glyphs"] = ok_az

    # 4) 0-9 字形
    ok_09 = sum(1 for c in range(0x30, 0x3A) if is_glyph(glyph_stats(chr_data, base + c)))
    pts += ok_09
    detail["digits"] = ok_09

    return pts, detail


def main() -> None:
    chr_data = CHR.read_bytes()
    n = len(chr_data) // 16
    print(f"CHR: {CHR}  ({len(chr_data)} 字节, {n} 个图块)\n")

    results = []
    for base in range(0, n - 0x5B):
        pts, detail = score_base(chr_data, base)
        results.append((pts, base, detail))

    results.sort(key=lambda r: (-r[0], r[1]))

    print("前 6 个候选：")
    print(f"{'base':>6} {'score':>6} {':空':>5} {'space非空':>9} {'A-Z':>5} {'0-9':>5}")
    for pts, base, d in results[:6]:
        print(f"  0x{base:02X} {pts:>6} {str(d.get('colon_blank')):>5} "
              f"{str(d.get('space_not_blank')):>9} {d.get('az_glyphs', 0):>5} {d.get('digits', 0):>5}")

    pts, base, d = results[0]
    maxpts = 4 + 2 + 26 + 10
    print(f"\n最佳 base = 0x{base:02X}  得分 {pts}/{maxpts}")

    if pts == maxpts:
        print("==> 4 条判据全部命中：字体映射 图块号 = 0x%02X + ASCII 码  [已验证]" % base)
        print("\n字符 → 图块号对照（可打印 ASCII 段）：")
        for c in list(range(0x20, 0x7F)):
            st = glyph_stats(chr_data, base + c)
            mark = "空" if st["blank"] else ("字形" if is_glyph(st) else f"复合({len(st['values'])}色)")
            if c in (0x20, 0x3A) or is_glyph(st):
                print(f"  {c:02X} '{chr(c)}'  -> tile 0x{base + c:03X}   {mark}")
    else:
        print("==> 判据未全部命中，假设需修正（见上表哪几条不满足）")


if __name__ == "__main__":
    main()
