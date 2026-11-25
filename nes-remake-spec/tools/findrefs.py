"""镜像感知的引用反查。

为什么必须做镜像感知
--------------------
NROM-128 把 16KB PRG 镜像到 $C000-$FFFF，所以**同一份数据在代码里有两个合法地址**：
    $9C53  与  $DC53           （后者 = 前者 + 0x4000）
本 ROM 的文本流就被写成 `LDA $DC53,Y`。我第一版只搜 $9C53，16 个字符串地址
全部 0 引用，差点误判成「通过指针表访问」。

三类引用模式
------------
1. 直接指针：数据里存小端 16 位地址         → 搜 `lo hi` 字节对
2. 立即数拼地址：`LDA #lo / STA p / LDA #hi` → 搜 `A9 lo` 后 window 内出现 `A9 hi`
3. 绝对寻址 / 立即数高位：`LDA $9Cxx` / `LDA #$9C` → 用递归下降的指令表按「页」搜

用法
----
    python tools\\findrefs.py                       # 内置待查清单
    python tools\\findrefs.py $9CF2 $A388           # 指定地址（自动含镜像）
    python tools\\findrefs.py --page 9C,9D,9E       # 按页列出所有代码引用
    python tools\\findrefs.py --no-mirror $9C53     # 只看精确地址

证据等级：字节/指令匹配是 [已验证]；「该引用属于哪种语义」仍是 [推断]。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
PRG_PATH = GAME / "L3-data" / "prg.bin"

import dis6502  # noqa: E402

MIRROR = 0x4000

# 当前待查清单（随进展维护）
DEFAULT = [
    ("文本流基址（渲染器读它）", 0x9C53),
    ("ENTER:SECRET:CODE", 0x9CF2),
    ("REVO:EMAG", 0x9D62),
    ("精灵调色板表 $9D22", 0x9D22),
    ("密码字母表 AOFKCPGELBHMJDNI", 0xA388),
    ("$A5E6 分派表（8 项）", 0xA5E6),
]


def parse_addr(s: str) -> int:
    s = s.strip().lower().replace("0x", "")
    s = s.lstrip("$")
    return int(s, 16)


def mirror_candidates(addr: int, use_mirror: bool) -> list[int]:
    if not use_mirror:
        return [addr]
    out = [addr]
    if addr + MIRROR <= 0xFFFF:
        out.append(addr + MIRROR)
    if addr - MIRROR >= 0x8000:
        out.append(addr - MIRROR)
    return out


def le_refs(prg: bytes, addr: int) -> list[int]:
    """数据区里存放该地址小端值的偏移。"""
    lo, hi = addr & 0xFF, (addr >> 8) & 0xFF
    return [i for i in range(len(prg) - 1) if prg[i] == lo and prg[i + 1] == hi]


def imm_pairs(prg: bytes, addr: int, window: int = 24) -> list[tuple[int, int]]:
    """`LDA #lo ... LDA #hi` 模式（高低字节顺序两种都试）。"""
    lo, hi = addr & 0xFF, (addr >> 8) & 0xFF
    out = []
    for i in range(len(prg) - 1):
        if prg[i] != 0xA9:
            continue
        cur = prg[i + 1]
        want = hi if cur == lo else (lo if cur == hi else None)
        if want is None:
            continue
        for j in range(i + 2, min(i + window, len(prg) - 1)):
            if prg[j] == 0xA9 and prg[j + 1] == want:
                out.append((i, j))
                break
    return out


def page_refs(disc: dis6502.Discover, page: int) -> list[tuple[int, str, str]]:
    """在已发现的指令里找指向某一页的引用。

    返回 [(指令地址, 类型, 反汇编文本)]。类型：
      abs  绝对寻址落在该页
      imm  立即数等于该页号（通常是在拼地址高位）
    """
    out = []
    for off in sorted(disc.ins):
        ins = disc.ins[off]
        if ins.mode in ("abs", "absx", "absy", "ind") and ins.operand is not None:
            if (ins.operand >> 8) == page:
                out.append((ins.addr, "abs", ins.text))
        elif ins.mode == "imm" and ins.operand == page:
            out.append((ins.addr, "imm", ins.text))
    return out


def ctx_of(disc: "dis6502.Discover", off: int) -> str:
    """判断该偏移属于指令还是数据。

    先看 off 是否指令起点；否则回看 1~2 字节，找「覆盖到 off 的指令」——
    指针通常正好是 `LDA $xxxx,Y` 的操作数字节，本身不是起点。
    这同时能抓假阳性：6502 的 `E6 A5` 既是 $A5E6 的小端字节，也是 `INC $A5`。
    """
    ins = disc.ins.get(off)
    if ins is not None:
        return f"指令 `{ins.text}`"
    for back in (1, 2):
        prev = disc.ins.get(off - back)
        if prev is not None and prev.off + prev.length > off:
            return f"指令 `{prev.text}` 的操作数"
    return "数据"


def describe(addr: int, prg: bytes, disc: "dis6502.Discover", use_mirror: bool) -> int:
    cands = mirror_candidates(addr, use_mirror)
    total = 0
    for a in cands:
        tag = "" if a == addr else "  [镜像]"
        for off in le_refs(prg, a)[:8]:
            total += 1
            print(f"      ptr  file 0x{off:04X}  总线 ${0x8000 + off:04X}"
                  f"  ← ${a:04X}{tag}   [{ctx_of(disc, off)}]")
        for lo_off, hi_off in imm_pairs(prg, a)[:8]:
            total += 1
            print(f"      imm  file 0x{lo_off:04X}/0x{hi_off:04X}"
                  f"  总线 ${0x8000 + lo_off:04X}/${0x8000 + hi_off:04X}"
                  f"  ← ${a:04X}{tag}   [{ctx_of(disc, lo_off)}]")
    return total


def parse_args(argv: list[str]) -> tuple[list[str], list[int], bool]:
    addrs, pages, mirror = [], [], True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-mirror":
            mirror = False
        elif a == "--page":
            i += 1
            if i < len(argv):
                pages += [parse_addr(p) for p in argv[i].split(",")]
        elif a.startswith("--"):
            pass
        else:
            addrs.append(a)
        i += 1
    return addrs, pages, mirror


def main() -> None:
    if not PRG_PATH.exists():
        raise SystemExit(f"找不到 {PRG_PATH}，先跑 pipeline.py")
    prg = PRG_PATH.read_bytes()

    addrs, pages, use_mirror = parse_args(sys.argv[1:])
    print(f"PRG: {len(prg)} 字节   镜像感知: {'开' if use_mirror else '关'}")

    vec = [0xC000, 0xC01A, 0xFFF9]
    disc = dis6502.find_code(prg, vec)
    print(f"递归下降：{len(disc.ins)} 条指令（用于给命中标注指令上下文）\n")

    if pages:
        for pg in pages:
            hits = page_refs(disc, pg)
            print(f"=== 页 ${pg:02X} 的代码引用：{len(hits)} 处 ===")
            for addr, kind, text in hits[:40]:
                print(f"  ${addr:04X}  [{kind}]  {text}")
            if len(hits) > 40:
                print(f"  … 另有 {len(hits) - 40} 处")
            print()
        return

    targets = ([(f"0x{parse_addr(a):04X}", parse_addr(a)) for a in addrs]
               if addrs else DEFAULT)

    found = 0
    for name, addr in targets:
        n = describe(addr, prg, disc, use_mirror)
        print(f"{name:<34} ${addr:04X}  {'有引用' if n else '**无引用**'}")
        if not n:
            print("      （直接指针、立即数拼地址、镜像地址均未命中 → 试 --page）")
        else:
            found += 1
        print()

    print(f"小计：{found}/{len(targets)} 个目标找到引用")
    print("注意：命中若标注为 `指令 INC $xx` 之类，通常是假阳性（字节对巧合）。")


if __name__ == "__main__":
    main()
