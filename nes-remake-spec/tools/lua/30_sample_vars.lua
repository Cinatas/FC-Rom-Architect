-- 30_sample_vars.lua —— 变量采样 + HUD 文本 + 定时截图（通用测量骨架）
--
-- 用途：给一堆已知的 RAM 变量做逐帧采样，同时把 nametable 里的 HUD 文本读出来，
--       并在关键帧截图。这是产出"实测参数"的主力工具。
--
-- 关键提醒：
--   * nametable RAM 是 **0 基址**（0x0000-0x07FF）。传 0x2000 会静默返回全 0。
--     屏幕第 r 行第 c 列 = r*32 + c。
--   * 截图必须用 "wb"，否则 PNG 被 CRLF 破坏（症状：只有 2-3 KB，打不开）。
--   * 只打印**变化**，否则每帧一行根本读不了。
--
-- 产物：
--   sample_log.txt   变化日志
--   smp_shot_XXXXX.png
--
-- 用法：Mesen.exe --testRunner <rom> 30_sample_vars.lua

-- ======== CONFIG ========
local CFG = {
  toFrame = 1200,

  -- 要监视的变量：{ 地址, 标签 }
  vars = {
    { 0x68, 'lives' },
    { 0xB5, 'state' },
    -- { 0x28, 'player_col' },
    -- { 0x2A, 'player_row' },
  },

  -- HUD：屏幕第 2 行的 32 个字符
  hud = { base = 0x0040, len = 32 },
  hudEvery = 60,

  shots = { 200, 400, 600, 800, 1000 },   -- 要截图的帧
  logName = 'sample_log.txt',
  dumpEvery = 0,      -- >0 时每 N 帧打印一次全部变量（调试用）
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
local last, lastHud, si, done = {}, nil, 1, false

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
    elseif f >= 400 and f < 460 then setAll({ right = true })
    elseif f >= 520 and f < 580 then setAll({ down = true })
    elseif f >= 640 and f < 650 then setAll({ a = true })
    else setAll({}) end
  end, emu.eventType.inputPolled)
end)

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

    -- 变化检测
    for _, v in ipairs(CFG.vars) do
      local val = rd(v[1])
      local key = v[2] or string.format('%02X', v[1])
      if last[key] ~= val then
        local extra = ''
        if CFG.dumpEvery > 0 then
          local parts = {}
          for _, w in ipairs(CFG.vars) do
            parts[#parts + 1] = string.format('%s=%02X', w[2] or string.format('%02X', w[1]), rd(w[1]))
          end
          extra = '  [' .. table.concat(parts, ' ') .. ']'
        end
        local line = string.format('帧 %5d: %-12s = %02X%s', f, key, val, extra)
        say(line)
        if log then log:write(line .. '\n') end
        last[key] = val
      end
    end

    -- 周期 HUD
    if CFG.hudEvery > 0 and f % CFG.hudEvery == 0 then
      local t = hudText()
      if t ~= lastHud then
        local line = string.format('帧 %5d: HUD = %s', f, t)
        say(line)
        if log then log:write(line .. '\n') end
        lastHud = t
      end
    end

    -- 截图
    if si <= #CFG.shots and f >= CFG.shots[si] then
      local ok, png = pcall(function() return emu.takeScreenshot() end)
      if ok and type(png) == 'string' then
        local name = string.format('smp_shot_%05d.png', CFG.shots[si])
        local sf = openOut(name, 'wb')          -- ★ 必须 wb
        if sf then sf:write(png) sf:close() end
      end
      si = si + 1
    end
  end, emu.eventType.endFrame)
end)

say('30_sample_vars.lua 就绪：监视 ' .. #CFG.vars .. ' 个变量')
