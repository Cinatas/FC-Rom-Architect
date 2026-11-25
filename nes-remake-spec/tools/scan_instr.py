"""全 ROM 线性扫描某条指令（不依赖分区），找所有引用。

背景：disasm_verified.txt 只含"静态 ∪ CDL"分区内的指令。玩法代码如果
两边都没覆盖到，就不会出现在里面 —— 所以搜 $12 只能找到 14 处。
本工具反其道而行：对 PRG 的**每一个偏移**都试解码一次，因此能扫到任何位置。

用法：
    python tools\\scan_instr.py "LDA $12"
    python tools\\scan_instr.py "AND #$04"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
DATA = GAME / "L3-data"

import dis6502  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    want = sys.argv[1].strip()
    prg = (DATA / "prg.bin").read_bytes()

    hits = []
    for off in range(len(prg)):
        ins = dis6502.decode(prg, off)
        if ins is None:
            continue
        if ins.text.strip() == want:
            hits.append(off)

    print(f"'{want}' 在全 ROM 命中 {len(hits)} 处")
    for off in hits:
        bus = 0x8000 + off
        print(f"\n--- file 0x{off:04X}  (总线 ${bus:04X} / 执行镜像 ${bus + 0x4000:04X}) ---")
        p = off
        for _ in range(9):
            ins = dis6502.decode(prg, p)
            if ins is None:
                break
            mark = " <<<" if p == off else ""
            print(f"   {0x8000 + p:04X}  "
                  + " ".join(f"{b:02X}" for b in ins.raw).ljust(9)
                  + f" {ins.text}{mark}")
            p += ins.length


if __name__ == "__main__":
    main()
