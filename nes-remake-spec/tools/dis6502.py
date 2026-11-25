"""6502 (NMOS) 反汇编 + 递归下降代码发现。

为什么需要它
------------
ROM 里代码与数据表混排，没有符号。纯线性扫描（线性 sweep）会把查表数据
当成指令、把指令当成数据。递归下降从真实入口（NMI/RESET/IRQ 向量）出发，
只沿着真实控制流前进，能在没有模拟器 CDL 的情况下给出一个不错的
「代码 / 数据」分区。

证据等级
--------
本模块产出的一切都是 [推断]：
  - 代码区：被递归下降到达 => 大概率代码
  - 数据区：未被到达 => 可能是数据，也可能是未被引用的代码（如跳转表目标）
必须用 Mesen2 的 CDL（实际执行记录）或执行轨迹来确认，才能升级为 [已验证]。

已知局限
--------
  - 间接跳转 JMP ($xxxx) 无法解析目标，会在此处终止路径
  - 跳转表/状态机分派会让部分代码路径漏掉
  - 若某条 JMP 落进数据中，递归下降会把数据误标为代码
"""
from __future__ import annotations

from dataclasses import dataclass, field

MODE_LEN = {
    "imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2,
    "abs": 3, "absx": 3, "absy": 3, "ind": 3, "izx": 2, "izy": 2, "rel": 2,
}

MODE_FMT = {
    "imp": "", "acc": "A", "imm": "#$%02X", "zp": "$%02X", "zpx": "$%02X,X",
    "zpy": "$%02X,Y", "abs": "$%04X", "absx": "$%04X,X", "absy": "$%04X,Y",
    "ind": "($%04X)", "izx": "($%02X,X)", "izy": "($%02X),Y", "rel": "$%04X",
}

# mnemonic -> {addressing mode: opcode}
_SPEC: dict[str, dict[str, int]] = {
    "ADC": {"imm": 0x69, "zp": 0x65, "zpx": 0x75, "abs": 0x6D, "absx": 0x7D, "absy": 0x79, "izx": 0x61, "izy": 0x71},
    "AND": {"imm": 0x29, "zp": 0x25, "zpx": 0x35, "abs": 0x2D, "absx": 0x3D, "absy": 0x39, "izx": 0x21, "izy": 0x31},
    "ASL": {"acc": 0x0A, "zp": 0x06, "zpx": 0x16, "abs": 0x0E, "absx": 0x1E},
    "BIT": {"zp": 0x24, "abs": 0x2C},
    "BRK": {"imp": 0x00},
    "CLC": {"imp": 0x18}, "CLD": {"imp": 0xD8}, "CLI": {"imp": 0x58}, "CLV": {"imp": 0xB8},
    "CMP": {"imm": 0xC9, "zp": 0xC5, "zpx": 0xD5, "abs": 0xCD, "absx": 0xDD, "absy": 0xD9, "izx": 0xC1, "izy": 0xD1},
    "CPX": {"imm": 0xE0, "zp": 0xE4, "abs": 0xEC},
    "CPY": {"imm": 0xC0, "zp": 0xC4, "abs": 0xCC},
    "DEC": {"zp": 0xC6, "zpx": 0xD6, "abs": 0xCE, "absx": 0xDE},
    "DEX": {"imp": 0xCA}, "DEY": {"imp": 0x88},
    "EOR": {"imm": 0x49, "zp": 0x45, "zpx": 0x55, "abs": 0x4D, "absx": 0x5D, "absy": 0x59, "izx": 0x41, "izy": 0x51},
    "INC": {"zp": 0xE6, "zpx": 0xF6, "abs": 0xEE, "absx": 0xFE},
    "INX": {"imp": 0xE8}, "INY": {"imp": 0xC8},
    "JMP": {"abs": 0x4C, "ind": 0x6C},
    "JSR": {"abs": 0x20},
    "LDA": {"imm": 0xA9, "zp": 0xA5, "zpx": 0xB5, "abs": 0xAD, "absx": 0xBD, "absy": 0xB9, "izx": 0xA1, "izy": 0xB1},
    "LDX": {"imm": 0xA2, "zp": 0xA6, "zpy": 0xB6, "abs": 0xAE, "absy": 0xBE},
    "LDY": {"imm": 0xA0, "zp": 0xA4, "zpx": 0xB4, "abs": 0xAC, "absx": 0xBC},
    "LSR": {"acc": 0x4A, "zp": 0x46, "zpx": 0x56, "abs": 0x4E, "absx": 0x5E},
    "NOP": {"imp": 0xEA},
    "ORA": {"imm": 0x09, "zp": 0x05, "zpx": 0x15, "abs": 0x0D, "absx": 0x1D, "absy": 0x19, "izx": 0x01, "izy": 0x11},
    "PHA": {"imp": 0x48}, "PHP": {"imp": 0x08}, "PLA": {"imp": 0x68}, "PLP": {"imp": 0x28},
    "ROL": {"acc": 0x2A, "zp": 0x26, "zpx": 0x36, "abs": 0x2E, "absx": 0x3E},
    "ROR": {"acc": 0x6A, "zp": 0x66, "zpx": 0x76, "abs": 0x6E, "absx": 0x7E},
    "RTI": {"imp": 0x40}, "RTS": {"imp": 0x60},
    "SBC": {"imm": 0xE9, "zp": 0xE5, "zpx": 0xF5, "abs": 0xED, "absx": 0xFD, "absy": 0xF9, "izx": 0xE1, "izy": 0xF1},
    "SEC": {"imp": 0x38}, "SED": {"imp": 0xF8}, "SEI": {"imp": 0x78},
    "STA": {"zp": 0x85, "zpx": 0x95, "abs": 0x8D, "absx": 0x9D, "absy": 0x99, "izx": 0x81, "izy": 0x91},
    "STX": {"zp": 0x86, "zpy": 0x96, "abs": 0x8E},
    "STY": {"zp": 0x84, "zpx": 0x94, "abs": 0x8C},
    "TAX": {"imp": 0xAA}, "TAY": {"imp": 0xA8}, "TSX": {"imp": 0xBA},
    "TXA": {"imp": 0x8A}, "TXS": {"imp": 0x9A}, "TYA": {"imp": 0x98},
    "BCC": {"rel": 0x90}, "BCS": {"rel": 0xB0}, "BEQ": {"rel": 0xF0}, "BMI": {"rel": 0x30},
    "BNE": {"rel": 0xD0}, "BPL": {"rel": 0x10}, "BVC": {"rel": 0x50}, "BVS": {"rel": 0x70},
}

OPCODE: dict[int, tuple[str, str]] = {}
for _mne, _modes in _SPEC.items():
    for _mode, _op in _modes.items():
        assert _op not in OPCODE, f"opcode 冲突 0x{_op:02X} ({_mne} vs {OPCODE[_op]})"
        OPCODE[_op] = (_mne, _mode)
assert len(OPCODE) == 151, f"官方 6502 指令数应为 151，实际 {len(OPCODE)}"

BRANCHES = {"BCC", "BCS", "BEQ", "BMI", "BNE", "BPL", "BVC", "BVS"}
STOPS = {"RTS", "RTI", "BRK"}


@dataclass
class Ins:
    off: int          # PRG 内文件偏移
    addr: int         # 总线地址
    opcode: int
    mne: str
    mode: str
    raw: bytes
    operand: int | None = None
    target: int | None = None   # 分支 / JMP / JSR 的目标地址

    @property
    def length(self) -> int:
        return len(self.raw)

    @property
    def text(self) -> str:
        if self.mode == "imp":
            return self.mne
        if self.mode == "acc":
            return f"{self.mne} A"
        return f"{self.mne} {MODE_FMT[self.mode] % self.operand}"

    @property
    def flow(self) -> str:
        if self.mne in BRANCHES:
            return "branch"
        if self.mne == "JSR":
            return "jsr"
        if self.mne == "JMP":
            return "jmp" if self.mode == "abs" else "jmp_ind"
        if self.mne in STOPS:
            return "stop"
        return "next"


def decode(prg: bytes, off: int) -> Ins | None:
    """解码 off 处的一条指令；未知操作码（含非法指令）返回 None。"""
    if off < 0 or off >= len(prg):
        return None
    op = prg[off]
    if op not in OPCODE:
        return None
    mne, mode = OPCODE[op]
    ln = MODE_LEN[mode]
    if off + ln > len(prg):
        return None

    addr = 0x8000 + off
    operand = None
    target = None
    if mode in ("imm", "zp", "zpx", "zpy", "izx", "izy"):
        operand = prg[off + 1]
    elif mode in ("abs", "absx", "absy", "ind"):
        operand = prg[off + 1] | (prg[off + 2] << 8)
    elif mode == "rel":
        rel = prg[off + 1]
        if rel > 127:
            rel -= 256
        target = (addr + 2 + rel) & 0xFFFF
        operand = target

    if mne == "JMP" and mode == "abs":
        target = operand
    elif mne == "JSR":
        target = operand

    return Ins(off=off, addr=addr, opcode=op, mne=mne, mode=mode,
               raw=prg[off:off + ln], operand=operand, target=target)


@dataclass
class Discover:
    ins: dict[int, Ins] = field(default_factory=dict)   # off -> Ins
    code_mask: bytearray = field(default_factory=bytearray)
    entry_used: list[int] = field(default_factory=list)
    truncated: bool = False

    @property
    def code_bytes(self) -> int:
        return sum(self.code_mask)

    @property
    def data_bytes(self) -> int:
        return len(self.code_mask) - self.code_bytes


def to_off(addr: int, prg_len: int) -> int:
    return (addr - 0x8000) % prg_len


def find_code(prg: bytes, entries: list[int], max_ins: int = 200_000) -> Discover:
    """从入口列表出发做递归下降。"""
    d = Discover(code_mask=bytearray(len(prg)))
    work = [a for a in entries if 0x8000 <= a <= 0xFFFF]
    d.entry_used = list(work)

    count = 0
    while work:
        addr = work.pop()
        while True:
            if count >= max_ins:
                d.truncated = True
                return d
            off = to_off(addr, len(prg))
            if off in d.ins:
                break
            ins = decode(prg, off)
            if ins is None:
                break
            d.ins[off] = ins
            count += 1
            for i in range(off, min(off + ins.length, len(prg))):
                d.code_mask[i] = 1

            flow = ins.flow
            nxt = (ins.addr + ins.length) & 0xFFFF

            if flow == "stop":
                break
            if flow == "jmp":
                if ins.target is not None:
                    work.append(ins.target)
                break
            if flow == "jmp_ind":
                break
            if flow == "branch":
                if ins.target is not None:
                    work.append(ins.target)
                addr = nxt
                continue
            if flow == "jsr":
                if ins.target is not None:
                    work.append(ins.target)
                addr = nxt
                continue
            addr = nxt
    return d


def listing(d: Discover) -> str:
    """把递归下降结果渲染成可读清单。"""
    out = []
    prev_end = 0
    for off in sorted(d.ins):
        if off != prev_end:
            gap = off - prev_end
            out.append(f"; --- 数据间隙 {gap} 字节 @ file 0x{prev_end:04X} (${0x8000 + prev_end:04X}) ---")
        ins = d.ins[off]
        hexs = " ".join(f"{b:02X}" for b in ins.raw)
        out.append(f"{ins.addr:04X}  {hexs:<9} {ins.text}")
        prev_end = off + ins.length
    if prev_end < len(d.code_mask):
        out.append(f"; --- 尾部数据 {len(d.code_mask) - prev_end} 字节 @ file 0x{prev_end:04X} ---")
    return "\n".join(out)


def coverage_rows(d: Discover, block: int = 256) -> list[dict]:
    rows = []
    n = len(d.code_mask)
    for start in range(0, n, block):
        end = min(start + block, n)
        c = sum(d.code_mask[start:end])
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
