-- 20_exec_callback.lua —— 查"谁调用了这个函数"（静态搜不到时的杀手锏）
--
-- 适用场景：
--   * 某个例程明明在执行（CDL 显示已覆盖），但 JSR / 指针表 / 任何 xref 都搜不到调用者
--   * 说明调用是计算出来的（间接跳转、状态表、或顺延进入）
--
-- 原理：JSR 会把「返回地址 - 1」压栈，SP 指向最后压入的字节。
--   lo = mem[0x0100 + (SP+1) & 0xFF]
--   hi = mem[0x0100 + (SP+2) & 0xFF]
--   调用点 = hi*256 + lo + 1
-- 若 SP 不在 $01xx（不在栈页），说明是顺延/跳转进入，不是 JSR。
--
-- 产物：
--   exec_watch.csv      frame,pc,sp,stk_lo,stk_hi,caller,a,x,y
--   exec_watch_sum.txt   caller -> 命中次数
--
-- 用法：Mesen.exe --testRunner <rom> 20_exec_callback.lua

-- ======== CONFIG ========
local CFG = {
  -- 目标例程。若 PRG 16KB 被镜像到 $C000，两个地址都挂上更保险。
  addresses = { 0x8FA4, 0xCFA4 },

  fromFrame = 100,
  toFrame   = 2600,
  maxRows   = 20000,
  csvName   = 'exec_watch.csv',
  sumName   = 'exec_watch_sum.txt',
}
-- ========================

local MEM_RAM = emu.memType.nesInternalRam
local MEM_CPU = emu.memType.nesMemory
local out = {}
local function say(s) out[#out + 1] = tostring(s) end
local function st(k, d)
  local ok, v = pcall(function() return emu.getState()[k] end)
  if ok and v ~= nil then return v end
  return d
end
local function frame() return st('frameCount', 0) end
local function openOut(name)
  local ok, fh = pcall(function() return io.open(name, 'w') end)
  if ok and fh then return fh end
  say('!! 打不开 ' .. name)
  return nil
end
local function rd(a)
  local ok, v = pcall(function() return emu.read(a, MEM_RAM) end)
  if ok and v ~= nil then return v % 256 end
  return 0
end

local csv = openOut(CFG.csvName)
if csv then csv:write('frame,pc,sp,stk_lo,stk_hi,caller,a,x,y\n') end

local callers, n, fromFallthrough = {}, 0, 0

local function onExec()
  local f = frame()
  if f < CFG.fromFrame or f > CFG.toFrame then return end

  local pc = st('cpu.pc', 0)
  local sp = st('cpu.sp', 0) % 256
  local lo = rd(0x0100 + ((sp + 1) % 256))
  local hi = rd(0x0100 + ((sp + 2) % 256))
  local caller = ((hi * 256) + lo + 1) % 65536

  -- 栈页判断：SP 一般在 $01xx 页内（这里 SP 是页内偏移，取不到页号时
  -- 用"返回地址是否落在合理代码区"作为经验判据）
  local plausible = caller >= 0x8000
  if not plausible then fromFallthrough = fromFallthrough + 1 end

  n = n + 1
  if csv and n <= CFG.maxRows then
    csv:write(string.format('%d,$%04X,%02X,%02X,%02X,$%04X,%02X,%02X,%02X\n',
      f, pc, sp, lo, hi, caller,
      st('cpu.a', 0) % 256, st('cpu.x', 0) % 256, st('cpu.y', 0) % 256))
  end
  local key = string.format('$%04X -> $%04X', pc, caller)
  callers[key] = (callers[key] or 0) + 1
end

for _, addr in ipairs(CFG.addresses) do
  pcall(function()
    emu.addMemoryCallback(onExec, emu.callbackType.exec, addr, addr, MEM_CPU)
  end)
end

-- ======== 输入 ========
local KEYS = { 'up','down','left','right','a','b','start','select' }
local function setAll(t)
  local o = {}
  for _, k in ipairs(KEYS) do o[k] = t[k] or false end
  pcall(function() emu.setInput(o, 0) end)
end
pcall(function()
  emu.addEventCallback(function()
    local f = frame()
    if f >= 100 and f < 116 then setAll({ start = true })
    elseif f >= 400 and f < 700 then setAll({ right = true })
    elseif f >= 800 and f < 810 then setAll({ a = true })
    else setAll({}) end
  end, emu.eventType.inputPolled)
end)
-- =====================

local done = false
pcall(function()
  emu.addEventCallback(function()
    if frame() >= CFG.toFrame and not done then
      done = true
      if csv then csv:flush() csv:close() end

      local list = {}
      for k, c in pairs(callers) do list[#list + 1] = { k = k, c = c } end
      table.sort(list, function(a, b) return a.c > b.c end)

      say(string.format('命中 %d 次，其中 %d 次栈上不像合法调用点',
        n, fromFallthrough))
      local sum = openOut(CFG.sumName)
      for i = 1, math.min(#list, 40) do
        local line = string.format('%s   %d 次', list[i].k, list[i].c)
        say(line)
        if sum then sum:write(line .. '\n') end
      end
      if sum then sum:close() end
      print(table.concat(out, '\n'))
      pcall(function() emu.stop(0) end)
    end
  end, emu.eventType.endFrame)
end)

say('20_exec_callback.lua 就绪')
