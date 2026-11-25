-- 40_poke_test.lua —— 定点注入实验（比写"会玩的机器人"便宜几十倍）
--
-- 用途：构造一个现实中很难摆出来的局面，测量它的后果。
--   例：在敌人脚下造一颗引信=1 的炸弹 → 测击杀、得分、死亡演出
--       在玩家脚下造炸弹       → 测死亡流程、命数何时递减
--       在某格写入一个物品值   → 测拾取条件
--
-- ⚠️ 注入会绕过碰撞 / 输入 / 前置判定。
--    注入"没反应"时，正确结论是「前置条件集合不完整」，不是「这个功能没用」。
--    可触发性必须再用真实输入验证一次。
--
-- 产物：poke_log.txt + pk_shot_XXXXX.png

-- ======== CONFIG ========
local CFG = {
  -- 要写入的动作：{ 帧, 地址, 值 }
  -- 地址可以是普通 RAM（用 MEM_RAM 写）
  writes = {
    -- { 500, 0x0200 + 9*32 + 14, 0x08 },   -- 例：把 (行9,列14) 写成"出口"
    -- { 500, 0x28, 14 }, { 500, 0x2A, 9 }, -- 例：把玩家传送到该格
  },

  -- 要监视的变量
  vars = {
    { 0xB5, 'state' },
    { 0x68, 'lives' },
  },

  fromFrame = 400,        -- 开始采样
  toFrame   = 1200,
  tickEvery = 15,         -- 每 N 帧打印一次全景（0=关）
  shots = { 480, 520, 560, 640, 760, 900 },

  hud = { base = 0x0040, len = 32 },
  logName = 'poke_log.txt',
}
-- ========================

local MEM_RAM = emu.memType.nesInternalRam
local MEM_NT  = emu.memType.nesNametableRam

local out = {}
local function say(s) out[#out + 1] = tostring(s) end
local function st(k, d)
  local ok, v = pcall(function() return emu.getState()[k] end)
  if ok and v ~= nil then return v end
  return d
end
local function frame() return st('frameCount', 0) end
local function openOut(name, mode)
  local ok, fh = pcall(function() return io.open(name, mode or 'w') end)
  if ok and fh then return fh end
  say('!! 打不开 ' .. name)
  return nil
end
local function rd(a)
  local ok, v = pcall(function() return emu.read(a, MEM_RAM) end)
  if ok and v ~= nil then return v % 256 end
  return 0
end
local function wr(a, v)
  pcall(function() emu.write(a, v % 256, MEM_RAM) end)
end
local function hudText()
  local t = {}
  for i = 0, CFG.hud.len - 1 do
    local ok, v = pcall(function() return emu.read(CFG.hud.base + i, MEM_NT) end)
    local b = (ok and v or 0) % 256
    t[#t + 1] = (b >= 0x20 and b < 0x7F) and string.char(b) or '.'
  end
  return table.concat(t)
end

local log = openOut(CFG.logName)
local function emit(line)
  say(line)
  if log then log:write(line .. '\n') end
end

-- 按帧索引写入动作
local byFrame = {}
for _, w in ipairs(CFG.writes) do
  byFrame[w[1]] = byFrame[w[1]] or {}
  table.insert(byFrame[w[1]], w)
end

local KEYS = { 'up','down','left','right','a','b','start','select' }
local function setAll(t)
  local o = {}
  for _, k in ipairs(KEYS) do o[k] = t[k] or false end
  pcall(function() emu.setInput(o, 0) end)
end
pcall(function()
  emu.addEventCallback(function()
    local f = frame()
    if f >= 100 and f < 116 then setAll({ start = true }) else setAll({}) end
  end, emu.eventType.inputPolled)
end)

local last, si, done = {}, 1, false

pcall(function()
  emu.addEventCallback(function()
    local f = frame()
    if f > CFG.toFrame then
      if not done then
        done = true
        if log then log:flush() log:close() end
        print(table.concat(out, '\n'))
        pcall(function() emu.stop(0) end)
      end
      return
    end

    -- 执行注入
    if byFrame[f] then
      for _, w in ipairs(byFrame[f]) do
        local before = rd(w[2])
        wr(w[2], w[3])
        emit(string.format('帧 %d: 写入 $%04X  %02X -> %02X', f, w[2], before, rd(w[2])))
      end
    end

    -- 采样变化
    if f >= CFG.fromFrame then
      for _, v in ipairs(CFG.vars) do
        local val = rd(v[1])
        local key = v[2] or string.format('%02X', v[1])
        if last[key] ~= val then
          emit(string.format('帧 %d: %s = %02X', f, key, val))
          last[key] = val
        end
      end
      if CFG.tickEvery > 0 and f % CFG.tickEvery == 0 then
        local parts = {}
        for _, v in ipairs(CFG.vars) do
          parts[#parts + 1] = string.format('%s=%02X', v[2] or string.format('%02X', v[1]), rd(v[1]))
        end
        emit(string.format('    tick %d  %s  HUD=%s', f, table.concat(parts, ' '), hudText()))
      end
    end

    -- 截图
    if si <= #CFG.shots and f >= CFG.shots[si] then
      local ok, png = pcall(function() return emu.takeScreenshot() end)
      if ok and type(png) == 'string' then
        local sf = openOut(string.format('pk_shot_%05d.png', CFG.shots[si]), 'wb')
        if sf then sf:write(png) sf:close() end
      end
      si = si + 1
    end
  end, emu.eventType.endFrame)
end)

say('40_poke_test.lua 就绪：' .. #CFG.writes .. ' 个注入动作')
