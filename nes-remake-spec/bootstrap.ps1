#requires -Version 5.1
<#
.SYNOPSIS
    为 NES 逆向工作流准备工具链。一条命令搞定。

.DESCRIPTION
    目录布局（与脚本的相对路径假设一致）：

        <Workspace>\
        ├── <Game>.nes
        ├── tools\                    ← Mesen2 等二进制工具（共享）
        │   └── Mesen2\Mesen.exe
        └── <Game>\                   ← 游戏文件夹
            ├── DESIGN.md
            ├── L2-spec\
            ├── L3-data\
            └── tools\                ← Python / Lua 脚本（必须在这里）
                └── lua\

    1. 建立上述骨架
    2. 预建 %USERPROFILE%\Documents\Mesen2
       —— 不建这个目录，Mesen.exe 启动即崩（0xC0000409）
    3. 从 GitHub Releases 下载 Mesen2（curl.exe 走 Schannel 补证书链）
    4. 校验并解压到 tools\Mesen2\
    5. 写出可移植 settings.json（UTF-8 带 BOM，开 AllowIoOsAccess）
    6. 把 skill 自带的 Python/Lua 工具复制进 <Game>\tools\

.PARAMETER Workspace
    工作区根目录（放 .nes 的地方）。

.PARAMETER Game
    游戏文件夹名。留空则用 .nes 的文件名（去掉扩展名）。

.PARAMETER SkillRoot
    skill 目录。默认按本脚本位置推断。

.PARAMETER Force
    已存在也重新下载。

.EXAMPLE
    pwsh -File bootstrap.ps1 -Workspace I:\work\my-game
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [string]$Game = '',

    [string]$SkillRoot,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Info($m) { Write-Host "  $m" }
function Step($m) { Write-Host "`n[$m]" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  x $m" -ForegroundColor Red; exit 1 }

# ---------- 0. 路径 ----------
if (-not $SkillRoot) { $SkillRoot = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path $SkillRoot)) { Fail "找不到 skill 目录: $SkillRoot" }

$Workspace = [System.IO.Path]::GetFullPath($Workspace)
if (-not (Test-Path $Workspace)) { New-Item -ItemType Directory -Path $Workspace -Force | Out-Null }

# 推导游戏名
if (-not $Game) {
    $rom = Get-ChildItem $Workspace -Filter '*.nes' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($rom) { $Game = [System.IO.Path]::GetFileNameWithoutExtension($rom.Name) }
    else { $Game = 'game'; Warn "工作区里还没 .nes，游戏文件夹暂用 '$Game'" }
}
$GameDir = Join-Path $Workspace $Game
$BinTools = Join-Path $Workspace 'tools'
$GameTools = Join-Path $GameDir 'tools'
$LuaDir = Join-Path $GameTools 'lua'

Step '1/6 目录骨架'
foreach ($d in @($BinTools, (Join-Path $BinTools '_downloads'), $GameDir, $GameTools, $LuaDir,
                 (Join-Path $GameDir 'L2-spec'), (Join-Path $GameDir 'L3-data'), (Join-Path $GameDir 'evidence'))) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}
Info "workspace  = $Workspace"
Info "游戏文件夹 = $GameDir"
Info "二进制工具 = $BinTools"
Info "脚本       = $GameTools"

# ---------- 1. 预建 Mesen 用户目录（关键）----------
Step '2/6 预建 Mesen 用户目录'
$mesenHome = Join-Path $env:USERPROFILE 'Documents\Mesen2'
if (Test-Path $mesenHome) {
    Info "已存在: $mesenHome"
} else {
    New-Item -ItemType Directory -Path $mesenHome -Force | Out-Null
    Info "已创建: $mesenHome"
    Info "(不建这个目录，Mesen 启动会因 UnauthorizedAccessException 崩溃 0xC0000409)"
}

# ---------- 2. HTTP 客户端 ----------
$curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if (-not $curl) { $curl = (Get-Command curl -ErrorAction SilentlyContinue).Source }
if (-not $curl) {
    Warn '找不到 curl。Python 的 OpenSSL 不做 AIA 抓取，GitHub 证书链不完整会失败。'
    Fail "请安装 curl，或手动下载 Mesen2 解压到 $BinTools\Mesen2"
}
Info "curl = $curl"

# ---------- 3~5. Mesen2 ----------
$mesenDir = Join-Path $BinTools 'Mesen2'
$exe = Join-Path $mesenDir 'Mesen.exe'

if ((Test-Path $exe) -and -not $Force) {
    Step '3/6 Mesen2 已就绪'
    Info $exe
} else {
    Step '3/6 查询 Mesen2 最新版'
    $api = 'https://api.github.com/repos/SourMesen/Mesen2/releases/latest'
    $jsonPath = Join-Path $BinTools '_downloads\mesen_release.json'
    & $curl -sSL --retry 3 --retry-all-errors -o $jsonPath $api 2>&1 | Out-Null

    $assetUrl = $null; $assetName = $null; $tag = $null
    if (Test-Path $jsonPath) {
        try {
            $rel = Get-Content $jsonPath -Raw | ConvertFrom-Json
            $tag = $rel.tag_name
            $asset = $rel.assets | Where-Object { $_.name -match '^Mesen_.*_Windows\.zip$' } | Select-Object -First 1
            if ($asset) { $assetUrl = $asset.browser_download_url; $assetName = $asset.name }
        } catch { Warn "解析 release JSON 失败: $_" }
    }

    if (-not $assetUrl) {
        Warn '无法自动获取下载地址（限流或离线）。'
        Info '请手动从 https://github.com/SourMesen/Mesen2/releases 下载 *Windows.zip'
        Info "解压到: $mesenDir"
    } else {
        Step "4/6 下载 Mesen2 $tag"
        $zipPath = Join-Path $BinTools '_downloads' $assetName
        Info "下载 $assetUrl"
        & $curl -L --retry 3 --retry-all-errors --fail -o $zipPath $assetUrl
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipPath)) {
            Fail "下载失败（curl 退出码 $LASTEXITCODE）。可手动下载后解压到 $mesenDir"
        }
        $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
        $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash
        Info "已下载 $sizeMb MB  SHA256=$hash"

        Info '解压...'
        if (Test-Path $mesenDir) { Remove-Item $mesenDir -Recurse -Force }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $mesenDir -Force

        if (-not (Test-Path $exe)) {
            $found = Get-ChildItem $mesenDir -Filter 'Mesen.exe' -Recurse -File | Select-Object -First 1
            if ($found) {
                Info 'exe 在子目录，上提一层'
                Get-ChildItem $found.Directory.FullName -Force |
                    Move-Item -Destination $mesenDir -Force
            }
        }
        if (-not (Test-Path $exe)) { Fail "解压后找不到 Mesen.exe，请手动整理到 $mesenDir" }
        Info "Mesen2 就绪: $exe"
    }

    Step '5/6 写 settings.json'
    $settings = Join-Path $mesenDir 'settings.json'
    $cfg = @{
        TestRunner  = @{ AllowIoOsAccess = $true }
        Preferences = @{ SingleInstance = $false }
    } | ConvertTo-Json -Depth 5
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    [System.IO.File]::WriteAllText($settings, $cfg, $utf8Bom)
    Info "已写 $settings"
    Info '(UTF-8 带 BOM —— 无 BOM 时 Mesen 读不出配置)'
}

# ---------- 6. 复制脚本 ----------
Step '6/6 复制 Python / Lua 工具'
$srcTools = Join-Path $SkillRoot 'tools'
if (Test-Path $srcTools) {
    $py = Get-ChildItem $srcTools -Filter '*.py' -File -ErrorAction SilentlyContinue
    if ($py) { $py | Copy-Item -Destination $GameTools -Force }
    Info "Python 工具 $($py.Count) 个"

    $srcLua = Join-Path $srcTools 'lua'
    if (Test-Path $srcLua) {
        $lua = Get-ChildItem $srcLua -Filter '*.lua' -File
        if ($lua) { $lua | Copy-Item -Destination $LuaDir -Force }
        Info "Lua 模板 $($lua.Count) 个"
    }
} else {
    Warn "skill 里没有 tools\ 目录，跳过复制"
}

Write-Host "`n=== 完成 ===" -ForegroundColor Green
Write-Host @"
下一步：
  1. 把 .nes 放进 $Workspace
  2. cd $GameDir
  3. python tools\pipeline.py        # 静态普查（头/分区/反汇编）
  4. python tools\strscan.py         # 先扫字符串——最快发现隐藏功能
  5. $exe --testRunner <rom> tools\lua\00_probe_api.lua
"@
