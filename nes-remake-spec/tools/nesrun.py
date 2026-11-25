"""执行覆盖探测：用一个最小 NES 执行器跑出"哪些字节真的执行过"。

为什么不用 Mesen2
----------------
Mesen2 需要人操作。在拿到真 CDL 之前，用自建执行器先取一版覆盖，价值是：
  1. 立刻把静态递归下降的 40.4% 推高（补上跳转表/间接跳转的漏网路径）
  2. 验证 ROM 加载、镜像、向量假设是否成立（跑不起来就说明假设错了）
  3. 为后续真 CDL 提供对照基准

**这不是真 CDL。** 差异见 nes6502.py 文档串，报告里也会标注。

做法
----
同一个 ROM 跑多组输入脚本，把覆盖取并集：
  会话1 无输入      → 启动/初始化/标题/音乐引擎
  会话2 连按 Start  → 进菜单/进游戏
  会话3 频繁 Start  → 覆盖菜单与重开路径
  会话4 乱按方向+A  → 覆盖移动与放炸弹
不同脚本覆盖不同分支，并集才接近"完整执行过的代码"。

用法
----
    python tools\\nesrun.py            # 默认 4 个会话 × 600 帧
    python tools\\nesrun.py --frames 1200

产物
----
    L3-data/exec_code_mask.bin / exec_data_mask.bin
    L3-data/exec_coverage.csv
    L2-spec/_exec-coverage.md
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
ROOT = GAME.parent
DATA = GAME / "L3-data"

import dis6502      # noqa: E402
import nes6502      # noqa: E402
import nesrom       # noqa: E402

CYC_PER_FRAME = 29781          # NTSC, 29780.5
VECTORS = [0xC000, 0xC01A, 0xFFF9]

# NES 标准按键位序：A B Select Start Up Down Left Right
BTN = {"A": 0x01, "B": 0x02, "SEL": 0x04, "START": 0x08,
       "UP": 0x10, "DN": 0x20, "LT": 0x40, "RT": 0x80}


class Bus:
    """整机总线 + CDL 记录。"""

    def __init__(self, rom: nesrom.RomImage):
        self.rom = rom
        self.prg = rom.prg
        self.prg_size = len(rom.prg)
        self.chr = bytearray(rom.chr)
        self.ram = bytearray(0x800)

        self.code_mask = bytearray(self.prg_size)
        self.data_mask = bytearray(self.prg_size)

        # PPU 寄存器级 stub
        self.ppu_ctrl = 0
        self.ppu_mask = 0
        self.vblank = False
        self.ppu_addr = 0
        self.ppu_toggle = 0
        self.vram = bytearray(0x1000)     # 2KB nametable + 属性
        self.palette = bytearray(32)
        self.oam = bytearray(256)
        self.oam_addr = 0

        # 手柄
        self.pad_byte = 0
        self.pad_shift = 0
        self.pad_strobe = 0

        self.in_prg_reads = 0

    def _off(self, addr: int) -> int:
        return (addr - 0x8000) % self.prg_size

    # ------------------------------------------------------------ read
    def read(self, addr: int, code: bool = False) -> int:
        if addr < 0x2000:
            return self.ram[addr & 0x7FF]
        if addr < 0x4000:
            reg = 0x2000 + (addr & 7)
            if reg == 0x2002:
                v = 0x80 if self.vblank else 0x00
                self.vblank = False          # 读 $2002 清 vblank
                self.ppu_toggle = 0
                return v | 0x00
            if reg == 0x2004:
                return self.oam[self.oam_addr]
            if reg == 0x2007:
                a = self.ppu_addr & 0x3FFF
                self.ppu_addr = (self.ppu_addr + (32 if self.ppu_ctrl & 0x04 else 1)) & 0xFFFF
                if a >= 0x3F00:
                    return self.palette[a & 0x1F]
                return self.vram[a & 0x0FFF]
            return 0
        if addr < 0x4020:
            if addr == 0x4016:
                v = self.pad_shift & 1
                if not self.pad_strobe:
                    self.pad_shift = (self.pad_shift >> 1) | 0x80
                return v
            if addr == 0x4017:
                return 0
            return 0
        if addr >= 0x8000:
            off = self._off(addr)
            if code:
                self.code_mask[off] = 1
            else:
                self.data_mask[off] = 1
            self.in_prg_reads += 1
            return self.prg[off]
        return 0

    # ------------------------------------------------------------ write
    def write(self, addr: int, v: int) -> None:
        if addr < 0x2000:
            self.ram[addr & 0x7FF] = v
            return
        if addr < 0x4000:
            reg = 0x2000 + (addr & 7)
            if reg == 0x2000:
                self.ppu_ctrl = v
            elif reg == 0x2001:
                self.ppu_mask = v
            elif reg == 0x2003:
                self.oam_addr = v
            elif reg == 0x2004:
                self.oam[self.oam_addr] = v
                self.oam_addr = (self.oam_addr + 1) & 0xFF
            elif reg == 0x2005:
                self.ppu_toggle ^= 1
            elif reg == 0x2006:
                if self.ppu_toggle == 0:
                    self.ppu_addr = (self.ppu_addr & 0x00FF) | (v << 8)
                else:
                    self.ppu_addr = (self.ppu_addr & 0xFF00) | v
                self.ppu_toggle ^= 1
            elif reg == 0x2007:
                a = self.ppu_addr & 0x3FFF
                self.ppu_addr = (self.ppu_addr + (32 if self.ppu_ctrl & 0x04 else 1)) & 0xFFFF
                if a >= 0x3F00:
                    self.palette[a & 0x1F] = v
                else:
                    self.vram[a & 0x0FFF] = v
            return
        if addr < 0x4020:
            if addr == 0x4014:                       # OAM DMA
                base = (v << 8) & 0xFFFF
                for i in range(256):
                    self.oam[i] = self.read(base + i)
            elif addr == 0x4016:
                self.pad_strobe = v & 1
                if self.pad_strobe:
                    self.pad_shift = self.pad_byte
            return
        # $8000+ 写入：NROM 无写目标，忽略


class Machine:
    def __init__(self, rom: nesrom.RomImage):
        self.rom = rom
        self.bus = Bus(rom)
        self.cpu = nes6502.CPU(self.bus)
        # 复位向量直接从 PRG 取，不走总线（避免污染 CDL 数据掩码）
        n = rom.prg_size
        lo = rom.prg[(0xFFFC - 0x8000) % n]
        hi = rom.prg[(0xFFFD - 0x8000) % n]
        self.cpu.pc = lo | (hi << 8)
        self.cpu.sp = 0xFD
        self.frames = 0
        self.instr = 0
        self.stop_reason = None
        self.stuck_pc = None

    def run(self, pad_by_frame: dict[int, int], frames: int, max_instr: int) -> None:
        cpu, bus = self.cpu, self.bus
        next_vblank = CYC_PER_FRAME
        last_pc = -1
        same_pc = 0

        while self.frames < frames:
            if cpu.cycles >= next_vblank:
                bus.vblank = True
                self.frames += 1
                bus.pad_byte = pad_by_frame.get(self.frames, 0)
                if bus.ppu_ctrl & 0x80:
                    cpu.nmi()
                next_vblank += CYC_PER_FRAME

            if self.instr >= max_instr:
                self.stop_reason = f"达到指令上限 {max_instr}"
                return

            # 死循环检测：PC 连续原地打转
            if cpu.pc == last_pc:
                same_pc += 1
                if same_pc > 3_000_000:
                    self.stop_reason = f"疑似死循环 @ ${cpu.pc:04X}"
                    return
            else:
                last_pc = cpu.pc
                same_pc = 0

            cpu.step()
            self.instr += 1

            if cpu.halted:
                self.stuck_pc = cpu.pc
                self.stop_reason = f"非法操作码 @ ${cpu.pc:04X}"
                return
        self.stop_reason = "正常结束"


def find_rom() -> Path:
    p = ROOT / (GAME.name + ".nes")
    if p.exists():
        return p
    cands = sorted(ROOT.glob("*.nes"))
    if not cands:
        raise SystemExit("找不到 .nes")
    return cands[0]


def script_none(frames: int) -> dict[int, int]:
    return {}


def script_start_burst(frames: int) -> dict[int, int]:
    """周期性短按 Start —— 用于穿过标题/菜单。"""
    d = {}
    for start in range(120, frames, 45):
        for f in range(start, start + 4):
            d[f] = BTN["START"]
    return d


def script_start_then_play(frames: int) -> dict[int, int]:
    """先连按 Start 进游戏，然后周期性乱按方向 + A（放炸弹）。"""
    d = {}
    for start in range(120, 300, 40):
        for f in range(start, start + 4):
            d[f] = BTN["START"]
    seq = [BTN["RT"], BTN["DN"], BTN["LT"], BTN["UP"], BTN["A"], BTN["A"] | BTN["RT"]]
    f = 300
    i = 0
    while f < frames:
        b = seq[i % len(seq)]
        for k in range(f, min(f + 12, frames)):
            d[k] = b
        f += 12
        i += 1
    return d


def script_mash(frames: int) -> dict[int, int]:
    """从头到尾混按所有键 —— 覆盖菜单的偶然路径。"""
    d = {}
    keys = [BTN["START"], BTN["A"], BTN["B"], BTN["SEL"], BTN["START"] | BTN["A"]]
    for f in range(frames):
        if f % 7 < 3:
            d[f] = keys[(f // 7) % len(keys)]
    return d


SESSIONS = [
    ("无输入（启动/标题/音效）", script_none, 600),
    ("周期 Start（穿菜单）", script_start_burst, 600),
    ("进游戏后移动+放弹", script_start_then_play, 900),
    ("全键混按（兜底）", script_mash, 600),
]


def coverage_rows(mask: bytearray, block: int = 256) -> list[dict]:
    rows = []
    for start in range(0, len(mask), block):
        end = min(start + block, len(mask))
        c = sum(mask[start:end])
        total = end - start
        rows.append({"file_off": f"0x{start:04X}", "bus_addr": f"${0x8000 + start:04X}",
                     "size": total, "code_bytes": c, "data_bytes": total - c,
                     "code_ratio": round(c / total, 3)})
    return rows


def main() -> None:
    frames_arg = None
    if "--frames" in sys.argv:
        frames_arg = int(sys.argv[sys.argv.index("--frames") + 1])

    rom = nesrom.load(find_rom())
    print(f"ROM: {rom.path}  mapper={rom.mapper}  PRG={rom.prg_size}")

    static = dis6502.find_code(rom.prg, VECTORS)
    print(f"静态分区: {static.code_bytes}/{rom.prg_size} "
          f"({100 * static.code_bytes / rom.prg_size:.1f}%)\n")

    union_code = bytearray(rom.prg_size)
    union_data = bytearray(rom.prg_size)

    for name, maker, frames in SESSIONS:
        frames = frames_arg or frames
        pad = maker(frames)
        m = Machine(rom)
        m.run(pad, frames, max_instr=12_000_000)

        c = sum(m.bus.code_mask)
        d = sum(m.bus.data_mask)
        # 并集
        for i in range(rom.prg_size):
            if m.bus.code_mask[i]:
                union_code[i] = 1
            if m.bus.data_mask[i]:
                union_data[i] = 1
        print(f"[{name}]")
        print(f"    帧 {m.frames}/{frames}  指令 {m.instr:,}  停止原因: {m.stop_reason}")
        print(f"    本会话覆盖代码 {c} 字节 ({100 * c / rom.prg_size:.1f}%)，数据 {d} 字节")
        print(f"    累计并集代码 {sum(union_code)} 字节 "
              f"({100 * sum(union_code) / rom.prg_size:.1f}%)\n")

    # ---- 与静态比对
    both = sum(1 for i in range(rom.prg_size) if static.code_mask[i] and union_code[i])
    static_only = [i for i in range(rom.prg_size) if static.code_mask[i] and not union_code[i]]
    exec_only = [i for i in range(rom.prg_size) if union_code[i] and not static.code_mask[i]]

    recall = both / static.code_bytes if static.code_bytes else 0
    print("=== 静态 vs 执行 ===")
    print(f"  两者一致判为代码 : {both}")
    print(f"  仅静态（执行未到达，可能误判或未触发的分支）: {len(static_only)}")
    print(f"  仅执行（静态漏掉的路径，跳转表/间接跳转）: {len(exec_only)}")
    print(f"  静态代码被执行的召回率: {100 * recall:.1f}%")

    # ---- 产物
    (DATA / "exec_code_mask.bin").write_bytes(bytes(union_code))
    (DATA / "exec_data_mask.bin").write_bytes(bytes(union_data))
    with open(DATA / "exec_coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file_off", "bus_addr", "size",
                                          "code_bytes", "data_bytes", "code_ratio"])
        w.writeheader()
        w.writerows(coverage_rows(union_code))

    def ranges(idx, min_len=4):
        out, i = [], 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
                j += 1
            if idx[j] - idx[i] + 1 >= min_len:
                out.append((idx[i], idx[j] + 1))
            i = j + 1
        return out

    r_new = ranges(exec_only)
    md = [
        "# 执行覆盖报告（自建执行器）",
        "",
        "> **这不是 Mesen2 的 CDL。** 由 `tools/nesrun.py` 用最小 6502 执行器跑出，",
        "> 用于在拿到真 CDL 前先验证假设、补路径。差异见文末。",
        "",
        "## 会话",
        "",
        "| 会话 | 帧数 | 本会话代码覆盖 |",
        "|---|---|---|",
    ]
    for name, _, frames in SESSIONS:
        md.append(f"| {name} | {frames_arg or frames} | — |")
    md += [
        "",
        f"## 汇总",
        "",
        f"| 项 | 字节数 | 占 PRG |",
        f"|---|---|---|",
        f"| 执行覆盖的代码 | {sum(union_code)} | {100 * sum(union_code) / rom.prg_size:.1f}% |",
        f"| 执行覆盖的数据 | {sum(union_data)} | {100 * sum(union_data) / rom.prg_size:.1f}% |",
        f"| 静态分区代码 | {static.code_bytes} | {100 * static.code_bytes / rom.prg_size:.1f}% |",
        f"| 两法一致 | {both} | — |",
        f"| 仅静态 | {len(static_only)} | — |",
        f"| 仅执行 | {len(exec_only)} | — |",
        "",
        f"静态代码被执行到的比例：**{100 * recall:.1f}%**。",
        "这个数字越高，说明执行器跑得越对、静态分区越可信。",
        "",
        "## 仅执行到的代码区段（静态漏掉，≥4 字节）",
        "",
        "跳转表目标、间接跳转落点通常在这里。**下一轮反汇编优先看这些区段。**",
        "",
    ]
    if r_new:
        md += ["| 文件偏移 | 总线地址 | 字节数 |", "|---|---|---|"]
        for a, b in r_new[:40]:
            md.append(f"| `0x{a:04X}` | `${0x8000 + a:04X}` | {b - a} |")
    else:
        md.append("_（无）_")

    md += [
        "",
        "## 局限（必读）",
        "",
        "1. **无 PPU 渲染**：只有寄存器级 stub。依赖 sprite-0 hit 等待的游戏会卡住。",
        "2. **时序近似**：周期数按寻址模式估算，不是逐周期精确。",
        "3. **BRK 按 1 字节**：真机会跳过 1 字节填充。",
        "4. **只有 4 组固定输入脚本**：没玩到的地方不会被执行覆盖。",
        "5. **不是真 CDL**：真 CDL 由 Mesen2 记录，覆盖度必然高于本报告。",
        "",
        "→ 因此本报告用于**交叉验证与补路径**，不能替代 `evidence/*.cdl`。",
        "",
        "## 下一步",
        "",
        "1. 用 `exec_code_mask.bin` 修正分区后重跑数据表普查，假阳性会下降。",
        "2. 对上面「仅执行到」的区段做反汇编。",
        "3. 拿 Mesen2 的真 CDL 与本报告取交集——三方差集能定位执行器的错误。",
        "",
    ]
    out = GAME / "L2-spec" / "_exec-coverage.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\n产物: {out.relative_to(GAME)}")
    print("      L3-data/exec_code_mask.bin / exec_data_mask.bin / exec_coverage.csv")


if __name__ == "__main__":
    main()
