"""从二进制里提取可读字符串（ASCII 与 UTF-16LE 两种编码）。

用途
----
逆向 GUI 程序时，最省事的一步是**先看它的字符串**：菜单名、快捷键提示、
错误信息、命令行开关都会露出来。比翻文档快，而且不会记错。

用法
----
    python tools\\strscan.py <二进制路径> [关键词1,关键词2,...] [--min 6] [--utf16]

不给关键词则列出全部（仅前 N 条）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ascii_runs(data: bytes, min_len: int):
    cur = bytearray()
    start = 0
    for i, b in enumerate(data):
        if 0x20 <= b <= 0x7E:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= min_len:
                yield start, cur.decode("ascii")
            cur = bytearray()
    if len(cur) >= min_len:
        yield start, cur.decode("ascii")


def utf16_runs(data: bytes, min_len: int):
    """粗扫 UTF-16LE：ASCII 字符 + 0x00 交替。"""
    cur = bytearray()
    start = 0
    i = 0
    n = len(data)
    while i + 1 < n:
        lo, hi = data[i], data[i + 1]
        if hi == 0 and 0x20 <= lo <= 0x7E:
            if not cur:
                start = i
            cur.append(lo)
            i += 2
        else:
            if len(cur) >= min_len:
                yield start, cur.decode("ascii")
            cur = bytearray()
            i += 1
    if len(cur) >= min_len:
        yield start, cur.decode("ascii")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return
    path = Path(args[0])
    kws = [k.strip().lower() for k in args[1].split(",")] if len(args) > 1 else []

    min_len = 6
    if "--min" in sys.argv:
        min_len = int(sys.argv[sys.argv.index("--min") + 1])

    data = path.read_bytes()
    print(f"{path.name}  {len(data):,} 字节")

    # --switches 模式：在原始字节里直接匹配 --xxx，不要求整个字符串只有开关
    if "--switches" in sys.argv:
        pat = re.compile(rb"--[A-Za-z][A-Za-z0-9_-]{2,}")
        found = {}
        for m in pat.finditer(data):
            s = m.group().decode("ascii", "replace")
            found.setdefault(s.lower(), (s, m.start()))
        print(f"命令行开关候选 {len(found)} 个：")
        for key in sorted(found):
            s, off = found[key]
            print(f"  0x{off:08X}  {s}")
        return

    seen = set()
    hits = []
    gens = [("ascii", ascii_runs(data, min_len))]
    if "--utf16" in sys.argv or not kws:
        gens.append(("utf16", utf16_runs(data, min_len)))

    for enc, gen in gens:
        for off, s in gen:
            key = (s.lower(), enc)
            if key in seen:
                continue
            seen.add(key)
            low = s.lower()
            if kws and not any(k in low for k in kws):
                continue
            hits.append((off, enc, s))

    hits.sort()
    print(f"命中 {len(hits):,} 条")
    for off, enc, s in hits[:300]:
        print(f"  0x{off:08X} [{enc}] {s}")
    if len(hits) > 300:
        print(f"  … 另有 {len(hits) - 300} 条")


if __name__ == "__main__":
    main()
