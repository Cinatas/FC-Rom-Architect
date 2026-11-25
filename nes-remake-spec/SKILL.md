---
name: nes-remake-spec
description: >
  Reverse-engineer a NES/Famicom ROM into an evidence-graded game design
  document that is detailed enough to rebuild the game in a modern engine
  (Godot / Unity / any). Use whenever the user supplies a .nes ROM and wants
  to understand it, document it, extract its rules, or remake it. Also use
  for retro game design research: extracting mechanics, level generation,
  scoring, timers and difficulty curves from a cartridge. Covers toolchain
  bootstrap (auto-downloads Mesen2), static disassembly, execution-coverage
  measurement, runtime frame-level measurement via Lua, targeted experiments
  by memory injection, unresolved-caller recovery, and the final spec audit.
  适用于：拿到一个 NES ROM，要产出足以支持 Godot/Unity 重制的设计文档；
  或做复古游戏设计研究（机制、关卡生成、计分、难度曲线）。
whenToUse: >
  Load as soon as a .nes / .fds / .unf ROM is in play and the goal is
  understanding or remaking it — not emulation development, not ROM hacking
  for distribution, not cheat codes.
version: 1.0.0
---

# NES ROM → remake-ready design document

Turn one cartridge into a spec a modern-engine developer can build from.

**The deliverable is not a disassembly.** It is a design document whose every
claim carries an evidence grade, plus a parameter table that can be copied
straight into engine code. A disassembly listing without measured behaviour is
not acceptable output.

---

## 0 · Non-negotiables

1. **Grade every claim.** Use the four levels in `references/evidence-and-doc.md`.
   An ungraded number is a lie with extra steps.
2. **Never invent a gameplay number.** If you did not measure it or read it out
   of the code, it is `[待测]` or `null`, never a plausible guess.
3. **One negative experiment does not refute a hypothesis.** You almost certainly
   have not found all the preconditions. See the worst trap in
   `references/pitfalls.md` ("single negative result").
4. **Runtime beats static.** A function you have only read is a hypothesis; a
   function with an execution count is a fact.
5. **Automate before you hand-wave.** Absolutely everything except "what is this
   game about" can be measured by a script that runs unattended.

---

## 1 · Bootstrap (one command, then never again)

**Windows**

```powershell
pwsh -File <skill>\bootstrap.ps1 -Workspace <repo-root>
```

**Linux / macOS**

```bash
./<skill>/bootstrap.sh -w <repo-root>
```

Both create the workspace layout, download Mesen2 from GitHub releases,
verify and extract it, write a `settings.json` with `AllowIoOsAccess` **and**
copy this skill's `tools/` into the workspace.

| Platform | Mesen2 asset | Binary | Extra deps |
|---|---|---|---|
| Windows | `Mesen_*_Windows.zip` | `Mesen2/Mesen.exe` | `curl.exe` (built in) |
| Linux x64 | `Mesen_*_Linux_x64.zip` | `Mesen2/Mesen` | `curl`, `unzip` |
| Linux ARM64 | `Mesen_*_Linux_ARM64.zip` | `Mesen2/Mesen` | `curl`, `unzip` |
| macOS Intel | `Mesen_*_macOS_x64_Intel.zip` | `Mesen2/Mesen` | `curl`, `unzip` |
| macOS Apple Silicon | `Mesen_*_macOS_ARM64_AppleSilicon.zip` | `Mesen2/Mesen` | `curl`, `unzip` |

**Windows-only step that users trip over:** Mesen2 writes
`%USERPROFILE%\Documents\Mesen2` on startup and **crashes with `0xC0000409`
if that directory cannot be created**. `bootstrap.ps1` pre-creates it.
On Linux/macOS the equivalent location is created on demand and needs no fix.

**Headless hosts (servers, CI, Docker)** need a virtual display — Mesen2 is a
GUI application even under `--testRunner`:

```bash
Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./Mesen --testRunner <rom> <script.lua>
```

If Lua `io.open` still fails (sandboxed/WSL/container), **the templates degrade
gracefully**: every one of them also prints the full log to stdout at the end,
so `./Mesen --testRunner ... 2>&1 | tee out.txt` still captures the results.
Only screenshots are lost.

Then lay out the game folder:

```
<repo-root>/
├── <game>.nes
├── tools/                  # Mesen2 etc. (shared)
└── <game>/
    ├── DESIGN.md           # ← the deliverable
    ├── L1-design.md        # design-layer notes (loop, comparisons)
    ├── L2-spec/*.md        # one file per subsystem, the evidence base
    ├── L3-data/            # prg.bin chr.bin, csv dumps, screenshots
    └── meta.yml            # cross-game comparable metrics
```

---

## 2 · Phase 1 — static survey (no emulator)

```powershell
python tools/pipeline.py            # header, code discovery, disassembly
python tools/disasm_verified.py     # recursive descent + data partitioning
python tools/strscan.py             # every readable ASCII string in ROM
```

What to extract and why:

| Artifact | Tool | Why it matters later |
|---|---|---|
| mapper, PRG/CHR size, mirroring | `nesrom.py` | determines address aliasing |
| code/data partition, coverage % | `pipeline.py` | tells you how much is left |
| recursive-descent disassembly | `disasm_verified.py` | seed for xrefs |
| **every ASCII string** | `strscan.py` | **cheapest way to discover features** |

**Run `strscan.py` first, before anything clever.** Unused or hidden features
announce themselves as text: level-clear banners, bonus round titles, ending
credits, leftover debug menus. In one real case a `BONUS:STAGE` string found
this way was the only clue that a whole bonus-round mode existed, after days of
code tracing had failed to find it.

Also scan for **reverse-stored** strings — many NES text engines write
right-to-left, so `EGATS` is `STAGE`.

---

## 3 · Phase 2 — execution coverage (the honesty check)

Static analysis alone is unreliable on hand-written assembly: it misses jump
tables, overlays, and code reached only through indirect dispatch.

1. Enable Mesen2's **CDL** logger (Debug → Code/Data Logger), let the game run
   through menus and gameplay, save the `.cdl`.
2. `python tools/cdl.py` merges CDL with static discovery and reports real
   coverage. `tools/selftest_cdl.py` validates the parser against a known file.
3. `python tools/analyze_autorun.py` compares **manual play vs unattended
   auto-play** coverage.

Measured on the reference title: static 40.4 %, auto-play CDL 35.7 %, manual
play only 30.5 %. **Unattended auto-play beats a human.** Let a script mash
START and random directions for a few thousand frames; it is free coverage.

Anything still unexecuted after both is either data or genuinely dead.

---

## 4 · Phase 3 — find the variables (write callbacks)

You cannot measure what you cannot name. Register a RAM **write** callback over
the suspected page and let the machine tell you the writer's PC.

```lua
emu.addMemoryCallback(cb, emu.callbackType.write, 0x0300, 0x03FF, emu.memType.nesMemory)
```

Two lessons that save hours:

- The callback's reported PC is the **next** instruction. For a 2-byte
  `STA zp`, the real write site is `pc - 2`.
- **A dozen PCs each firing the same count is a struct writeback**, not twelve
  independent variables. That shape revealed a 12-field × 10-slot object array
  with stride 10 in one call.

Template: `tools/lua/10_write_watch.lua`.

Cross-check statically with `ramxref.py` (list every read/write of `$xx`) and
`scan_instr.py` (linear scan of *all* ROM offsets — bypasses the partition so
you can still find an instruction that static descent missed).

---

## 5 · Phase 4 — frame-level measurement (Lua)

This is where the numbers come from. Read the API cheat sheet in
`references/mesen-lua-api.md`, then work through the templates:

| Template | Use |
|---|---|
| `tools/lua/00_probe_api.lua` | confirm what this Mesen build exposes |
| `tools/lua/30_sample_vars.lua` | periodic variable sampling + screenshots + HUD text |
| `tools/lua/40_poke_test.lua` | **targeted experiment by memory injection** |

### Read the HUD out of the nametable, not out of RAM

HUDs usually store **ASCII** in nametable RAM. Reading it gives you live
score/time/lives without knowing a single variable name:

```lua
emu.read(0x0040 + col, emu.memType.nesNametableRam)  -- row 2, 0-based addresses!
```

Note the base: nametable RAM is addressed **0-based** (`0x0000`–`0x07FF`).
Passing `0x2000` silently returns zeros — a trap that cost a full round.

### Prefer targeted injection over writing a game-playing AI

Writing a bot that survives is expensive. `emu.write` lets you construct the
exact situation you need:

- put a bomb under an enemy → measure kill, score, death animation
- put a bomb under the player → measure the death path and when lives decrement
- put a bomb beside a brick → measure destruction timing
- write a coordinate → test a pickup / an exit

`tools/lua/40_poke_test.lua` is the harness: it pokes a cell, samples the
observable consequence, takes screenshots, and logs deltas only.

**But** injection bypasses collision and input paths. When a poke produces no
effect, the correct conclusion is *"the precondition set is incomplete"*, not
*"the feature does nothing"*. Re-test with real input before concluding.

---

## 6 · Phase 5 — recover what static analysis cannot find

When a routine demonstrably executes but no `JSR` or pointer table points at it,
the caller is computed. Ask the CPU:

```lua
emu.addMemoryCallback(onExec, emu.callbackType.exec, LO, HI, emu.memType.nesMemory)
-- inside: read the stack — JSR pushed return-1
```

Template: `tools/lua/20_exec_callback.lua`. This single technique unlocked an
entire gameplay layer whose call mechanism was invisible to xref tools.

Corollary: when you cannot find *who calls* something, stop searching the code
and start watching the execution.

---

## 7 · Phase 6 — the spec

Work through `references/evidence-and-doc.md`. In short:

1. Fill `L2-spec/<subsystem>.md` per subsystem, each claim graded.
2. Write `DESIGN.md` from those, with a **copy-paste parameter table**.
3. Fill `meta.yml` with the cross-game metric keys. Leave unmeasured keys
   `null` — never invent.
4. Keep an explicit **未测定项 (open items)** chapter. It is the most-read
   chapter by whoever continues the work.

**Write the parameter table in seconds and in frames**, because that is what
transfers to a modern engine:

```
bomb fuse      160 frames  → 2.67 s
flame lifetime  32 frames  → 0.53 s
input sample    4 frames   → 15 Hz
```

Call out which NES-specific content should be **discarded** on remake
(pattern tables, OAM, nametables, VRAM queues, sprite flicker workarounds), and
which feel-carrying quirks should be **deliberately preserved** (e.g. a coarse
15 Hz input sample reads as "heavy" and removing it changes the game).

---

## 8 · Automation boundary

| Automatable (do it in a script) | Requires judgement |
|---|---|
| download / verify toolchain | deciding which subsystem matters |
| header, partition, coverage | judging whether an observation is an artifact |
| variable discovery, xrefs | deciding what a variable *means* |
| sampling, screenshots, HUD text | interpreting a 2-frame timing anomaly |
| poke experiments | concluding a mechanic is absent |
| string scan, stage enumeration | writing the design analysis |

If you catch yourself in a trial-and-error loop of pokes, you have missed a
precondition. Go read the branch conditions instead.

---

## 9 · Definition of done

- [ ] Coverage measured, % reported, unexplained gaps named
- [ ] Every core mechanic has a measured spec: input, movement, primary action,
      its timing, its hit resolution, enemies, scoring, death/lose, win
- [ ] Level/data generation decoded *or* proven procedural, with the generator reimplemented
- [ ] `DESIGN.md` parameter table complete enough to implement without re-reading code
- [ ] `meta.yml` filled, unmeasured keys `null`
- [ ] Open-items chapter honest and specific
- [ ] Every claim graded; grep the doc for ungraded numerals before delivering

Then report **what is solid, what is inferred, and what is still open** — in
that order, and never blur the three.
