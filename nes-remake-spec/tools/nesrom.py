"""iNES / NES 2.0 头解析与 ROM 拆包。

职责边界：只做「读头 + 拆段 + 编目」，不做任何反汇编判断。
反汇编与代码发现见 dis6502.py。

证据等级说明：
  本模块产出的字段全部是 [已验证] —— 直接来自文件字节，无推断成分。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

MIRRORING = {0: "horizontal", 1: "vertical"}
TV_SYSTEM = {0: "NTSC", 1: "PAL", 2: "dual", 3: "Dendy"}

# 常见 mapper 名，仅用于报告可读性；未知编号照实打印数字
MAPPER_NAMES = {
    0: "NROM",
    1: "MMC1",
    2: "UxROM",
    3: "CNROM",
    4: "MMC3 / MMC6",
    5: "MMC5",
    7: "AxROM",
    9: "MMC2",
    10: "MMC4",
    11: "Color Dreams",
    34: "BNROM / NINA-001",
    66: "GxROM",
    69: "Sunsoft FME-7",
}


@dataclass
class RomImage:
    path: str
    filesize: int
    sha256: str
    header_ok: bool
    header: bytes
    nes2: bool
    prg_units: int
    chr_units: int
    prg: bytes
    chr: bytes
    mapper: int
    submapper: int
    mirroring: str
    battery: bool
    trainer: bool
    four_screen: bool
    tv: str
    prg_ram_units: int
    trailing_bytes: int

    @property
    def prg_size(self) -> int:
        return len(self.prg)

    @property
    def chr_size(self) -> int:
        return len(self.chr)

    @property
    def has_chr_rom(self) -> bool:
        return self.chr_size > 0

    @property
    def mapper_name(self) -> str:
        return MAPPER_NAMES.get(self.mapper, "unknown")

    @property
    def chr_tiles(self) -> int:
        """CHR ROM 中的 8x8 图块数（每块 16 字节）。"""
        return self.chr_size // 16

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "filesize": self.filesize,
            "sha256": self.sha256,
            "header_ok": self.header_ok,
            "header_hex": self.header.hex(),
            "nes2": self.nes2,
            "mapper": self.mapper,
            "mapper_name": self.mapper_name,
            "submapper": self.submapper,
            "mirroring": self.mirroring,
            "battery": self.battery,
            "trainer": self.trainer,
            "four_screen": self.four_screen,
            "tv": self.tv,
            "prg_units": self.prg_units,
            "prg_size": self.prg_size,
            "chr_units": self.chr_units,
            "chr_size": self.chr_size,
            "chr_tiles": self.chr_tiles,
            "prg_ram_units": self.prg_ram_units,
            "trailing_bytes": self.trailing_bytes,
        }


def load(path: str | Path) -> RomImage:
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) < 16:
        raise ValueError(f"文件小于 16 字节，不是有效 iNES 文件: {len(raw)}")

    h = raw[:16]
    header_ok = h[:4] == b"NES\x1a"
    nes2 = (h[7] & 0x0C) == 0x08

    f6, f7 = h[6], h[7]
    mapper = (f7 & 0xF0) | (f6 >> 4)
    submapper = 0
    prg_units = h[4]
    chr_units = h[5]

    if nes2:
        mapper = ((h[8] & 0x0F) << 8) | (f7 & 0xF0) | (f6 >> 4)
        submapper = h[8] >> 4
        prg_units |= (h[9] & 0x0F) << 8
        chr_units |= (h[9] & 0xF0) << 4

    trainer = bool(f6 & 0x04)
    battery = bool(f6 & 0x02)
    four_screen = bool(f6 & 0x08)
    mirroring = "four-screen" if four_screen else MIRRORING[(f6 & 0x01)]
    tv = TV_SYSTEM[h[9] & 0x03]
    prg_ram_units = h[8] if not nes2 else (h[10] & 0x0F)

    start = 16 + (512 if trainer else 0)
    prg_size = prg_units * 16384
    chr_size = chr_units * 8192
    prg = raw[start:start + prg_size]
    chr_ = raw[start + prg_size:start + prg_size + chr_size]
    trailing = len(raw) - (start + prg_size + chr_size)

    return RomImage(
        path=str(p),
        filesize=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        header_ok=header_ok,
        header=h,
        nes2=nes2,
        prg_units=prg_units,
        chr_units=chr_units,
        prg=prg,
        chr=chr_,
        mapper=mapper,
        submapper=submapper,
        mirroring=mirroring,
        battery=battery,
        trainer=trainer,
        four_screen=four_screen,
        tv=tv,
        prg_ram_units=prg_ram_units,
        trailing_bytes=trailing,
    )


# ---------------------------------------------------------------- PRG 地址映射

class Prg:
    """PRG 总线地址 ($8000-$FFFF) 与文件内偏移的映射。

    NROM-128 (16KB) 会镜像到 $C000-$FFFF，因此用取模实现；
    NROM-256 (32KB) 取模后与原地址一一对应，同样成立。
    """

    def __init__(self, data: bytes):
        self.data = data
        self.size = len(data)

    def off(self, addr: int) -> int:
        return (addr - 0x8000) % self.size

    def in_space(self, addr: int) -> bool:
        return 0x8000 <= addr <= 0xFFFF

    def read8(self, addr: int) -> int:
        return self.data[self.off(addr)]

    def read16(self, addr: int) -> int:
        lo = self.read8(addr)
        hi = self.read8(addr + 1)
        return lo | (hi << 8)

    def vectors(self) -> dict:
        """读 NMI / RESET / IRQ 三个向量。"""
        return {
            "nmi": self.read16(0xFFFA),
            "reset": self.read16(0xFFFC),
            "irq": self.read16(0xFFFE),
        }
