-- 10_write_watch.lua —— 谁在写这块 RAM？（写回调 → 写入者 PC 统计）
--
-- 用途：定位变量、发现对象数组、确认某个字段的写点。
--
-- 判读要点（很重要）：
--   * 回调报的 PC 是**下一条指令**。2 字节的 STA zp，真实写点 = PC - 2。
--   * **多个 PC 各自命中相同的次数 = 一次结构体写回**，不是多个独立变量。
--     例如 12 个 PC 各 3030 次 → 一次写 12 个字段的拷贝例程。
--
-- 产物（落在 Mesen.exe 目录，因为 --testRunner 下绝对路径写不了）：
--   write_watch.csv        frame,pc,addr,value,a,x,y
--   write_watch_sum.txt    按命中次数排序的写入者清单
--
-- 用法：Mesen.exe --testRunner <rom> 10_write_watch.lua

-- ======== CONFIG ========
local CFG = {
  lo        = 0x0500,          -- 要监视的地址范围（含）
  hi        = 0x05FF,
  memType   = emu.memType.nesMemory,
  fromFrame = 100,             -- 跳过开机初始化
  -- 写回调的用途是看 PC 分布，几百帧就足够稳定——
  -- 不要盲目跑几千帧：单帧可能有几十次写入，长时间跑会拖慢甚至拖死。
  toFrame   = 900,
  maxRows   = 100000,          -- CSV 行数上限
  csvName   = 'write_watch.csv',
  sumName   = 'write_watch_sum.txt',
}
-- ========================

local MEM_RAM = emu.memType.nesInternalRam
local out = {}
local function say(s) out[#out + 1] = tostring(s) end
local function st(k, d)
  local ok, v = pcall(function() return emu.getState()[k] end)
  if ok and v ~= nil then return v end
  return d
end
local function frame() return st('frameCount', 0) end
local function openOut(name, mode)
  -- 绝对路径在 --testRunner 下必败，只能用相对路径
  local ok, fh = pcall(function() return io.open(name, mode or 'w') end)
  if ok and fh then return fh end
  say('!! 打不开 ' .. name)
  return nil
end

local csv = openOut(CFG.csvName)
if csv then csv:write('frame,pc,addr,value,a,x,y\n') end

local counts, n = {}, 0

local function onWrite(address, value)
  local f = frame()
  if f < CFG.fromFrame or f > CFG.toFrame then return end
  local pc = st('cpu.pc', 0)
  n = n + 1
  -- 定期 flush，避免进程被杀时丢数据
  if csv and n % 5000 == 0 then csv:flush() end
  if csv and n <= CFG.maxRows then
    csv:write(string.format('%d,$%04X,$%04X,$%02X,$%02X,$%02X,$%02X\n',
      f, pc, address, value or 0,
      st('cpu.a', 0) % 256, st('cpu.x', 0) % 256, st('cpu.y', 0) % 256))
  end
  local key = string.format('$%04X', pc)
  counts[key] = (counts[key] or 0) + 1
end

pcall(function()
  emu.addMemoryCallback(onWrite, emu.callbackType.write, CFG.lo, CFG.hi, CFG.memType)
end)

-- ======== 输入：按需修改 ========
local KEYS = { 'up','down','left','right','a','b','start','select' }
local function setAll(t)
  -- 必须显式给全部按键；未指定的键会保持原状态（常见 bug）
  local o = {}
  for _, k in ipairs(KEYS) do o[k] = t[k] or false end
  pcall(function() emu.setInput(o, 0) end)
end
pcall(function()
  emu.addEventCallback(function()
    local f = frame()
    if f >= 100 and f < 116 then setAll({ start = true })   -- 进游戏
    elseif f >= 400 and f < 700 then setAll({ right = true }) -- 制造一些活动
    else setAll({}) end
  end, emu.eventType.inputPolled)
end)
-- ==============================

local done = false
pcall(function()
  emu.addEventCallback(function()
    local now = frame()
    if CFG.fromFrame > 0 and now > CFG.fromFrame and now % 300 == 0 then
      say(string.format('  … 帧 %d  已记录 %d 次写入', now, n))
    end
    if now >= CFG.toFrame and not done then
      done = true
      if csv then csv:flush() csv:close() end

      local list = {}
      for k, c in pairs(counts) do list[#list + 1] = { k = k, c = c } end
      table.sort(list, function(a, b) return a.c > b.c end)

      say(string.format('$%04X-$%04X 共 %d 次写入，涉及 %d 个 PC',
        CFG.lo, CFG.hi, n, #list))
      local sum = openOut(CFG.sumName)
      for i = 1, math.min(#list, 40) do
        local pc = tonumber(list[i].k:sub(2), 16)
        local line = string.format('%s   (指令 $%04X)   %d 次',
          list[i].k, (pc - 2) % 65536, list[i].c)
        say(line)
        if sum then sum:write(line .. '\n') end
      end
      if sum then sum:close() end
      print(table.concat(out, '\n'))
      pcall(function() emu.stop(0) end)
    end
  end, emu.eventType.endFrame)
end)

say('10_write_watch.lua 就绪：监视 $' .. string.format('%04X-$%04X', CFG.lo, CFG.hi))
