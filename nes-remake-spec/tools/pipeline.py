"""P0 + P1 管线：拆包编目 + 静态数据表普查。

P0（编目）：iNES 解析、PRG/CHR 拆分、CHR 图集渲染、校验和
P1（普查）：递归下降代码发现、代码/数据分区、指针表候选、调色板候选、
           ASCII 字符串

所有产物写到 game 目录下，脚本通过 __file__ 定位路径，无需传参：
    python bomberman\tools\pipeline.py
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent          # .../bomberman/tools
GAME = HERE.parent                              # .../bomberman
ROOT = GAME.parent                              # .../逆向FC

DATA = GAME / "L3-data"
EVID = GAME / "evidence"

import dis6502        # noqa: E402
import nesrom         # noqa: E402
import render         # noqa: E402


# ------------------------------------------------------------------ 工具函数

def find_rom() -> Path:
    cands = sorted(ROOT.glob("*.nes")) + sorted(ROOT.glob("*.NES"))
    if not cands:
        raise SystemExit(f"在 {ROOT} 下找不到 .nes 文件")
    exact = [c for c in cands if c.stem.lower() == GAME.name.lower()]
    return (exact or cands)[0]


def data_regions(mask: bytearray, min_len: int = 8) -> list[tuple[int, int]]:
    """返回 mask 为 **0** 的连续区间 [(start, end), ...]。

    注意极性：本函数收集「0」的区间，所以要传 `code_mask` 才能得到数据区。
    历史 bug：早期版本传了取反后的 `is_data`，结果把**代码区**当成数据区列出
    （连 0x0000 的 RESET 入口都上榜了）。修正于 2026-11-25。
    """
    regions = []
    n = len(mask)
    i = 0
    while i < n:
        if mask[i]:
            i += 1
            continue
        j = i
        while j < n and not mask[j]:
            j += 1
        if j - i >= min_len:
            regions.append((i, j))
        i = j
    return regions


def find_palette_candidates(prg: bytes, is_data: bytearray,
                            min_len: int = 16, max_len: int = 32) -> list[dict]:
    """调色板表候选：连续字节全部 <= 0x3F（NES 颜色索引上限）。

    NES 调色板一次写入 $3F00 的通常是 16 或 32 字节（4 组 / 8 组各 4 色）。
    """
    out = []
    n = len(prg)
    i = 0
    while i < n:
        if not is_data[i] or prg[i] > 0x3F:
            i += 1
            continue
        j = i
        while j < n and is_data[j] and prg[j] <= 0x3F and (j - i) < max_len:
            j += 1
        run = j - i
        if run >= min_len:
            out.append({
                "offset": i,
                "length": run,
                "bus_addr": 0x8000 + i,
                "bytes": prg[i:j].hex(),
            })
            i = j
        else:
            i += 1
    return out


def find_pointer_tables(prg: bytes, is_data: bytearray,
                        min_entries: int = 8) -> list[dict]:
    """指针表候选：连续 2 字节小端值均落在 $8000-$FFFF。

    这个过滤器本身很松（随机数据也很容易连中 8 项），因此额外打分：
      +3  单调不减（关卡/字幕表通常是顺序表）
      +2  最大步长 <= 0x800（表项大小有限，不像随机游走）
      +2  多数目标落在数据区（表项应该指数据，不是指代码）
      +1  项数 >= 16
    score >= 5 才算「值得人工验证」，其余只在 CSV 里留档。
    """
    n = len(prg)
    out = []
    i = 0
    while i + 1 < n:
        if not is_data[i] or not is_data[i + 1]:
            i += 1
            continue
        vals: list[int] = []
        p = i
        while p + 1 < n and is_data[p] and is_data[p + 1]:
            v = prg[p] | (prg[p + 1] << 8)
            if not (0x8000 <= v <= 0xFFFF):
                break
            vals.append(v)
            p += 2
        if len(vals) >= min_entries:
            diffs = [vals[k + 1] - vals[k] for k in range(len(vals) - 1)]
            monotonic = all(d >= 0 for d in diffs)
            max_d = max(diffs) if diffs else 0
            min_d = min(diffs) if diffs else 0

            in_data = sum(1 for v in vals if is_data[(v - 0x8000) % n])
            score = 0
            if monotonic:
                score += 3
            if max_d <= 0x800:
                score += 2
            if in_data * 2 >= len(vals):
                score += 2
            if len(vals) >= 16:
                score += 1

            out.append({
                "offset": i,
                "entries": len(vals),
                "bus_addr": 0x8000 + i,
                "first": vals[0],
                "last": vals[-1],
                "monotonic": monotonic,
                "min_diff": min_d,
                "max_diff": max_d,
                "in_data": in_data,
                "score": score,
                "values": vals,
            })
            i = p
        else:
            i += 1
    out.sort(key=lambda r: (-r["score"], r["offset"]))
    return out


def find_ascii(prg: bytes, min_len: int = 5) -> list[dict]:
    out = []
    cur = bytearray()
    start = 0
    for i, b in enumerate(prg + b"\x00"):
        printable = 0x20 <= b <= 0x7E
        if printable:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append({
                    "offset": start,
                    "bus_addr": 0x8000 + start,
                    "text": cur.decode("ascii", "replace"),
                })
            cur = bytearray()
    return out


def md_table(rows: list[dict], headers: list[tuple[str, str]]) -> str:
    if not rows:
        return "_（无）_\n"
    buf = io.StringIO()
    buf.write("| " + " | ".join(h[0] for h in headers) + " |\n")
    buf.write("|" + "|".join("---" for _ in headers) + "|\n")
    for r in rows:
        cells = []
        for _, key in headers:
            v = r.get(key, "")
            cells.append(str(v).replace("|", "\\|"))
        buf.write("| " + " | ".join(cells) + " |\n")
    return buf.getvalue()


# ------------------------------------------------------------------ 主流程

def main() -> None:
    for d in (DATA, EVID, GAME / "L2-spec"):
        d.mkdir(parents=True, exist_ok=True)

    rom_path = find_rom()
    rom = nesrom.load(rom_path)
    prg = nesrom.Prg(rom.prg)

    print(f"[P0] ROM      : {rom_path.name}  ({rom.filesize} 字节)")
    print(f"[P0] SHA-256  : {rom.sha256}")
    print(f"[P0] mapper   : {rom.mapper} ({rom.mapper_name})  mirroring={rom.mirroring}  tv={rom.tv}")
    print(f"[P0] PRG/CHR  : {rom.prg_size} / {rom.chr_size} 字节   CHR 图块 {rom.chr_tiles}")

    # ---- 证据与原始段
    (EVID / "rom.sha256").write_text(
        f"{rom.sha256}  {rom_path.name}\n", encoding="utf-8")
    (DATA / "prg.bin").write_bytes(rom.prg)
    (DATA / "chr.bin").write_bytes(rom.chr)
    (DATA / "rominfo.json").write_text(
        json.dumps(rom.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- 向量与代码发现
    vec = prg.vectors()
    print(f"[P1] 向量     : NMI=${vec['nmi']:04X}  RESET=${vec['reset']:04X}  IRQ=${vec['irq']:04X}")

    entries = [vec["reset"], vec["nmi"], vec["irq"]]
    disc = dis6502.find_code(rom.prg, entries)
    is_data = bytearray(1 - v for v in disc.code_mask)
    # 传 code_mask（而不是 is_data）：data_regions 收集的是 mask==0 的区间
    regions = data_regions(disc.code_mask, 8)

    code_pct = 100 * disc.code_bytes / rom.prg_size
    print(f"[P1] 代码     : {disc.code_bytes} 字节 ({code_pct:.1f}%)  数据区 {len(regions)} 段")

    (DATA / "disasm_partial.txt").write_text(dis6502.listing(disc) + "\n", encoding="utf-8")

    with open(DATA / "code_coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file_off", "bus_addr", "size",
                                          "code_bytes", "data_bytes", "code_ratio"])
        w.writeheader()
        w.writerows(dis6502.coverage_rows(disc, 256))

    # ---- 表普查
    pals = find_palette_candidates(rom.prg, is_data)
    ptrs = find_pointer_tables(rom.prg, is_data)
    strs = find_ascii(rom.prg)

    print(f"[P1] 调色板候选 : {len(pals)}")
    print(f"[P1] 指针表候选 : {len(ptrs)}")
    print(f"[P1] ASCII 串   : {len(strs)}")

    rows = []
    for p in pals:
        rows.append({"kind": "palette", "offset": f"0x{p['offset']:04X}",
                     "bus_addr": f"${p['bus_addr']:04X}", "size": p["length"],
                     "detail": p["bytes"]})
    for p in ptrs:
        rows.append({"kind": "pointer_table", "offset": f"0x{p['offset']:04X}",
                     "bus_addr": f"${p['bus_addr']:04X}", "size": p["entries"] * 2,
                     "detail": f"{p['entries']} entries  ${p['first']:04X}..${p['last']:04X}"
                               f"  monotonic={p['monotonic']}  d=[{p['min_diff']},{p['max_diff']}]"})
    for s in strs:
        rows.append({"kind": "ascii", "offset": f"0x{s['offset']:04X}",
                     "bus_addr": f"${s['bus_addr']:04X}", "size": len(s["text"]),
                     "detail": s["text"]})

    with open(DATA / "tables_candidates.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "offset", "bus_addr", "size", "detail"])
        w.writeheader()
        w.writerows(rows)

    (DATA / "ascii_strings.txt").write_text(
        "\n".join(f"0x{s['offset']:04X} ${s['bus_addr']:04X}  {s['text']}" for s in strs) + "\n",
        encoding="utf-8")

    # ---- 渲染
    render.render_tileset(rom.chr, str(DATA / "chr_tileset.png"),
                          cols=16, scale=4, palette4=render.GRAY4)
    render.render_tileset(rom.chr, str(DATA / "chr_tileset_indexed.png"),
                          cols=16, scale=4, palette4=render.GRAY4,
                          show_index=True, font_size=9)
    render.render_palette_candidates(rom.prg, pals, str(DATA / "palette_candidates.png"))

    colored_note = "无候选"
    if pals:
        c0 = pals[0]
        pal4 = render.palette_from_table(rom.prg, c0["offset"], 0, 4)
        render.render_tileset(rom.chr, str(DATA / "chr_tileset_colored.png"),
                              cols=16, scale=4, palette4=pal4)
        colored_note = f"0x{c0['offset']:04X} 的前 4 色"
    print(f"[P0] 图集     : chr_tileset.png / chr_tileset_indexed.png / chr_tileset_colored.png ({colored_note})")

    # ---- P0 报告
    sig_note = "OK (`NES\\x1a`)" if rom.header_ok else "**无效**"
    ver_note = "NES 2.0" if rom.nes2 else "iNES (旧版)"
    map_note = "无 bank 切换，PRG/CHR 直接映射" if rom.mapper == 0 else "有 bank 切换"

    p0 = io.StringIO()
    p0.write(f"""# P0 · ROM 编目报告 — {rom_path.stem}

> 本文件由 `tools/pipeline.py` 自动生成，请勿手改；结论性分析写在 `L1-design.md` / `L2-spec/`。
> 证据等级：本页全部为 **[已验证]**（直接来自文件字节）。

## 1. 身份

| 字段 | 值 |
|---|---|
| 文件名 | `{rom_path.name}` |
| 文件大小 | {rom.filesize} 字节 |
| SHA-256 | `{rom.sha256}` |
| iNES 签名 | {sig_note} |
| 头部版本 | {ver_note} |
| 头部字节 | `{rom.header.hex()}` |

## 2. 卡带结构

| 字段 | 值 | 说明 |
|---|---|---|
| Mapper | **{rom.mapper} ({rom.mapper_name})** | {map_note} |
| Submapper | {rom.submapper} | |
| Mirroring | {rom.mirroring} | |
| PRG ROM | {rom.prg_units} × 16KB = **{rom.prg_size} 字节** | 在 $8000-$FFFF |
| CHR ROM | {rom.chr_units} × 8KB = **{rom.chr_size} 字节** | {rom.chr_tiles} 个 8×8 图块 |
| PRG RAM | {rom.prg_ram_units} × 8KB | |
| 电池存档 | {'有' if rom.battery else '无'} | |
| Trainer | {'有' if rom.trainer else '无'} | |
| 四屏 | {'是' if rom.four_screen else '否'} | |
| TV 制式 | {rom.tv} | |
| 尾部多余字节 | {rom.trailing_bytes} | |

## 3. 中断向量（[已验证]，直接读 $FFFA-$FFFF）

| 向量 | 地址 | PRG 内偏移 |
|---|---|---|
| NMI | `${vec['nmi']:04X}` | `0x{nesrom.Prg(rom.prg).off(vec['nmi']):04X}` |
| RESET | `${vec['reset']:04X}` | `0x{nesrom.Prg(rom.prg).off(vec['reset']):04X}` |
| IRQ/BRK | `${vec['irq']:04X}` | `0x{nesrom.Prg(rom.prg).off(vec['irq']):04X}` |

## 4. 拆包产物

| 文件 | 内容 |
|---|---|
| `L3-data/prg.bin` | PRG ROM {rom.prg_size} 字节，偏移 0 = 总线 $8000 |
| `L3-data/chr.bin` | CHR ROM {rom.chr_size} 字节，每 16 字节一个图块 |
| `L3-data/rominfo.json` | 本页所有字段的机器可读版本 |
| `L3-data/chr_tileset.png` | 全部 {rom.chr_tiles} 个图块，灰度 4 级，4 倍放大 |
| `L3-data/chr_tileset_indexed.png` | 同上，带十六进制图块编号（用于后续指认） |
| `L3-data/chr_tileset_colored.png` | 用候选调色板着色（**[推断]**，配色未经运行时确认） |
| `L3-data/palette_candidates.png` | 静态扫描出的调色板候选色带 |
| `evidence/rom.sha256` | 校验和留痕 |

## 5. 已知局限

- CHR 若是 CHR RAM（本 ROM 为 CHR ROM，{rom.chr_size} 字节，不适用）
- 彩色图集用的是静态扫描的候选调色板；真实调色板由代码写入 `$3F00`，
  需 Mesen2 在运行时读取 PPU 调色板才能确认。
- 本 ROM mapper={rom.mapper}，无 bank 切换，PRG 偏移与总线地址一一对应：
  `bus = 0x8000 + file_off`（16KB 会镜像到 $C000-$FFFF）。
""")
    (GAME / "P0-rominfo.md").write_text(p0.getvalue(), encoding="utf-8")

    # ---- P1 报告
    big_regions = sorted(regions, key=lambda r: r[0] - r[1])[:20]
    reg_rows = [{"file": f"0x{a:04X}", "bus": f"${0x8000 + a:04X}",
                 "end": f"0x{b:04X}", "size": b - a} for a, b in big_regions]

    pal_rows = [{"off": f"0x{p['offset']:04X}", "bus": f"${p['bus_addr']:04X}",
                 "len": p["length"], "bytes": p["bytes"]} for p in pals[:30]]

    ptr_rows = [{"off": f"0x{p['offset']:04X}", "bus": f"${p['bus_addr']:04X}",
                 "entries": p["entries"],
                 "range": f"${p['first']:04X}..${p['last']:04X}",
                 "mono": "是" if p["monotonic"] else "否",
                 "diff": f"{p['min_diff']}..{p['max_diff']}",
                 "indata": f"{p['in_data']}/{p['entries']}",
                 "score": p["score"]} for p in ptrs[:25]]
    plaus = [p for p in ptrs if p["score"] >= 5]

    str_rows = [{"off": f"0x{s['offset']:04X}", "bus": f"${s['bus_addr']:04X}",
                 "text": s["text"]} for s in strs]

    p1 = io.StringIO()
    p1.write(f"""# P1 · 静态普查报告 — {rom_path.stem}

> 由 `tools/pipeline.py` 自动生成。证据等级：**全部为 [推断]**。
> 递归下降不是真实执行记录；必须用 Mesen2 的 CDL / 执行轨迹确认后才能升级为 [已验证]。

## 1. 代码发现概况

| 指标 | 值 |
|---|---|
| 入口（NMI/RESET/IRQ） | `${vec['nmi']:04X}` / `${vec['reset']:04X}` / `${vec['irq']:04X}` |
| 识别出的指令数 | {len(disc.ins)} |
| 代码字节 | {disc.code_bytes} / {rom.prg_size}（**{code_pct:.1f}%**） |
| 未识别（数据）字节 | {disc.data_bytes}（{100 - code_pct:.1f}%） |
| 数据区段数（≥8 字节） | {len(regions)} |
| 是否截断 | {'是（达到上限）' if disc.truncated else '否'} |

反汇编清单：`L3-data/disasm_partial.txt`（按地址排序，含数据间隙标注）
覆盖率分区表：`L3-data/code_coverage.csv`（每 256 字节一行）

## 2. 最大的数据区（优先当表看）

{md_table(reg_rows, [("文件偏移", "file"), ("总线地址", "bus"), ("结束(文件)", "end"), ("字节数", "size")])}
## 3. 调色板候选（连续字节 ≤ 0x3F）

共 {len(pals)} 个候选。渲染在 `L3-data/palette_candidates.png`，请肉眼挑选游戏真实配色。

{md_table(pal_rows, [("文件偏移", "off"), ("总线地址", "bus"), ("长度", "len"), ("字节", "bytes")])}
## 4. 指针表候选（连续 2 字节小端值 ∈ $8000-$FFFF，≥8 项）

共 {len(ptrs)} 个候选，其中 **{len(plaus)} 个值得人工验证（score ≥ 5）**。

打分规则（满分 8）：单调不减 +3；最大步长 ≤ 0x800 +2；多数目标落在数据区 +2；项数 ≥ 16 +1。
**score 低的一律视为噪声**——「8 个连续 2 字节值落在 $8000-$FFFF」是极松的约束，
随机数据也能满足，务必只看高分项。

{md_table(ptr_rows, [("文件偏移", "off"), ("总线地址", "bus"), ("项数", "entries"), ("指向范围", "range"), ("单调", "mono"), ("差值", "diff"), ("目标在数据区", "indata"), ("score", "score")])}
## 5. ASCII 字符串

共 {len(strs)} 条。FC 游戏的正文多在 CHR 里用自定编码，PRG 里通常是
版权/制作人员等 ASCII 串——可用于确认 ROM 版本与厂商。

{md_table(str_rows[:40], [("文件偏移", "off"), ("总线地址", "bus"), ("文本", "text")])}
## 6. 局限与下一步

**局限**
- 递归下降无法解析 `JMP ($xxxx)` 间接跳转，也无法跟随跳转表分派；
  因此「数据区」里可能混有未被引用的代码。
- 若某条 `JMP` 落进数据，会把数据误标为代码。
- 上述所有偏移都未与运行时行为对齐。

**下一步（按优先级）**
1. 用 Mesen2 跑起 ROM，开 CDL，玩到第一关，导出 `.cdl` →
   与本报告的代码分区对比，把 **[推断]** 升级为 **[已验证]**。
2. 用 CDL 修正后的分区重跑本脚本，`data_regions` 会显著收敛。
3. 逐个人眼确认第 3 节的调色板，找出实际使用的那一张。
4. 对第 4 节的指针表候选做「改一个字节看哪张图变」验证。
""")
    (GAME / "P1-tables.md").write_text(p1.getvalue(), encoding="utf-8")

    print("\n[完成] 产物：")
    for f in sorted(GAME.rglob("*")):
        if f.is_file():
            print(f"  {f.relative_to(GAME)}  ({f.stat().st_size} B)")


if __name__ == "__main__":
    main()
