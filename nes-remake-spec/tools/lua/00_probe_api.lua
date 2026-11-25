-- 00_probe_api.lua —— 自省 Mesen2 的 Lua API
--
-- 为什么要这个脚本
-- ----------------
-- Mesen2 的 Lua API 与 Mesen 1.x 有差异（函数名、状态表结构），网上教程常常过时。
-- 与其凭记忆写采样脚本、跑起来报错，不如先花 5 秒把真实 API 打印出来。
--
-- 用法
-- ----
-- Mesen2 → Tools → Script Window（或 Debugger 内的 Script 页）→ 载入本文件 → Run
-- 然后把 Log 窗口里的输出复制给我。
--
-- 输出会告诉我们三件事：
--   1. emu 表里到底有哪些函数 / 常量
--   2. emu.getState() 返回的状态表有哪些字段（CPU 寄存器怎么取）
--   3. 内存读取是 emu.read(addr) 还是 emu.read(addr, len)

local function L(s)
  emu.log(tostring(s))
end

L("========== PROBE START ==========")
L("Lua: " .. tostring(_VERSION))

-- 1) emu 表内容
L("---------- emu 表 ----------")
local n = 0
local ok = pcall(function()
  for k, v in pairs(emu) do
    L(string.format("  %-24s %s", tostring(k), type(v)))
    n = n + 1
  end
end)
if not ok then
  L("  pairs(emu) 失败 —— emu 不是普通 table，需逐个探测已知函数名")
  for _, name in ipairs({
    "read", "write", "getState", "getCpuState", "log",
    "addMemoryCallback", "addEventCallback", "registerEvent",
    "drawText", "setTimeout", "stop", "reset", "takeScreenshot",
    "memCallbackType", "eventType", "getLabel",
  }) do
    local okt, v = pcall(function() return emu[name] end)
    L(string.format("  emu.%-22s %s", name, okt and type(v) or "ERR"))
  end
end
L("  (共 " .. n .. " 项)")

-- 2) 状态表结构
L("---------- emu.getState() ----------")
local sok, st = pcall(function() return emu.getState() end)
if sok and type(st) == "table" then
  for k, v in pairs(st) do
    if type(v) == "table" then
      L(string.format("  %s (table):", tostring(k)))
      for k2, v2 in pairs(v) do
        L(string.format("      %s = %s", tostring(k2), tostring(v2)))
      end
    else
      L(string.format("  %-16s %s = %s", tostring(k), type(v), tostring(v)))
    end
  end
else
  L("  getState() 不可用或返回非 table: " .. tostring(st))
end

-- 3) 内存读取两种签名都试
L("---------- 内存读取签名 ----------")
local ok1, r1 = pcall(function() return emu.read(0x0000) end)
L(string.format("  emu.read(addr)      -> %s : %s", type(r1), tostring(r1)))
local ok2, r2 = pcall(function() return emu.read(0x0000, 16) end)
L(string.format("  emu.read(addr,len)  -> %s : %s", type(r2), tostring(r2)))
if type(r2) == "table" then
  local lo, hi = 0, 0
  for _ in pairs(r2) do hi = hi + 1 end
  L(string.format("    table 长度 %d，起始索引类型可能是 0 或 1（两种都试过）", hi))
end

L("========== PROBE END ==========")
