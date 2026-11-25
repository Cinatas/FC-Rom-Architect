"""最小 NMOS 6502 解释器（用于执行覆盖探测，不是完整模拟器）。

定位
----
目标只有一个：**跑起来、记录哪些字节被当指令取过、哪些被当数据读过**。
不需要渲染、不需要声音、不追求逐周期精确。因此：

  - PPU 只做寄存器级 stub（$2002 vblank 位、$2006/$2007、$4014 OAM DMA）
  - APU 寄存器写入直接丢弃
  - 周期数是近似值（足够让 vblank 按合理节奏到来）
  - 十进制模式不实现（2A03 本来就没有）

opcode 表复用 dis6502.OPCODE，避免第二份手写表引入不一致。

已知与真机的差异（都在报告里标注）：
  - BRK 按 1 字节处理（真机会跳过 1 字节填充）
  - 周期数为近似，页面跨界惩罚做了但不精确
  - JMP ($xxFF) 实现了硬件的页内回绕 bug（真机行为）
"""
from __future__ import annotations

from dis6502 import MODE_LEN, OPCODE

# 标志位
FC, FZ, FI, FD, FB, FU, FV, FN = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80

# 各寻址模式的基准周期
BASE_CYCLES = {
    "imp": 2, "acc": 2, "imm": 2, "zp": 3, "zpx": 4, "zpy": 4,
    "abs": 4, "absx": 4, "absy": 4, "ind": 5, "izx": 6, "izy": 5, "rel": 2,
}


class CPU:
    __slots__ = ("bus", "a", "x", "y", "sp", "pc", "p", "cycles", "halted")

    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.p = FI | FU
        self.cycles = 0
        self.halted = False

    # ------------------------------------------------------------ 基础访问
    def _rd(self, addr: int) -> int:
        return self.bus.read(addr & 0xFFFF, code=False)

    def _wr(self, addr: int, v: int) -> None:
        self.bus.write(addr & 0xFFFF, v & 0xFF)

    def _fetch(self) -> int:
        v = self.bus.read(self.pc, code=True)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def _push(self, v: int) -> None:
        self._wr(0x0100 | self.sp, v)
        self.sp = (self.sp - 1) & 0xFF

    def _pop(self) -> int:
        self.sp = (self.sp + 1) & 0xFF
        return self._rd(0x0100 | self.sp)

    # ------------------------------------------------------------ 标志位
    def _zn(self, v: int) -> int:
        self.p = (self.p & ~(FZ | FN)) | (FZ if v == 0 else 0) | (v & FN)
        return v

    def _setc(self, c: bool) -> None:
        self.p = (self.p | FC) if c else (self.p & ~FC)

    def _setv(self, v: bool) -> None:
        self.p = (self.p | FV) if v else (self.p & ~FV)

    def _getc(self) -> int:
        return 1 if (self.p & FC) else 0

    # ------------------------------------------------------------ ALU
    def _adc(self, v: int) -> None:
        a = self.a
        r = a + v + self._getc()
        self._setv(bool((~(a ^ v) & (a ^ r)) & 0x80))
        self._setc(r > 0xFF)
        self.a = self._zn(r & 0xFF)

    def _sbc(self, v: int) -> None:
        a = self.a
        r = a - v - (1 - self._getc())
        self._setv(bool(((a ^ v) & (a ^ r)) & 0x80))
        self._setc(r >= 0)
        self.a = self._zn(r & 0xFF)

    def _cmp(self, reg: int, v: int) -> None:
        r = reg - v
        self._setc(r >= 0)
        self._zn(r & 0xFF)

    def _asl(self, v: int) -> int:
        self._setc(bool(v & 0x80))
        return self._zn((v << 1) & 0xFF)

    def _lsr(self, v: int) -> int:
        self._setc(bool(v & 0x01))
        return self._zn(v >> 1)

    def _rol(self, v: int) -> int:
        c = self._getc()
        self._setc(bool(v & 0x80))
        return self._zn(((v << 1) | c) & 0xFF)

    def _ror(self, v: int) -> int:
        c = self._getc()
        self._setc(bool(v & 0x01))
        return self._zn((v >> 1) | (c << 7))

    # ------------------------------------------------------------ 中断
    def _interrupt(self, vector: int, brk: bool) -> None:
        self._push((self.pc >> 8) & 0xFF)
        self._push(self.pc & 0xFF)
        p = self.p | FU
        p = (p | FB) if brk else (p & ~FB)
        self._push(p)
        self.p |= FI
        lo = self.bus.read(vector, code=False)
        hi = self.bus.read(vector + 1, code=False)
        self.pc = lo | (hi << 8)
        self.cycles += 7

    def nmi(self) -> None:
        self._interrupt(0xFFFA, brk=False)

    def irq(self) -> None:
        if not (self.p & FI):
            self._interrupt(0xFFFE, brk=False)

    # ------------------------------------------------------------ 单步
    def step(self) -> int:
        op = self._fetch()
        ent = OPCODE.get(op)
        if ent is None:
            # 非法指令：当作停机，交由上层记录现场
            self.halted = True
            return 2
        mne, mode = ent

        operand = None
        addr = None
        crossed = False

        if mode == "imp" or mode == "acc":
            pass
        elif mode == "imm":
            operand = self._fetch()
        elif mode == "zp":
            addr = self._fetch()
        elif mode == "zpx":
            addr = (self._fetch() + self.x) & 0xFF
        elif mode == "zpy":
            addr = (self._fetch() + self.y) & 0xFF
        elif mode == "abs":
            lo = self._fetch()
            hi = self._fetch()
            addr = lo | (hi << 8)
        elif mode in ("absx", "absy"):
            lo = self._fetch()
            hi = self._fetch()
            base = lo | (hi << 8)
            idx = self.x if mode == "absx" else self.y
            addr = (base + idx) & 0xFFFF
            crossed = (base & 0xFF00) != (addr & 0xFF00)
        elif mode == "ind":
            p = self._fetch()
            p |= self._fetch() << 8
            lo = self.bus.read(p, code=False)
            # 硬件 bug：高字节从同一页取
            hi = self.bus.read((p & 0xFF00) | ((p + 1) & 0xFF), code=False)
            addr = lo | (hi << 8)
        elif mode == "izx":
            p = (self._fetch() + self.x) & 0xFF
            lo = self._rd(p)
            hi = self._rd((p + 1) & 0xFF)
            addr = lo | (hi << 8)
        elif mode == "izy":
            p = self._fetch()
            lo = self._rd(p)
            hi = self._rd((p + 1) & 0xFF)
            base = lo | (hi << 8)
            addr = (base + self.y) & 0xFFFF
            crossed = (base & 0xFF00) != (addr & 0xFF00)
        elif mode == "rel":
            rel = self._fetch()
            if rel > 127:
                rel -= 256
            operand = rel

        cyc = BASE_CYCLES.get(mode, 3)

        # ---- 执行
        if mne == "LDA":
            self.a = self._zn(operand if mode == "imm" else self._rd(addr))
        elif mne == "LDX":
            self.x = self._zn(operand if mode == "imm" else self._rd(addr))
        elif mne == "LDY":
            self.y = self._zn(operand if mode == "imm" else self._rd(addr))
        elif mne == "STA":
            self._wr(addr, self.a)
        elif mne == "STX":
            self._wr(addr, self.x)
        elif mne == "STY":
            self._wr(addr, self.y)
        elif mne == "ADC":
            self._adc(operand if mode == "imm" else self._rd(addr))
        elif mne == "SBC":
            self._sbc(operand if mode == "imm" else self._rd(addr))
        elif mne == "AND":
            self.a = self._zn(self.a & (operand if mode == "imm" else self._rd(addr)))
        elif mne == "ORA":
            self.a = self._zn(self.a | (operand if mode == "imm" else self._rd(addr)))
        elif mne == "EOR":
            self.a = self._zn(self.a ^ (operand if mode == "imm" else self._rd(addr)))
        elif mne == "CMP":
            self._cmp(self.a, operand if mode == "imm" else self._rd(addr))
        elif mne == "CPX":
            self._cmp(self.x, operand if mode == "imm" else self._rd(addr))
        elif mne == "CPY":
            self._cmp(self.y, operand if mode == "imm" else self._rd(addr))
        elif mne == "BIT":
            v = self._rd(addr)
            self.p = (self.p & ~(FZ | FN | FV)) | (v & (FN | FV)) | (FZ if (self.a & v) == 0 else 0)
        elif mne == "INC":
            self._wr(addr, self._zn((self._rd(addr) + 1) & 0xFF))
        elif mne == "DEC":
            self._wr(addr, self._zn((self._rd(addr) - 1) & 0xFF))
        elif mne == "INX":
            self.x = self._zn((self.x + 1) & 0xFF)
        elif mne == "INY":
            self.y = self._zn((self.y + 1) & 0xFF)
        elif mne == "DEX":
            self.x = self._zn((self.x - 1) & 0xFF)
        elif mne == "DEY":
            self.y = self._zn((self.y - 1) & 0xFF)
        elif mne == "ASL":
            if mode == "acc":
                self.a = self._asl(self.a)
            else:
                self._wr(addr, self._asl(self._rd(addr)))
        elif mne == "LSR":
            if mode == "acc":
                self.a = self._lsr(self.a)
            else:
                self._wr(addr, self._lsr(self._rd(addr)))
        elif mne == "ROL":
            if mode == "acc":
                self.a = self._rol(self.a)
            else:
                self._wr(addr, self._rol(self._rd(addr)))
        elif mne == "ROR":
            if mode == "acc":
                self.a = self._ror(self.a)
            else:
                self._wr(addr, self._ror(self._rd(addr)))
        elif mne == "TAX":
            self.x = self._zn(self.a)
        elif mne == "TAY":
            self.y = self._zn(self.a)
        elif mne == "TXA":
            self.a = self._zn(self.x)
        elif mne == "TYA":
            self.a = self._zn(self.y)
        elif mne == "TSX":
            self.x = self._zn(self.sp)
        elif mne == "TXS":
            self.sp = self.x
        elif mne == "PHA":
            self._push(self.a)
        elif mne == "PHP":
            self._push(self.p | FB | FU)
        elif mne == "PLA":
            self.a = self._zn(self._pop())
        elif mne == "PLP":
            self.p = (self._pop() & ~FB) | FU
        elif mne == "JMP":
            self.pc = addr
        elif mne == "JSR":
            ret = (self.pc - 1) & 0xFFFF
            self._push((ret >> 8) & 0xFF)
            self._push(ret & 0xFF)
            self.pc = addr
        elif mne == "RTS":
            lo = self._pop()
            hi = self._pop()
            self.pc = (((hi << 8) | lo) + 1) & 0xFFFF
        elif mne == "RTI":
            self.p = (self._pop() & ~FB) | FU
            lo = self._pop()
            hi = self._pop()
            self.pc = (hi << 8) | lo
        elif mne == "BRK":
            self.pc = (self.pc + 1) & 0xFFFF   # 跳过填充字节
            self._interrupt(0xFFFE, brk=True)
        elif mne == "SEC":
            self._setc(True)
        elif mne == "CLC":
            self._setc(False)
        elif mne == "SED":
            self.p |= FD
        elif mne == "CLD":
            self.p &= ~FD
        elif mne == "SEI":
            self.p |= FI
        elif mne == "CLI":
            self.p &= ~FI
        elif mne == "CLV":
            self._setv(False)
        elif mne == "NOP":
            pass
        elif mne in ("BCC", "BCS", "BEQ", "BMI", "BNE", "BPL", "BVC", "BVS"):
            cond = {
                "BCC": not (self.p & FC), "BCS": bool(self.p & FC),
                "BEQ": bool(self.p & FZ), "BNE": not (self.p & FZ),
                "BMI": bool(self.p & FN), "BPL": not (self.p & FN),
                "BVC": not (self.p & FV), "BVS": bool(self.p & FV),
            }[mne]
            if cond:
                target = (self.pc + operand) & 0xFFFF
                self.cycles += 1
                if (target & 0xFF00) != (self.pc & 0xFF00):
                    self.cycles += 1
                self.pc = target
        else:
            self.halted = True
            return 2

        if crossed and mne in ("LDA", "LDX", "LDY", "ADC", "SBC", "AND", "ORA", "EOR",
                               "CMP", "CPX", "CPY", "BIT"):
            cyc += 1
        self.cycles += cyc
        return cyc
