# 证据分级与文档规范

---

## 1. 四级证据（每条结论必须带一个）

| 标记 | 含义 | 获取方式 | 复刻者能怎么用 |
|---|---|---|---|
| `[已验证]` | 静态与运行时双向印证 | 反汇编 + 实测一致 | 直接照抄 |
| `[实测]` | 仅运行时观测，未复核代码 | 帧级采样 / 注入实验 | 参数值可用，慎用细节 |
| `[推断·强]` | 有代码或数据结构支撑但未运行时确认 | 读代码 / 读表 | 需自己验证一次 |
| `[待测]` | 明确未验证 | — | **不可用** |

### 分级判据

- 读到 `INC $58`（关卡号）**且**实测 `$58` 从 1 变 2 → `[已验证]`
- 只测到"敌人 1 像素/帧"但没读到那段代码 → `[实测]`
- 从 `$84E4` 的 `CMP #$33` 推出"共 51 关"，但没打到第 51 关 → `[推断·强]`
- 从没测过受击无敌帧 → `[待测]`，`meta.yml` 里填 `null`

### 铁律

**没测到的数字一律写 `null` 或 `[待测]`，绝不填"合理猜测"。**
一份诚实标了 30% `[待测]` 的文档，比一份 100% 看似完整但掺假的文档有用得多。

---

## 2. `DESIGN.md` 章节骨架

见 `templates/DESIGN.md`。十个章节，顺序有讲究：

| # | 章节 | 为什么放这个位置 |
|---|---|---|
| 1 | 概览 + 核心循环 + HUD | 读者先建立整体图像 |
| 2 | 技术底座 | 只留"影响设计"的部分（分辨率、帧率、场地尺度） |
| 3 | 输入系统 | 手感的第一因 |
| 4 | 关卡系统 | 内容生成，决定重复可玩性 |
| 5 | 玩法内核 | **主体**：移动/主行动/时序/判定/敌人/计分/胜负 |
| 6 | 设计分析 | 为什么这样设计，取舍在哪 |
| 7 | 复刻参数表 | **可照抄清单**，复刻者最先看这页 |
| 8 | 未测定项 | 诚实清单 + 下一步 |
| 9 | 工具与可复现命令 | 让别人能重跑 |
| 10 | 相关文档索引 | 指向 L2 细节 |

**§7 参数表是最有价值的一节。** 写成"秒"而非"帧"：

```
炸弹引信     160 帧  → 2.67 s
火焰持续      32 帧  → 0.53 s
输入采样       4 帧  → 15 Hz   ← 这是手感核心
```

---

## 3. `L2-spec/` 分工

每个子系统一个文件，作为 `DESIGN.md` 的证据底座：

```
level-format.md          关卡格式与生成
player-and-bombs.md      玩家 / 炸弹 / 爆炸
hud-and-economy.md       HUD / 时间 / 敌人 / 计分
input-system.md          输入链路与去抖
frame-pipeline.md        NMI / DMA / VRAM 队列
game-state-machine.md    状态机 $B5
life-and-gameover.md     命数与 Game Over
text-engine.md           文本渲染
graphics.md              CHR / 调色板 / 元图块
password.md              密码系统
dispatch-table.md        间接分派表
measurement-plan.md      还有哪些没测、怎么测
_exec-coverage.md        执行覆盖率
```

---

## 4. `meta.yml` 字段语义

跨游戏比较的关键。**未测填 `null`。**

```yaml
metrics:
  iframes: null                 # 受击无敌帧，null = 未测
  hitstop: null                 # 是否卡帧
  rng_kind: custom-4byte-prng   # 随机数类型（决定"可预测性"）
  playfield_tiles: 64x26        # 场地尺寸
  screen_time: 200              # 单关时限
  enemy_density: 2              # 每关敌人数
  powerup_cadence: 2            # 每关道具数，0 = 无道具

measured:                       # 实测数值，供复刻直接抄
  bomb_fuse_frames: 160
  ...
```

不要按游戏改键名 —— 键名固定才能横向比较。

---

## 5. 现代引擎的取舍

### 该丢掉（NES 硬件实现细节）

| 文档里的内容 | 现代引擎 |
|---|---|
| CHR、pattern table、nametable、OAM | SpriteRenderer / Texture2D |
| VRAM 队列、NMI、DMA | 丢弃 |
| 精灵闪烁（规避扫描线限制） | 不需要 |
| 倒序写入、`:` 顶替空格 | 正常 UI 文本 |
| PPU 地址（`$2041` 等） | 只用来理解"HUD 有哪些字段" |

### 该保留（承载手感的设计决策）

- **粗粒度输入采样**（如 15 Hz）：去掉会明显变"跟手"，就不像原作了
- **固定帧时序**：用秒实现，但别随手改
- **死亡演出的时长**：命数在演出末尾才递减，这个节奏影响紧张感
- **敌人不追踪**：威胁感来自"在窄道迎面相撞"，改成追踪会变成另一种游戏

---

## 6. 交付前自检

```powershell
# 未分级的数字（人工看一遍结果）
Select-String -Path DESIGN.md -Pattern '\d' |
  Where-Object { $_.Line -notmatch '\[已验证\]|\[实测\]|\[推断|\[待测\]' } |
  Select-Object -First 40

# 章节编号连续性（有过重复 5.10 的事故）
Select-String -Path DESIGN.md -Pattern '^#{2,3} '

# 未测定项存在
Select-String -Path DESIGN.md -Pattern '未测定|待测'

# meta.yml 可解析
python -c "import yaml,pathlib;print(len(yaml.safe_load(pathlib.Path('meta.yml').read_text(encoding='utf-8'))['metrics']))"
```

三问：
1. 每条结论都有等级吗？
2. 有没有把"我推断"写成"事实"？
3. 单次否定实验，补充验证过了吗？
