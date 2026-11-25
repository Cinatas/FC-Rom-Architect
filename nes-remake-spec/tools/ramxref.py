"""RAM 交叉引用：列出所有读写某个内存地址的指令。

为什么需要它
------------
想给某个变量下断点，先要知道**哪些指令碰它**。手工翻反汇编很慢，而且容易漏。
本工具按「合并分区」（静态 ∪ CDL）线性解码，收集所有操作数指向目标地址的指令，
并按读/写分类。

读写分类依据助记符：
  写 = STA/STX/STY/INC/DEC/ASL/LSR/ROL/ROR（后五个是读-改-写）
  读 = 其余

用法
----
    python tools\\ramxref.py $B5 $68 $59        # 查多个地址
    python tools\\ramxref.py $B5 --all          # 含被推断但未执行的指令

产物：仅打印，供下断点参考。
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

WRITE_MNE = {"STA", "STX", "STY", "INC", "DEC", "ASL", "LSR", "ROL", "ROR"}
# 这些模式的“基址”才是要比较的目标地址
ADDR_MODES = {"zp", "zpx", "zpy", "abs", "absx", "absy", "izx", "izy"}


def parse_addr(s: str) -> int:
    s = s.strip().lower().replace("0x", "").lstrip("$")
    return int(s, 16)


def collect(prg: bytes, merged: bytes) -> list[dis6502.Ins]:
    """按合并分区线性解码，返回指令列表。"""
    out = []
    off = 0
    n = len(prg)
    while off < n:
        if not merged[off]:
            off += 1
            continue
        ins = dis6502.decode(prg, off)
        if ins is None:
            off += 1
            continue
        out.append(ins)
        off += ins.length
    return out


def main() -> None:
    args = sys.argv[1:]
    show_all = "--all" in args
    targets = [parse_addr(a) for a in args if not a.startswith("--")]
    if not targets:
        print(__doc__)
        return

    prg = (DATA / "prg.bin").read_bytes()
    cdl_path = DATA / "cdl_merged.bin"
    if not cdl_path.exists():
        raise SystemExit("缺少 L3-data/cdl_merged.bin —— 先跑 tools/cdl.py")
    cdl = cdl_path.read_bytes()

    disc = dis6502.find_code(prg, VECTORS)
    static = disc.code_mask
    merged = bytes(1 if (cdl[i] or static[i]) else 0 for i in range(len(prg)))

    ins_list = collect(prg, merged)
    print(f"解码 {len(ins_list):,} 条指令（合并分区）")
    if not show_all:
        print("只显示 CDL 确认执行过的指令；加 --all 显示全部\n")

    for tgt in targets:
        hits = []
        for ins in ins_list:
            if ins.mode not in ADDR_MODES or ins.operand is None:
                continue
            # izx/izy 的操作数是零页指针地址本身
            base = ins.operand & 0xFF if ins.mode in ("izx", "izy") else ins.operand
            if base != tgt:
                continue
            if not show_all and not cdl[ins.off]:
                continue
            kind = "W" if ins.mne in WRITE_MNE else "R"
            hits.append((ins, kind))

        nw = sum(1 for _, k in hits if k == "W")
        nr = sum(1 for _, k in hits if k == "R")
        print(f"=== ${tgt:04X}：{len(hits)} 处（写 {nw} / 读 {nr}）===")
        if not hits:
            print("  （无直接引用 —— 可能通过指针间接访问，如 LDA ($xx),Y）\n")
            continue
        for ins, kind in hits:
            print(f"  {ins.addr:04X}  {kind}  {ins.text}")
        print()


if __name__ == "__main__":
    main()
