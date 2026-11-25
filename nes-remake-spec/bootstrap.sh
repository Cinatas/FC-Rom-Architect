#!/usr/bin/env bash
# bootstrap.sh —— 为 NES 逆向工作流准备工具链（Linux / macOS）
#
# 与 bootstrap.ps1 等价，但走 Linux/macOS 路径约定。
#
# 目录布局：
#   <Workspace>/
#   ├── <game>.nes
#   ├── tools/                    ← Mesen2 二进制（共享）
#   │   └── Mesen2/Mesen
#   └── <game>/                   ← 游戏文件夹
#       ├── DESIGN.md
#       ├── L2-spec/  L3-data/
#       └── tools/                ← Python / Lua 脚本（必须在这里）
#           └── lua/
#
# 用法：
#   ./bootstrap.sh -w /path/to/workspace [-g game] [-s /path/to/skill] [-f]
#
#   注意：Mesen2 release 提供 *zip*（不是 tar.gz），所以需要 unzip。

set -euo pipefail

WS=""
GAME=""
SKILL=""
FORCE=0

while getopts "w:g:s:fh" opt; do
  case "$opt" in
    w) WS="$OPTARG" ;;
    g) GAME="$OPTARG" ;;
    s) SKILL="$OPTARG" ;;
    f) FORCE=1 ;;
    h) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "用法: $0 -w <workspace> [-g game] [-s skilldir] [-f]" >&2; exit 2 ;;
  esac
done

info() { printf '  %s\n' "$*"; }
step() { printf '\n[%s]\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf '  x %s\n' "$*" >&2; exit 1; }

[ -n "$WS" ] || die "必须用 -w 指定工作区"
[ -n "$SKILL" ] || SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- 依赖 ----------
step "0/6 检查依赖"
for c in curl unzip; do
  command -v "$c" >/dev/null 2>&1 || die "缺少 $c（apt install $c / brew install $c）"
done
info "curl  = $(command -v curl)"
info "unzip = $(command -v unzip)"
command -v python3 >/dev/null 2>&1 && info "python3 = $(python3 -V 2>&1)" || warn "未找到 python3（静态分析脚本需要）"

# ---------- 目录 ----------
if [ -z "$GAME" ]; then
  rom=$(find "$WS" -maxdepth 1 -iname '*.nes' -type f 2>/dev/null | head -n1 || true)
  if [ -n "$rom" ]; then
    GAME="$(basename "${rom%.*}")"
  else
    GAME="game"
    warn "工作区里没有 .nes，游戏文件夹暂用 '$GAME'"
  fi
fi

BIN_TOOLS="$WS/tools"
GAME_DIR="$WS/$GAME"
GAME_TOOLS="$GAME_DIR/tools"
LUA_DIR="$GAME_TOOLS/lua"

step "1/6 目录骨架"
mkdir -p "$BIN_TOOLS/_downloads" "$GAME_DIR/L2-spec" "$GAME_DIR/L3-data" \
         "$GAME_DIR/evidence" "$GAME_TOOLS" "$LUA_DIR"
info "workspace  = $WS"
info "游戏文件夹 = $GAME_DIR"

# ---------- 架构 ----------
step "2/6 识别平台"
os="$(uname -s)"
arch="$(uname -m)"
case "$os" in
  Linux)  plat="Linux" ;;
  Darwin) plat="macOS" ;;
  *) die "不支持的系统: $os" ;;
esac
case "$arch" in
  x86_64|amd64) a="x64" ;;
  aarch64|arm64) a="ARM64" ;;
  *) die "不支持的架构: $arch" ;;
esac
info "系统=$os 架构=$arch → 需要 ${plat}_${a}"

# 资产名模式（实测 2.1.1 的命名）
case "$plat-$a" in
  Linux-x64)    asset_re='^Mesen_.*_Linux_x64\.zip$' ;;
  Linux-ARM64)  asset_re='^Mesen_.*_Linux_ARM64\.zip$' ;;
  macOS-x64)    asset_re='^Mesen_.*_macOS_x64_Intel\.zip$' ;;
  macOS-ARM64)  asset_re='^Mesen_.*_macOS_ARM64_AppleSilicon\.zip$' ;;
esac

MESEN_DIR="$BIN_TOOLS/Mesen2"
MESEN_BIN="$MESEN_DIR/Mesen"

if [ -x "$MESEN_BIN" ] && [ "$FORCE" -eq 0 ]; then
  step "3/6 Mesen2 已就绪"
  info "$MESEN_BIN"
else
  step "3/6 查询 Mesen2 最新版"
  api="https://api.github.com/repos/SourMesen/Mesen2/releases/latest"
  js="$BIN_TOOLS/_downloads/mesen_release.json"
  curl -fsSL --retry 3 --retry-all-errors -o "$js" "$api" 2>/dev/null || true

  url=""; name=""; tag=""
  if [ -s "$js" ] && command -v python3 >/dev/null 2>&1; then
    parsed="$(python3 - "$js" "$asset_re" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1]))
pat = re.compile(sys.argv[2])
pick = next((a for a in d.get("assets", []) if pat.match(a["name"])), None)
if pick:
    print(pick["browser_download_url"])
    print(pick["name"])
    print(d.get("tag_name", ""))
PY
)"
    url="$(printf '%s\n' "$parsed" | sed -n '1p')"
    name="$(printf '%s\n' "$parsed" | sed -n '2p')"
    tag="$(printf '%s\n' "$parsed" | sed -n '3p')"
  fi

  if [ -z "$url" ]; then
    warn "无法自动获取下载地址（限流或离线）"
    info "手动下载: https://github.com/SourMesen/Mesen2/releases"
    info "把 ${plat}_${a} 的 zip 解压出的 Mesen 放到: $MESEN_BIN"
  else
    step "4/6 下载 Mesen2 $tag"
    info "资产 $name"
    zip="$BIN_TOOLS/_downloads/$name"
    curl -fL --retry 3 --retry-all-errors -o "$zip" "$url" \
      || die "下载失败。可手动下载后解压到 $MESEN_DIR"
    info "已下载 $(du -h "$zip" | cut -f1)"

    info "解压..."
    rm -rf "$MESEN_DIR"
    mkdir -p "$MESEN_DIR"
    unzip -qo "$zip" -d "$MESEN_DIR"
    [ -f "$MESEN_BIN" ] || die "解压后找不到 Mesen（Linux 包应只含一个二进制）"
    chmod +x "$MESEN_BIN"
    info "Mesen2 就绪: $MESEN_BIN  ($("$MESEN_BIN" --version 2>/dev/null | head -n1 || echo '版本未知'))"
  fi

  # settings.json：Mesen2 需要 AllowIoOsAccess，Lua 的 io 才能写文件
  step "5/6 settings.json"
  CFG='{
  "TestRunner": { "AllowIoOsAccess": true },
  "Preferences": { "SingleInstance": false }
}'
  # 便携模式：放在二进制旁边
  printf '%s\n' "$CFG" > "$MESEN_DIR/settings.json"
  info "已写 $MESEN_DIR/settings.json"
  # XDG 配置目录兜底（部分版本/平台只读这里）
  if [ "$plat" = "Linux" ]; then
    XDG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/Mesen2"
    mkdir -p "$XDG_DIR"
    printf '%s\n' "$CFG" > "$XDG_DIR/settings.json"
    info "已写 $XDG_DIR/settings.json（兜底）"
  fi
  info "注：若 Lua 的 io.open 仍失败，本 skill 的模板会退化为 stdout 输出，"
  info "    可用 ./Mesen --testRunner rom.lua脚本 2>&1 | tee out.txt 收集结果。"
fi

# ---------- 复制脚本 ----------
step "6/6 复制 Python / Lua 工具"
if [ -d "$SKILL/tools" ]; then
  cp -f "$SKILL"/tools/*.py "$GAME_TOOLS"/ 2>/dev/null || true
  info "Python 工具 $(find "$GAME_TOOLS" -maxdepth 1 -name '*.py' | wc -l | tr -d ' ') 个"
  if [ -d "$SKILL/tools/lua" ]; then
    cp -f "$SKILL"/tools/lua/*.lua "$LUA_DIR"/ 2>/dev/null || true
    info "Lua 模板 $(find "$LUA_DIR" -maxdepth 1 -name '*.lua' | wc -l | tr -d ' ') 个"
  fi
else
  warn "skill 里没有 tools/ 目录"
fi

cat <<EOF

=== 完成 ===
下一步：
  1. 把 .nes 放进 $WS
  2. cd $GAME_DIR
  3. python3 tools/pipeline.py        # 静态普查
  4. python3 tools/strscan.py         # 先扫字符串（最快发现隐藏功能）
  5. $MESEN_BIN --testRunner <rom> tools/lua/00_probe_api.lua

提示：Mesen2 在无显示环境下需要虚拟屏。
  Xvfb :99 -screen 0 1024x768x24 &
  DISPLAY=:99 $MESEN_BIN --testRunner <rom> <script.lua>
EOF
