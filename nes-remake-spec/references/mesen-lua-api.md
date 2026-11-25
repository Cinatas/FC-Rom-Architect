# Mesen2 Lua API — 已核实的事实

只记录**在本项目里实测通过**的调用。Mesen2 版本不同会有差异，
先跑 `tools/lua/00_probe_api.lua` 确认。

---

## 1. 运行方式

```powershell
Mesen.exe --testRunner <rom.nes> <script.lua>
```

- **绝对路径的 `io.open` 一律失败**，只能用相对路径；产物落在 **Mesen.exe 目录**。
- `--testRunner` 必须同时给 rom 和脚本。
- 单独的 `--a` 会以 `-1` 退出；未知参数（如 `--foo`）被忽略。
- 脚本最后要 `emu.stop(0)`，否则不会退出。
- `print()` 输出到 stdout，可被重定向捕获。

```powershell
$p = Start-Process -FilePath $exe `
  -ArgumentList @('--testRunner', "`"$rom`"", "`"$lua`"") `
  -PassThru -RedirectStandardOutput "$env:TEMP\out.txt"
$p | Wait-Process -Timeout 300
```

---

## 2. 回调

```lua
-- 内存回调：读 / 写 / 执行
emu.addMemoryCallback(cb, emu.callbackType.write, 0x0300, 0x03FF, emu.memType.nesMemory)
emu.addMemoryCallback(cb, emu.callbackType.exec,  0x8FA4, 0x8FA4, emu.memType.nesMemory)
-- cb 签名: cb(address, value)   对 exec 是 cb(address)

-- 事件回调
emu.addEventCallback(cb, emu.eventType.inputPolled)  -- 输入轮询时（改输入必须在这里）
emu.addEventCallback(cb, emu.eventType.endFrame)     -- 每帧结束（采样、截图）
```

**`exec` 回调是定位"找不到调用者"的函数的唯一可靠手段。**

---

## 3. 状态与内存

```lua
local s = emu.getState()
s["frameCount"]  s["cpu.pc"]  s["cpu.sp"]  s["cpu.a"]  s["cpu.x"]  s["cpu.y"]

emu.read(addr, memType)
emu.write(addr, value, memType)      -- 实测可用（注入实验的基础）
```

### memType

| 常量 | 用途 | 注意 |
|---|---|---|
| `emu.memType.nesMemory` | CPU 总线（含 ROM 映射） | 回调最常用 |
| `emu.memType.nesInternalRam` | `$0000-$07FF` | 采样零页/变量用 |
| `emu.memType.nesPrgRom` | PRG ROM | 读静态数据 |
| `emu.memType.nesNametableRam` | 名称表 | **0 基址**，`0x0000-0x07FF` |

**`nesNametableRam` 用 `0x2000` 读会静默返回全 0。** HUD 文本在第 2 行：
```lua
emu.read(0x0040 + col, emu.memType.nesNametableRam)
```

---

## 4. 输入

```lua
-- 必须显式给全部 8 个键，否则未指定的键保持原状态
local KEYS = {"up","down","left","right","a","b","start","select"}
local function setAll(t)
  local o = {}
  for _,k in ipairs(KEYS) do o[k] = t[k] or false end
  emu.setInput(o, 0)      -- 第 2 参数 = 端口（0=P1, 1=P2）
end
```

**历史教训**：`emu.setInput({}, 0)` 不会松开按键，角色会一直走。

---

## 5. 截图

```lua
local png = emu.takeScreenshot()
local f = io.open("shot.png", "wb")   -- ★ 必须 "wb"，否则 PNG 被 CRLF 破坏
f:write(png) f:close()
```

---

## 6. CDL（代码/数据日志）

```lua
local cdl = emu.getCdlData(emu.memType.nesPrgRom)
```

Mesen2 的 `CDLv2` 格式 = 9 字节头 + PRG 标志 + CHR 标志；
`tools/cdl.py` 已做头部偏移自适应搜索（`skip=9, layout=prg, code_bit=0x01`）。

解锁步骤：Debug → Code/Data Logger → 跑游戏 → 保存 `.cdl`。

---

## 7. 两个核心配方

### 配方 A：查"谁写了这个变量"

```lua
local counts = {}
emu.addMemoryCallback(function(address, value)
  local pc = emu.getState()["cpu.pc"]
  local key = string.format("$%04X", pc)   -- 注意：pc 是"下一条指令"
  counts[key] = (counts[key] or 0) + 1
end, emu.callbackType.write, 0x0500, 0x05FF, emu.memType.nesMemory)
```

判读：**多个 PC 各自命中相同次数 = 一次结构体写回**，不是多个独立变量。
完整模板见 `tools/lua/10_write_watch.lua`。

### 配方 B：查"谁调用了这个函数"（静态搜不到时的杀手锏）

JSR 会把**返回地址减一**压栈，栈指针 `SP` 指向最后压入的字节：

```lua
emu.addMemoryCallback(function()
  local sp = emu.getState()["cpu.sp"] % 256
  local lo = emu.read(0x0100 + ((sp + 1) % 256), emu.memType.nesInternalRam)
  local hi = emu.read(0x0100 + ((sp + 2) % 256), emu.memType.nesInternalRam)
  local caller = ((hi * 256) + lo + 1) % 65536
  -- 若 SP 不在 $01xx，说明是顺延进入而不是 JSR
end, emu.callbackType.exec, 0x8FA4, 0x8FA4, emu.memType.nesMemory)
```

完整模板见 `tools/lua/20_exec_callback.lua`。

---

## 8. 调试参数

| 参数 | 作用 |
|---|---|
| `--testRunner` | 无人值守跑脚本 |
| `--headless` | 无窗口 |
| `--windowSize` / `--scale` | 窗口尺寸 |
| `--noVideo` | 不渲染（更快，但截图失效） |

`settings.json` 里需开 `AllowIoOsAccess` 才能写文件（但仍只支持相对路径）。
**该文件必须是带 BOM 的 UTF-8**，否则 Mesen 读不出配置。
