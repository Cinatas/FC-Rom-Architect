"""打印指定区间的 CHR 图块为 ASCII 点阵，用于人工确认字体/图标布局。

用法：
    python tools\\tiles.py 0x130 0x162        # 打印 0x130..0x161
    python tools\\tiles.py 0x130 0x162 --cols 8
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
CHR = GAME / "L3-data" / "chr.bin"

import render  # noqa: E402

CH = {0: ".", 1: "1", 2: "2", 3: "#"}


def parse_int(s: str) -> int:
    s = s.strip()
    return int(s[1:], 16) if s.startswith("$") else int(s, 0)


def art(chr_data: bytes, idx: int) -> list[str]:
    px = render.tile_pixels(chr_data, idx)
    return ["".join(CH[v] for v in row) for row in px]


def stats(chr_data: bytes, idx: int) -> str:
    px = render.tile_pixels(chr_data, idx)
    flat = [v for row in px for v in row]
    vals = sorted(set(flat))
    on = sum(1 for v in flat if v)
    return f"值={vals} 点亮={on}"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cols = 8
    for a in sys.argv[1:]:
        if a.startswith("--cols"):
            cols = int(a.split("=")[1]) if "=" in a else cols
    if len(args) < 2:
        print(__doc__)
        return

    a, b = parse_int(args[0]), parse_int(args[1])
    chr_data = CHR.read_bytes()
    n = len(chr_data) // 16
    b = min(b, n)

    for start in range(a, b, cols):
        idxs = list(range(start, min(start + cols, b)))
        print("  ".join(f"  0x{i:03X}   " for i in idxs))
        rows = [art(chr_data, i) for i in idxs]
        for y in range(8):
            print("  ".join("  " + r[y] + "   " for r in rows))
        print("  ".join(f"{stats(chr_data, i):<10}" for i in idxs))
        print()


if __name__ == "__main__":
    main()
