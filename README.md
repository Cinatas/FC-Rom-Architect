# FC-Rom-Architect

把一块 **FC / NES 卡带**逆向成一份**证据分级、可直接照抄参数去重制**的设计文档。

仓库里装的是一个 DSH / agent **skill**：`nes-remake-spec`。它不是反汇编器 GUI，也不是模拟器；
它是一套可复现的工作流 + 16 个 Python 脚本 + 5 个 Mesen2 Lua 模板 + 模板文档规范，
把「读代码猜机制」换成「跑起来测出数值」。

> 交付物不是反汇编清单，而是一份每个结论都带证据等级的设计文档，
> 以及一张能直接贴进引擎代码的参数表（秒与帧双写）。

---

## ⚠️ 本仓库**不包含**什么

这是发布边界，也是设计原则：

| 不发布 | 原因 |
|---|---|
| **任何 ROM**（`.nes` / `.fds` / `.unf` / `.nsf`） | 版权物；请自备合法副本 |
| **任何逆向产物**（`L2-spec/`、`L3-data/`、`evidence/`、`.cdl`、`.mss`、反汇编导出、截图、`DESIGN.md`） | 针对具体某款游戏的测量结果，同样受版权与作品权利约束 |
| **工具链二进制**（Mesen2、6502bench / SourceGen、`_downloads/*.zip`） | 由 `bootstrap` 脚本按需下载，不进版本库 |

`.gitignore` 已按上述边界写死；提交前请跑 `git status` 复核。

---

## 仓库结构

```
FC-Rom-Architect/
├── README.md
├── LICENSE                        MIT（只覆盖本仓库的代码与文档，不涉及 ROM）
└── nes-remake-spec/               ← 装到 ~/.dsh/skills/ 或 <repo>/.dsh/skills/ 的那一层
    ├── SKILL.md                   主流程：6 个阶段 + 交付定义
    ├── bootstrap.ps1              Windows：一条命令装好工具链（自动下载 Mesen2）
    ├── bootstrap.sh               Linux / macOS：同上
    ├── references/
    │   ├── evidence-and-doc.md    四级证据分级 + 文档规范
    │   ├── mesen-lua-api.md       Mesen2 Lua API 速查（含实测陷阱）
    │   └── pitfalls.md            踩坑清单（含 6 条 Linux 专属坑）
    ├── templates/
    │   ├── DESIGN.md              DESIGN.md 骨架
    │   └── meta.yml               跨游戏可比指标骨架（未测项留 null）
    └── tools/
        ├── nesrom.py · pipeline.py · disasm_verified.py · dis6502.py
        ├── cdl.py · selftest_cdl.py · scan_instr.py · ramxref.py · findrefs.py
        ├── strscan.py · font.py · tiles.py · render.py · nes6502.py · nesrun.py
        ├── fetch_tools.py         仅 Windows 可用；Linux/macOS 用 bootstrap.sh
        └── lua/                   5 个 Mesen2 运行时模板
            ├── 00_probe_api.lua     确认当前 Mesen 构建暴露了哪些 API
            ├── 10_write_watch.lua   写回调 → 找出谁在写这个变量
            ├── 20_exec_callback.lua 执行回调 → 恢复「找不到调用者」的例程
            ├── 30_sample_vars.lua   周期采样 + 截图 + nametable 读 HUD
            └── 40_poke_test.lua     内存注入做定向实验
```

---

## 快速开始

### 1. 装 skill

| 范围 | 路径 | 适用 |
|---|---|---|
| **用户级（推荐）** | `~/.dsh/skills/` | 所有项目可用 |
| 用户级（通用 agent） | `~/.agents/skills/` | 同上，给其它 agent 用 |
| 项目级 | `<git根>/.dsh/skills/` | 只在某个项目里可用 |

```powershell
# Windows
git clone https://github.com/Cinatas/FC-Rom-Architect.git
Copy-Item -Recurse .\FC-Rom-Architect\nes-remake-spec "$env:USERPROFILE\.dsh\skills\"
```

```bash
# macOS / Linux
git clone https://github.com/Cinatas/FC-Rom-Architect.git
mkdir -p ~/.dsh/skills && cp -r FC-Rom-Architect/nes-remake-spec ~/.dsh/skills/
```

自检：`~/.dsh/skills/nes-remake-spec/SKILL.md` 存在，且文件以 `---` 开头、frontmatter 含 `name` 与 `description`
（缺任一项该 skill 会被静默忽略）。

只想读流程不要工具链？把 `SKILL.md` 单独放成 `~/.dsh/skills/nes-remake-spec.md` 即可。

### 2. 装工具链

```powershell
pwsh -File ~/.dsh/skills/nes-remake-spec/bootstrap.ps1 -Workspace <你的工作区>
```

```bash
~/.dsh/skills/nes-remake-spec/bootstrap.sh -w <你的工作区> [-g 游戏名]
```

脚本会：建工作区目录 → 从 GitHub Releases 取最新 Mesen2 → 校验并解压 → 写 `settings.json`（含 `AllowIoOsAccess`）
→ 把 skill 的 `tools/` 复制进工作区。

**Windows 上最容易卡住的一步**：Mesen2 启动时要写 `%USERPROFILE%\Documents\Mesen2`，
建不出来就直接 `0xC0000409` 崩溃。`bootstrap.ps1` 已预建该目录。

工作区布局：

```
<workspace>/
├── <game>.nes
├── tools/Mesen2/            # 模拟器（共享）
└── <game>/
    ├── DESIGN.md            # ← 交付物
    ├── L1-design.md
    ├── L2-spec/*.md         # 子系统规格 = 证据底座
    ├── L3-data/             # 分段 / csv / 截图
    └── meta.yml
```

---

## 平台支持

| 平台 | 状态 | 引导脚本 | Mesen2 资产 |
|---|---|---|---|
| Windows x64 | ✅ 已验证 | `bootstrap.ps1` | `Mesen_*_Windows.zip` |
| Ubuntu / Linux x64 | ✅ 支持 | `bootstrap.sh` | `Mesen_*_Linux_x64.zip` |
| Linux ARM64 | ✅ 支持 | `bootstrap.sh` | `Mesen_*_Linux_ARM64.zip` |
| macOS Intel / Apple Silicon | ✅ 支持 | `bootstrap.sh` | `Mesen_*_macOS_*.zip` |

纯文本部分（SKILL.md / references / templates / tools）跨平台无差别。
Linux/macOS 请用 `bootstrap.sh`，**不要**用 `fetch_tools.py`（它是 Windows 优先的：优先 `curl.exe`、按 `*.exe` 找二进制）。

无显示环境（服务器 / CI / Docker）需要虚拟屏：

```bash
Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./Mesen --testRunner <rom> <script.lua>
```

即便 Lua `io.open` 被沙箱拦掉，模板也会在结尾把完整日志打到 stdout，
`./Mesen --testRunner ... 2>&1 | tee out.txt` 仍能拿到结果——只是丢掉截图。

### 依赖

| 依赖 | 自动获取 | 说明 |
|---|---|---|
| Mesen2 | ✅ `bootstrap` 下载 | 运行时测量 + CDL 覆盖率 |
| curl | 需系统已有 | Windows 10+ 自带 `curl.exe` |
| unzip | 需系统已有 | 仅 Linux/macOS（release 是 zip） |
| Python 3.9+ | 需系统已有 | 静态分析脚本 |
| Lua 5.1+ | 可选 | 只用于 `luac -p` 语法自检；Mesen2 自带运行时 |

---

## 工作流一览

| 阶段 | 做什么 | 关键点 |
|---|---|---|
| 0 | bootstrap | 一条命令，之后不再碰 |
| 1 | 静态勘测 | **先跑 `strscan.py`**：没被用到的功能常常自己以字符串暴露（`BONUS:STAGE`）；注意反向存储字符串（`EGATS` = `STAGE`） |
| 2 | 执行覆盖率 | CDL + 无人值守自动玩。实测：静态 40.4% / 自动玩 35.7% / 手动玩 30.5% —— **脚本乱按比人打得全** |
| 3 | 找变量 | RAM 写回调拿写入方 PC（报告的是**下一条**指令，`STA zp` 要 `pc-2`）；十几个 PC 同频触发通常是**结构体回写** |
| 4 | 帧级测量 | Lua 采样；HUD 从 nametable 读（**0 基址**，传 `0x2000` 只会拿到 0）；优先用内存注入做定向实验，而不是写会打的 AI |
| 5 | 恢复调用者 | 例程确实在跑但没有 `JSR`/指针表指向它 → 执行回调读栈（`JSR` 压入 return-1） |
| 6 | 出规格 | 每个子系统一份 `L2-spec`，每条结论分级；`DESIGN.md` 给可复制的参数表；未测项单列一章 |

四条铁律（详见 `SKILL.md` §0）：

1. **每个结论都要分级**，未分级的数字等于加了步骤的谎话。
2. **绝不编造玩法数值**——没测到就是 `[待测]` / `null`。
3. **一个负实验结果不能证伪假设**，你几乎肯定还没找全前置条件。
4. **运行时高于静态**：只读过的函数是假设，带执行计数的函数才是事实。

---

## 维护

- 本仓为分发源；改动后同步到已安装位置（Windows）：

  ```powershell
  robocopy .\nes-remake-spec "$env:USERPROFILE\.dsh\skills\nes-remake-spec" /MIR
  ```

- `bootstrap.ps1` 必须保持 **UTF-8 带 BOM**（`.gitattributes` 已锁定 `*.ps1 -text`，禁止 git 做换行/编码转换）；
  `.sh` 一律 LF（转成 CRLF 在 Linux 上会 `bad interpreter`）。
- 提交前自查：`git status` 里不应出现任何 ROM 或 `L2-spec/ L3-data/ evidence/ *.cdl`。

**License**：[MIT](LICENSE)。可自由使用、修改、再分发（含商用），保留版权声明即可。
该许可只覆盖**本仓库的代码与文档**；ROM 的版权另属其权利人，不在本许可范围内。

---

## English

**FC-Rom-Architect** ships one agent skill, `nes-remake-spec`: reverse-engineer a NES/Famicom ROM into an
evidence-graded design document detailed enough to rebuild the game in a modern engine (Godot, Unity, anything).

The deliverable is *not* a disassembly. Every claim carries one of four evidence grades, and every gameplay
number is either measured at frame level or read out of the code — never guessed.

The workflow: bootstrap a toolchain (auto-downloads Mesen2) → static survey (string scan first) → CDL execution
coverage → RAM write callbacks to name variables → Lua frame-level measurement and memory-injection experiments →
exec-callback stack reads to recover computed callers → write the graded spec.

**Not in this repository:** no ROMs, no per-game reverse-engineering output (`L2-spec/`, `L3-data/`,
`evidence/`, `.cdl`, disassembly exports), no bundled toolchain binaries. Bring your own legally obtained cartridge.

License: [MIT](LICENSE), covering this repository's code and documentation only —
not any ROM's contents.
