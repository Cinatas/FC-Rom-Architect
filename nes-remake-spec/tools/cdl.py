"""摄取 Mesen2 的 CDL（Code/Data Log），与静态递归下降的分区比对合并。

为什么需要它
------------
静态递归下降只能给出 [推断]：跳转表、间接跳转、未被引用的代码路径都会漏。
CDL 是模拟器**实际执行**的记录——哪个字节当指令取过、哪个当数据读过。
两者合并后，代码/数据边界才能升级为 [已验证]。

自动识别布局（本工具的关键设计）
--------------------------------
Mesen2 的 .cdl 到底按什么顺序排列（仅 PRG / PRG+CHR / CHR+PRG）、哪一位表示
"代码"，不同版本可能不同。**猜错会得到完全错误的对照结论**，所以本工具不硬编码，
而是穷举候选并**用静态递归下降的结果当校验集**：
  正确的布局下，静态已知的代码字节应当大量被 CDL 标为 code（高召回）。
因此自动选出召回率最高的组合，并把该召回率一并报告。若最高召回率过低，
说明假设全错，工具会明确警告而不是编一个结论。

用法
----
    python tools\\cdl.py                     # 自动找 evidence/*.cdl
    python tools\\cdl.py path\\to\\file.cdl  # 指定文件
    python tools\\cdl.py --list-layouts      # 只打印各组合的召回率

产物
----
    L3-data/cdl_merged.bin        每 PRG 字节 1 位：0=数据 1=代码
    L3-data/cdl_coverage.csv      合并后的 256 字节分区覆盖率
    L2-spec/_cdl-compare.md       CDL 与静态分析的差异报告
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
DATA = GAME / "L3-data"
EVID = GAME / "evidence"

import dis6502  # noqa: E402

# 候选：flag 字节到 PRG 的映射顺序
LAYOUTS = ["prg", "prg+chr", "chr+prg"]

# 候选：哪一位表示"代码"
CODE_BITS = [0x01, 0x02, 0x04, 0x80]

VECTORS = [0xC000, 0xC01A, 0xFFF9]


def find_cdl() -> Path | None:
    cands = sorted(EVID.glob("*.cdl")) + sorted(EVID.glob("*.CDL")) + sorted(GAME.glob("**/*.cdl"))
    return cands[0] if cands else None


def build_mask(buf: bytes, layout: str, code_bit: int, prg_size: int, chr_size: int) -> bytearray | None:
    """按候选布局取出 PRG 部分的 flag 字节并解成 0/1 掩码。

    buf 已去掉文件头（调用方负责搜索正确偏移）。
    """
    if layout == "prg":
        if len(buf) < prg_size:
            return None
        seg = buf[:prg_size]
    elif layout == "prg+chr":
        if len(buf) < prg_size:
            return None
        seg = buf[:prg_size]
    elif layout == "chr+prg":
        if len(buf) < chr_size + prg_size:
            return None
        seg = buf[chr_size:chr_size + prg_size]
    else:
        return None
    return bytearray(1 if (b & code_bit) else 0 for b in seg)


def recall(mask: bytearray, static_mask: bytearray) -> float:
    """静态已知代码里，被 CDL 也标为代码的比例。"""
    total = sum(static_mask)
    if not total:
        return 0.0
    hit = sum(1 for i, v in enumerate(static_mask) if v and mask[i])
    return hit / total


def coverage_rows(mask: bytearray, block: int = 256) -> list[dict]:
    rows = []
    for start in range(0, len(mask), block):
        end = min(start + block, len(mask))
        c = sum(mask[start:end])
        total = end - start
        rows.append({
            "file_off": f"0x{start:04X}",
            "bus_addr": f"${0x8000 + start:04X}",
            "size": total,
            "code_bytes": c,
            "data_bytes": total - c,
            "code_ratio": round(c / total, 3),
        })
    return rows


def main() -> None:
    list_only = "--list-layouts" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    prg_path = DATA / "prg.bin"
    if not prg_path.exists():
        raise SystemExit("找不到 L3-data/prg.bin，先跑 pipeline.py")
    prg = prg_path.read_bytes()
    chr_data = (DATA / "chr.bin").read_bytes() if (DATA / "chr.bin").exists() else b""

    cdl_path = Path(args[0]) if args else find_cdl()
    if cdl_path is None or not cdl_path.exists():
        print("未找到 .cdl 文件。")
        print(f"  请把 Mesen2 导出的 CDL 放到: {EVID}")
        print("  Mesen2 路径: Debugger -> Code/Data Logger -> 勾选 Code+Data -> 退出时导出")
        raise SystemExit(1)

    cdl = cdl_path.read_bytes()
    print(f"CDL    : {cdl_path}")
    print(f"大小   : {len(cdl)} 字节")
    print(f"PRG    : {len(prg)} 字节   CHR: {len(chr_data)} 字节")
    print(f"理论大小: PRG={len(prg)}  PRG+CHR={len(prg) + len(chr_data)}")

    disc = dis6502.find_code(prg, VECTORS)
    static_mask = disc.code_mask
    print(f"静态分区: {disc.code_bytes} 字节代码 ({100 * disc.code_bytes / len(prg):.1f}%)\n")

    results = []
    # Mesen2 的 CDL 带文件头（实测 "CDLv2" + 4 字节，共 9 字节），
    # 因此必须把「头部偏移」也作为未知量一起搜索，否则整份数据错位、召回率归零。
    header_note = ""
    if cdl[:5] == b"CDLv2":
        header_note = "检测到 Mesen2 CDLv2 头部（5 字节魔数 + 4 字节附加）"
        max_skip = 64
    else:
        max_skip = 16

    for skip in range(0, max_skip + 1):
        buf = cdl[skip:]
        if len(buf) < len(prg):
            break
        for layout in LAYOUTS:
            for bit in CODE_BITS:
                m = build_mask(buf, layout, bit, len(prg), len(chr_data))
                if m is None:
                    continue
                results.append((recall(m, static_mask), skip, layout, bit, m))

    if not results:
        raise SystemExit("所有候选（偏移×布局×位）都放不下这个 CDL —— "
                         "文件大小与 PRG/CHR 不匹配，可能不是 Mesen2 CDL。")

    results.sort(key=lambda r: (-r[0], r[1]))
    print("候选（头部偏移 × 布局 × code_bit，按召回率排序）：")
    print(f"  {'skip':>4}  {'layout':<8} {'code_bit':>8}   召回率")
    for rec, skip, layout, bit, _ in results[:8]:
        print(f"  {skip:>4}  {layout:<8} 0x{bit:02X}     {rec * 100:5.1f}%")

    if list_only:
        return

    best_rec, best_skip, best_layout, best_bit, merged = results[0]
    print(f"\n选定: 头部偏移={best_skip}  layout={best_layout}  "
          f"code_bit=0x{best_bit:02X}  召回率 {best_rec * 100:.1f}%")
    if header_note:
        print(f"      {header_note}")

    if best_rec < 0.30:
        print("\n**警告**：最佳候选的召回率过低，说明上述假设都不成立。")
        print("         请不要采信下面的对照结论；把 CDL 文件和这份输出发我，我改判据。")
        raise SystemExit(3)

    # ---- 差异统计
    static_only = [i for i in range(len(prg)) if static_mask[i] and not merged[i]]
    cdl_only = [i for i in range(len(prg)) if merged[i] and not static_mask[i]]
    both = sum(1 for i in range(len(prg)) if static_mask[i] and merged[i])

    print(f"\n两种方法都判为代码 : {both} 字节")
    print(f"仅静态判为代码     : {len(static_only)} 字节（CDL 说这里没执行过 → 之前是误判）")
    print(f"仅 CDL 判为代码    : {len(cdl_only)} 字节（静态漏掉的路径，主要来自跳转表/间接跳转）")
    merged_bytes = sum(merged)
    print(f"合并后代码占比     : {merged_bytes}/{len(prg)} = {100 * merged_bytes / len(prg):.1f}%"
          f"（静态单方为 {100 * disc.code_bytes / len(prg):.1f}%）")

    # ---- 产物
    (DATA / "cdl_merged.bin").write_bytes(bytes(merged))
    with open(DATA / "cdl_coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file_off", "bus_addr", "size",
                                          "code_bytes", "data_bytes", "code_ratio"])
        w.writeheader()
        w.writerows(coverage_rows(merged))

    def ranges(idx_list: list[int], min_len: int = 4) -> list[tuple[int, int]]:
        out, i = [], 0
        while i < len(idx_list):
            j = i
            while j + 1 < len(idx_list) and idx_list[j + 1] == idx_list[j] + 1:
                j += 1
            if idx_list[j] - idx_list[i] + 1 >= min_len:
                out.append((idx_list[i], idx_list[j] + 1))
            i = j + 1
        return out

    r_new = ranges(cdl_only)
    r_drop = ranges(static_only)

    md = [
        "# CDL 对照报告（自动生成）",
        "",
        f"> 来源：`{cdl_path.name}`（{len(cdl)} 字节）",
        f"> 布局假设：`{best_layout}`，code_bit=`0x{best_bit:02X}`，"
        f"召回率 **{best_rec * 100:.1f}%**",
        "",
        "召回率是**校验指标**：静态递归下降已知的代码，有多少也被 CDL 标为代码。",
        "它越高，说明布局假设越可信。低于 30% 时本报告视为无效。",
        "",
        "## 汇总",
        "",
        "| 项 | 字节数 | 占 PRG |",
        "|---|---|---|",
        f"| 两法一致判为代码 | {both} | {100 * both / len(prg):.1f}% |",
        f"| 仅静态（CDL 未执行 → 误判） | {len(static_only)} | {100 * len(static_only) / len(prg):.1f}% |",
        f"| 仅 CDL（静态漏掉的路径） | {len(cdl_only)} | {100 * len(cdl_only) / len(prg):.1f}% |",
        f"| **合并后代码总量** | **{merged_bytes}** | **{100 * merged_bytes / len(prg):.1f}%** |",
        "",
        "## 新增代码区段（CDL 独有，≥4 字节）",
        "",
        "这些是静态递归下降漏掉的路径，通常是跳转表目标或间接跳转的落点。",
        "**优先反汇编这些区段**，往往能补上关键函数。",
        "",
    ]
    if r_new:
        md += ["| 文件偏移 | 总线地址 | 字节数 |", "|---|---|---|"]
        for a, b in r_new[:40]:
            md.append(f"| `0x{a:04X}` | `${0x8000 + a:04X}` | {b - a} |")
        if len(r_new) > 40:
            md.append(f"\n… 另有 {len(r_new) - 40} 段，见 `cdl_coverage.csv`")
    else:
        md.append("_（无）_")

    md += ["", "## 被推翻的代码区（静态误判，≥4 字节）", "",
           "这些字节被静态判为代码但从未执行 —— 多半是查表数据恰好被解码成指令。",
           "把它们改回数据，会让数据表普查的信噪比显著上升。", ""]
    if r_drop:
        md += ["| 文件偏移 | 总线地址 | 字节数 |", "|---|---|---|"]
        for a, b in r_drop[:40]:
            md.append(f"| `0x{a:04X}` | `${0x8000 + a:04X}` | {b - a} |")
        if len(r_drop) > 40:
            md.append(f"\n… 另有 {len(r_drop) - 40} 段")
    else:
        md.append("_（无）_")

    md += ["", "## 下一步", "",
           "1. 用合并掩码重跑 `pipeline.py`（后续可加 `--use-cdl` 参数），",
           "   数据区普查的假阳性会明显下降。",
           "2. 对上面「仅 CDL」的区段做反汇编，补 `disasm_verified.txt`。",
           "3. 之前标 `[推断]` 的代码/数据边界，现在可升级为 `[已验证]`。", ""]

    out = GAME / "L2-spec" / "_cdl-compare.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\n产物: {out.relative_to(GAME)}")
    print(f"      L3-data/cdl_merged.bin / cdl_coverage.csv")


if __name__ == "__main__":
    main()
