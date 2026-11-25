"""生成「带执行标记」的反汇编清单：每条指令标注是否被真实执行过。

为什么需要它
------------
静态递归下降漏掉 685 字节代码（跳转表目标、间接跳转落点），同时也把 2310 字节
没执行过的字节判成代码。CDL 给了真实执行证据，把两者合并后可以产出一份
**可信度分层**的清单：

    E = Executed，CDL 确认执行过       → [已验证]
    S = Static-only，静态判为代码但没执行 → [推断]
    D = Data，两法都判为数据            → [已验证]

复刻者可以直接只信 E 行，把 S 行当参考。

用法
----
    python tools\\disasm_verified.py

产物
----
    L3-data/disasm_verified.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
DATA = GAME / "L3-data"

import dis6502  # noqa: E402

VECTORS = [0xC000, 0xC01A, 0xFFF9]


def main() -> None:
    prg = (DATA / "prg.bin").read_bytes()
    cdl_path = DATA / "cdl_merged.bin"
    if not cdl_path.exists():
        raise SystemExit("缺少 L3-data/cdl_merged.bin —— 先跑 tools/cdl.py")
    cdl = cdl_path.read_bytes()

    disc = dis6502.find_code(prg, VECTORS)
    static = disc.code_mask
    n = len(prg)

    # 合并分区：任一方判为代码即视为代码（CDL 补漏，静态兜底）
    merged = bytearray(1 if (cdl[i] or static[i]) else 0 for i in range(n))

    lines: list[str] = []
    lines.append("; 带执行标记的反汇编清单")
    lines.append(";   E = 已执行（CDL 证据，[已验证]）")
    lines.append(";   S = 仅静态判为代码、未执行（[推断]，可能是未触发的分支或误判）")
    lines.append(";   ; === 数据 xxx 字节 ===  表示两法都判为数据")
    lines.append(f"; PRG {n} 字节，静态代码 {sum(static)}，CDL 代码 {sum(cdl)}，"
                 f"合并 {sum(merged)}")
    lines.append("")

    off = 0
    n_e = n_s = n_d = 0
    ins_e = ins_s = 0

    while off < n:
        if not merged[off]:
            # 数据段
            j = off
            while j < n and not merged[j]:
                j += 1
            lines.append(f"; === 数据 {j - off} 字节 @ file 0x{off:04X} "
                         f"(总线 ${0x8000 + off:04X}) ===")
            for k in range(off, min(j, off + 32), 16):
                chunk = prg[k:min(k + 16, j)]
                lines.append(";   " + " ".join(f"{b:02X}" for b in chunk))
            if j - off > 32:
                lines.append(";   …")
            lines.append("")
            n_d += j - off
            off = j
            continue

        ins = dis6502.decode(prg, off)
        if ins is None:
            lines.append(f"{0x8000 + off:04X}  S  {prg[off]:02X}      .byte ${prg[off]:02X}")
            n_s += 1
            ins_s += 1
            off += 1
            continue

        tag = "E" if cdl[off] else "S"
        if tag == "E":
            n_e += ins.length
            ins_e += 1
        else:
            n_s += ins.length
            ins_s += 1
        hexs = " ".join(f"{b:02X}" for b in ins.raw)
        lines.append(f"{ins.addr:04X}  {tag}  {hexs:<9} {ins.text}")
        off += ins.length

    out = DATA / "disasm_verified.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"指令数: 已执行 {ins_e:,}  仅静态 {ins_s:,}")
    print(f"字节数: E {n_e:,}  S {n_s:,}  D {n_d:,}")
    print(f"产物: {out.relative_to(GAME)}  ({len(lines):,} 行)")
    print()
    print("用法建议：")
    print("  - 复刻时【直接采信 E 行】")
    print("  - S 行标 [推断]，需要再验证（多玩一段或下断点）")
    print("  - 数据段行给出原始字节，便于当表读")


if __name__ == "__main__":
    main()
