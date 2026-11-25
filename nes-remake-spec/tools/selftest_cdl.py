"""自测：验证 cdl.py 的布局识别与掩码解码逻辑。

为什么需要
----------
cdl.py 的核心风险是「把 CDL 布局猜错」——猜错会得出完全错误的代码/数据分区，
而且看起来很正常。所以给它配一个不依赖真实 CDL 的自测：
用静态递归下降的结果伪造一份「已知答案」的 CDL，检查工具能否还原。

这个测试不需要 ROM、不需要模拟器，可在任何机器上跑：
    python tools\\selftest_cdl.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
DATA = GAME / "L3-data"

import cdl          # noqa: E402
import dis6502      # noqa: E402

PRG = DATA / "prg.bin"
CHR = DATA / "chr.bin"

fails = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def make_fake_cdl(mask: bytearray, code_bit: int, data_bit: int,
                  layout: str, chr_size: int) -> bytes:
    """由掩码伪造 flag 字节流。"""
    body = bytes(code_bit if v else data_bit for v in mask)
    if layout == "prg":
        return body
    if layout == "prg+chr":
        return body + bytes(chr_size)
    if layout == "chr+prg":
        return bytes(chr_size) + body
    raise ValueError(layout)


def main() -> None:
    if not PRG.exists():
        raise SystemExit("先跑 pipeline.py 生成 L3-data/prg.bin")

    prg = PRG.read_bytes()
    chr_size = len(CHR.read_bytes()) if CHR.exists() else 8192

    disc = dis6502.find_code(prg, [0xC000, 0xC01A, 0xFFF9])
    truth = disc.code_mask
    print(f"基准：静态分区 {disc.code_bytes}/{len(prg)} 字节代码\n")

    print("1) 'prg' 布局 + code_bit=0x01 应被完全还原")
    fake = make_fake_cdl(truth, 0x01, 0x02, "prg", chr_size)
    m = cdl.build_mask(fake, "prg", 0x01, len(prg), chr_size)
    check("build_mask 长度正确", m is not None and len(m) == len(prg))
    check("掩码逐字节相同", m == truth)
    check("召回率 = 100%", cdl.recall(m, truth) == 1.0,
          f"得到 {cdl.recall(m, truth) * 100:.1f}%")

    print("\n2) 错误的 code_bit 应给出 0 召回率（说明能识别而非盲信）")
    m_wrong = cdl.build_mask(fake, "prg", 0x02, len(prg), chr_size)
    check("code_bit=0x02 时召回率为 0", cdl.recall(m_wrong, truth) == 0.0)

    print("\n3) 'chr+prg' 布局应被识别（PRG 段整体后移 chr_size）")
    fake2 = make_fake_cdl(truth, 0x01, 0x02, "chr+prg", chr_size)
    m2 = cdl.build_mask(fake2, "chr+prg", 0x01, len(prg), chr_size)
    check("chr+prg 也能还原", m2 == truth)

    print("\n4) 文件过短必须返回 None 而不是猜测")
    short = bytes(100)
    check("prg 布局下过短 -> None", cdl.build_mask(short, "prg", 0x01, len(prg), chr_size) is None)

    print("\n5) 覆盖率分块统计")
    rows = cdl.coverage_rows(truth, 256)
    check("块数正确", len(rows) == (len(prg) + 255) // 256, f"{len(rows)} 块")
    check("合计代码字节吻合", sum(r["code_bytes"] for r in rows) == disc.code_bytes)

    print()
    if fails:
        print(f"**{len(fails)} 项失败**：" + ", ".join(fails))
        raise SystemExit(1)
    print("全部通过：cdl.py 的布局识别与解码逻辑可信。")


if __name__ == "__main__":
    main()
