# 踩坑清单（按代价排序）

每一条都是真实踩过的。**先读这一份，能省掉几小时。**

---

## A · 方法论坑（代价最高，直接导致结论错误）

### A1. 单次"没反应"不足以否定假设 ★最贵

**症状**：把玩家传送到某个可疑格子上，什么都没发生 → 判定"这个格子没用"。

**真实原因**：触发条件不止一个。那次的完整条件是：
格子值 == 8 **且** `$9C == 0` **且** `$29 == 8` **且** `$2B == 8`（对齐），
我漏了 `$9C` 和对齐要求。

**代价**：三轮结论反复（对 → 否 → 又否 → 最终确认）。

**规则**：
- 注入实验失败时，正确结论是**"前置条件集合不完整"**，不是"这个功能没用"。
- 先去把分支条件**全部读出来**，再设计实验。
- 每加一个条件就重测一次，别一次改多处。

### A2. 不要从一个来源类别推广

**症状**：炸毁 7 块普通软砖，没有道具掉落 → 判定"本作没有道具系统"。

**真实原因**：道具只存在于每关 2 个**伪装成砖的特殊格**里，普通砖本来就不产道具。

**规则**：写下"X 不存在"之前，先问**"我检查的是全部来源吗？"**
把结论降级为"普通砖不产道具"，而不是"没有道具系统"。

### A3. 注入会绕过前置条件

`emu.write` 直接把玩家坐标写进格子，绕过了碰撞/输入路径，
所以"走过去能触发"的效果可能完全测不出来。

**规则**：注入用于**测量后果**（时序、数值），不用于**证明可触发性**。
可触发性必须用真实输入验证。

### A4. 输出被截断会骗人

**症状**：看到"14 处 `LDA $12`"的输出被截断，就断言"全部都在菜单里"。

**规则**：任何"全部/只有/都"的结论，先确认输出没被 `Select-Object -First N` 截断。

### A5. 初始化代码不代表"死字段"

**症状**：HUD 分数位被 `$9DF6` 写成 `"00"`，几轮跑测都是 `00` → 判定"这是硬编码死位"。

**真实原因**：那只是**初始绘制**，运行时另有路径改写它（击杀后变 `100`）。

**规则**：看到某字段一直不变，先找**全部写点**（`ramxref`），别只看初始化那一处。

---

## B · 寻址与反汇编坑

### B1. NROM-128 镜像 ★高频

16 KB PRG 被镜像到 `$C000-$FFFF`。同一个数据在 `$9C53` 和 `$DC53` 都能取到。

```
文件偏移 = (总线地址 - 0x8000) % 0x4000
清单地址 $8xxx  ↔  运行时 $Cxxx     → 相互换算 ±0x4000
```

**症状**：搜引用搜不到（代码用的是镜像地址）。
**对策**：`findrefs.py` 必须同时搜索两种写法。

### B2. 数据被当指令解 → 假命中

扫 `$9F` 的访问时出现 `ORA $9F`，看起来像位运算；
实际那一段是 16 位数值表（`05A0 A006 07F0…`），反汇编器把数据解成了指令。

**规则**：歧义命中的判据是**"这个 PC 被执行过吗"**（CDL 或 exec 回调），
不是"这行代码看起来合理吗"。

### B3. 两字节指针扫描假阳性

找 `JSR $9E20` 时扫字节 `20 9E`，命中 `20 DE` 这类
`JSR $D4DE` 的字节片段。

**规则**：找调用者要**严格解码 `JSR`/`JMP` 并检查操作数**，别用裸字节模式匹配。

### B4. 回调报的 PC 是"下一条指令"

```
STA $B5 是 2 字节 → 真实写入点 = 回调 PC - 2
```
不换算会一直差 2 字节，看反汇编时对不上。

### B5. 认错变量

把 `$29` 当成玩家列，实际代码用 `$28`。

**规则**：变量语义必须由**代码里的使用方式**确认，不能靠"数值范围看起来像"。

---

## C · Mesen2 Lua 坑

### C1. `emu.setInput({}, 0)` 不会松开按键 ★高频

文档明确：**未指定的按键保持原状态**。
必须显式给每个键：

```lua
local KEYS = {"up","down","left","right","a","b","start","select"}
local function setAll(t)
  local o = {}
  for _,k in ipairs(KEYS) do o[k] = t[k] or false end
  emu.setInput(o, 0)
end
```

症状：角色只走一格就停住；`$12` 卡在 `0x04` 不归零。

### C2. 二进制文件必须用 `"wb"`

`io.open(name, "w")` 会把 `0x0A` 改写成 `0x0D 0x0A`，PNG 直接损坏
（症状：截图只有 2–3 KB，打不开）。

### C3. `--testRunner` 下只有相对路径能写文件

配了 `AllowIoOsAccess` 也一样：`io.open` **绝对路径必定失败**。
文件落在 **Mesen.exe 所在目录**。脚本里用
`io.open("out.csv","w")`，然后去 `tools\Mesen2\` 里取。

### C4. nametable RAM 是 0 基址

```lua
-- 错：读 0x2040 全是 0
-- 对：HUD 第 2 行第 c 列
emu.read(0x0040 + c, emu.memType.nesNametableRam)
```

### C5. 精灵闪烁会让计数失效

为规避单扫描线 8 精灵限制，敌方精灵常在 16/8 之间逐帧交替。
**不要数精灵个数**，要按 2×2 坐标块统计，且取块内**非隐藏精灵的最小 Y/X**
（隐藏精灵的 Y 常是 248）。

### C6. `table.unpack` 可能不存在

不同 Lua 版本不一致，自己写循环取最小值。

---

## D · Windows / PowerShell 坑（仅 Windows；Linux 见 F 节）

### D1. `.ps1` 必须带 UTF-8 BOM

PowerShell 5.1 会把**无 BOM 的 UTF-8** 当 ANSI 读，中文全乱、语法报错。

### D2. Mesen.exe 双击无反应 → 预建目录

**症状**：双击无窗口，事件日志 `0xC0000409`。
**原因**：启动时写 `%USERPROFILE%\Documents\Mesen2` 抛
`System.UnauthorizedAccessException`。
**修复**：**先 `New-Item -ItemType Directory` 建好这个目录**。
（曾误判为"中文路径问题"，那条是错的。）

### D3. 别把输出重定向进 `tools\Mesen2\`

Mesen 会因此直接退出。用 `$env:TEMP`。

### D4. `Start-Process -PassThru` 的 `HasExited` 在 PS 5.1 不可靠

改用 `Get-Process -Id $p.Id -ErrorAction SilentlyContinue` 判断。

### D5. `Write-Host "..." -f $x -ForegroundColor Red` 报错

`-f` 与 `-replace`/格式化冲突。把 `-f` 表达式用括号包起来。

### D6. 双引号里 `$12` 会被 PowerShell 当变量吃掉

传内存地址用**单引号**：`'$12'`。

### D7. `-replace` 参数超过两个元素 → ParserError

```powershell
# 报错：-replace 运算符后只能跟两个元素
$c -replace 'a','b','c','d'
```
改用 Python 脚本或 `[regex]::Replace`。

### D8. PowerShell 没有 heredoc

`cat > f <<'EOF'` 不成立。写文件用 write 工具，或用 `Set-Content`。

### D9. Python f-string 里不能出现反斜杠

```python
# SyntaxError
f"{re.findall(r'^  \w+: ', t)}"
```
先算到变量再拼。

---

## E · 下载 / TLS 坑（E1/E2 是 Windows Schannel 特有；Linux 一般不会遇到）

### E1. 沙箱下 Schannel 取不到凭证

`SEC_E_NO_CREDENTIALS`、`schannel AcquireCredentialsHandle failed`
→ 提权到可联网的沙箱模式再下载。

### E2. GitHub 证书链不完整

只下发叶子证书。Windows Schannel / `curl.exe` 会自动去系统 store 补中间证书
→ 成功；**Python 3.11 自带 OpenSSL 不做 AIA 抓取** → `unable to get issuer certificate`。

**规则**：下载优先 `curl.exe`（走 Schannel），别用 `urllib`。

### E3. 大文件 curl 中断

32 MB 的 Mesen 包偶发 `curl: (56)`。
加 `--retry 3 --retry-all-errors`。

---

## F · Linux / macOS 专属坑

### F1. Mesen2 的 Linux 包只有一个二进制

`Mesen_*_Linux_x64.zip` 解压后**只有一个 83 MB 的自包含二进制 `Mesen`**
（NativeAOT，无安装脚本、无目录结构）。

```bash
unzip Mesen_2.1.1_Linux_x64.zip -d tools/Mesen2
chmod +x tools/Mesen2/Mesen        # ← 别忘了
```

macOS 同理，但首次运行可能被 Gatekeeper 拦：
`xattr -d com.apple.quarantine tools/Mesen2/Mesen`

### F2. 无显示环境需要虚拟屏

Mesen2 即使 `--testRunner` 也是 GUI 程序。服务器 / CI / Docker 里要：

```bash
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
./Mesen --testRunner <rom> <script.lua>
```

不装 `xvfb` 的典型症状：进程立即退出、无任何输出。

### F3. Lua `io.open` 在容器 / WSL 里可能写不了

本 skill 的 4 个模板都设计成**双通道**：既写文件，也在结束时
`print()` 整份日志到 stdout。所以最坏情况可以退化为：

```bash
./Mesen --testRunner <rom> tools/lua/30_sample_vars.lua 2>&1 | tee out.txt
```

结果不丢，**只有截图会丢**。若必须截图，确认
`settings.json` 里 `TestRunner.AllowIoOsAccess = true`，且文件在
`~/.config/Mesen2/settings.json` 或二进制旁边（两处都写最保险）。

### F4. 路径与大小写

Linux 文件系统区分大小写。脚本里出现的 `tools/lua/...`、`L3-data/` 必须
与实际目录大小写一致——Windows 上无感，Linux 上直接找不到文件。

### F5. `python` vs `python3`

Ubuntu 默认只有 `python3`。所有命令用 `python3 tools/xxx.py`。
（本文档其它地方写 `python` 是 Windows 习惯，Linux 请自行替换。）

### F6. 换行符

若 skill 目录在 Windows 上编辑过再拷到 Linux，`.sh` 会带 CRLF，
执行时报 `bad interpreter: /usr/bin/env bash^M`。

```bash
sed -i 's/\r$//' bootstrap.sh      # 或 dos2unix bootstrap.sh
```

`.lua` / `.py` 带 CRLF 通常无害，但 `.sh` 必坏。

---

## G · 自检清单（每次交付前跑一遍）

```powershell
# 1) 文档里有没有"未分级"的数字
Select-String -Path DESIGN.md -Pattern '^\|[^|]*\d' | Where-Object { $_.Line -notmatch '\[已验证\]|\[实测\]|\[推断|\[待测\]' }

# 2) 未测定项是否还在
Select-String -Path DESIGN.md -Pattern '未测定|待测'

# 3) meta.yml 是否留了编造值（未测应为 null）
Select-String -Path meta.yml -Pattern 'metrics' -Context 0,15
```

三问：
1. 每条结论都有证据等级吗？
2. 有没有把"我推断"写成"事实"？
3. 单次实验的否定结论，是否已用补充实验确认？
