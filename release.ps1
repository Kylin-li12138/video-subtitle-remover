# release.ps1 - 一键发版脚本
# 用法: .\release.ps1
# 流程: 输入新版本号 -> 修改 config.py -> 提交 -> 打 tag -> 生成补丁 -> 推送

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 读取当前版本
$configFile = "backend\config.py"
$match = Select-String -Path $configFile -Pattern 'VERSION\s*=\s*"(.+?)"'
$currentVersion = $match.Matches.Groups[1].Value

Write-Host ""
Write-Host "=== Video Subtitle Remover 发版工具 ===" -ForegroundColor Cyan
Write-Host "当前版本: v$currentVersion" -ForegroundColor Yellow
Write-Host ""

# 输入新版本号
$newVersion = Read-Host "请输入新版本号 (如 1.0.2)"
if ([string]::IsNullOrWhiteSpace($newVersion)) {
    Write-Host "未输入版本号，已取消。" -ForegroundColor Red
    exit 1
}
if ($newVersion -eq $currentVersion) {
    Write-Host "新版本号与当前版本相同，已取消。" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "即将执行:" -ForegroundColor Cyan
Write-Host "  1. 修改版本号: $currentVersion -> $newVersion"
Write-Host "  2. 提交代码并打 tag v$newVersion"
Write-Host "  3. 生成补丁 (--from v$currentVersion)"
Write-Host "  4. 推送到远程仓库"
Write-Host ""
$confirm = Read-Host "确认? (y/n)"
if ($confirm -ne "y") {
    Write-Host "已取消。" -ForegroundColor Red
    exit 0
}

# Step 1: 修改版本号
Write-Host ""
Write-Host "[1/4] 修改版本号..." -ForegroundColor Green
$raw = Get-Content $configFile -Raw -Encoding UTF8
$oldPattern = 'VERSION = "' + $currentVersion + '"'
$newPattern = 'VERSION = "' + $newVersion + '"'
$raw = $raw.Replace($oldPattern, $newPattern)
Set-Content $configFile -Value $raw -Encoding UTF8 -NoNewline
Write-Host "  $configFile -> VERSION = $newVersion"

# Step 2: 提交 + 打 tag
Write-Host ""
Write-Host "[2/4] 提交代码并打 tag..." -ForegroundColor Green
git add -A
git commit -m "release: v$newVersion"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  提交失败！" -ForegroundColor Red
    exit 1
}
$ErrorActionPreference = "SilentlyContinue"
git tag -d "v$newVersion" 2>$null
$ErrorActionPreference = "Stop"
git tag "v$newVersion"
Write-Host "  tag v$newVersion 已创建"

# Step 3: 生成补丁
Write-Host ""
Write-Host "[3/4] 生成补丁包..." -ForegroundColor Green
python backend/tools/make_patch.py --from "v$currentVersion"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  补丁生成失败！" -ForegroundColor Red
    exit 1
}

# Step 4: 推送
Write-Host ""
Write-Host "[4/4] 推送到远程仓库..." -ForegroundColor Green
git push origin main --tags
if ($LASTEXITCODE -ne 0) {
    Write-Host "  推送失败！" -ForegroundColor Red
    exit 1
}

# 完成
Write-Host ""
Write-Host "=== 发版完成! ===" -ForegroundColor Cyan
Write-Host "  版本: v$newVersion" -ForegroundColor Green
Write-Host "  补丁: dist\patches\patch-v$newVersion.exe" -ForegroundColor Green
Write-Host ""
Write-Host "下一步: 把 patch-v$newVersion.exe 上传到 Electron Release Server" -ForegroundColor Yellow
Write-Host "  https://cmupdate.mengjun.icu" -ForegroundColor Yellow
Write-Host ""
