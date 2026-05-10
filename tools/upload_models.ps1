# upload_models.ps1
# 从完整版 VSR 安装目录中提取模型文件，打包为 zip 并上传到 GitHub Release
#
# 用法:
#   .\tools\upload_models.ps1 -SourceDir "D:\path\to\full-vsr"
#
# SourceDir 应该是包含 models/ 和 ffmpeg/ 目录的完整版 VSR 根目录
# 例如从原始 vsr-windows-nvidia-cuda-11.8.7z 解压后的目录

param(
    [Parameter(Mandatory=$true)]
    [string]$SourceDir
)

$ErrorActionPreference = "Stop"
$repo = "Kylin-li12138/video-subtitle-remover"
$tag = "models-v1"
$outDir = Join-Path $PSScriptRoot "..\dist\model-zips"
$ghExe = "C:\Program Files\GitHub CLI\gh.exe"

if (-not (Test-Path $ghExe)) {
    Write-Host "GitHub CLI not found at $ghExe" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$models = @(
    @{ Name="big-lama";    Zip="big-lama.zip";    Dirs=@("models\big-lama") },
    @{ Name="propainter";  Zip="propainter.zip";  Dirs=@("models\propainter") },
    @{ Name="sttn";        Zip="sttn.zip";        Dirs=@("models\sttn-auto","models\sttn-det") },
    @{ Name="v5-det";      Zip="v5-det.zip";      Dirs=@("models\V5") },
    @{ Name="ffmpeg";      Zip="ffmpeg-win64.zip"; Dirs=@("ffmpeg") }
)

Write-Host ""
Write-Host "=== VSR Model Packager & Uploader ===" -ForegroundColor Cyan
Write-Host "Source: $SourceDir"
Write-Host "Output: $outDir"
Write-Host ""

foreach ($m in $models) {
    $zipPath = Join-Path $outDir $m.Zip
    Write-Host "[$($m.Name)] " -NoNewline -ForegroundColor Yellow

    $allExist = $true
    foreach ($d in $m.Dirs) {
        $full = Join-Path $SourceDir $d
        if (-not (Test-Path $full)) {
            Write-Host "SKIP - $d not found" -ForegroundColor Red
            $allExist = $false
            break
        }
    }
    if (-not $allExist) { continue }

    if (Test-Path $zipPath) {
        Write-Host "zip already exists, skipping build" -ForegroundColor Gray
    } else {
        Write-Host "packing..." -NoNewline
        $tempDir = Join-Path $env:TEMP "vsr_model_pack_$($m.Name)"
        if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

        foreach ($d in $m.Dirs) {
            $src = Join-Path $SourceDir $d
            $dst = Join-Path $tempDir $d
            $parent = Split-Path $dst -Parent
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            Copy-Item $src $dst -Recurse
        }

        Compress-Archive -Path "$tempDir\*" -DestinationPath $zipPath -Force
        Remove-Item $tempDir -Recurse -Force
        $sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
        Write-Host " done ($sizeMB MB)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== Uploading to GitHub Release ===" -ForegroundColor Cyan
Write-Host ""

$zips = Get-ChildItem $outDir -Filter "*.zip"
if ($zips.Count -eq 0) {
    Write-Host "No zip files to upload!" -ForegroundColor Red
    exit 1
}

foreach ($z in $zips) {
    Write-Host "Uploading $($z.Name)..." -NoNewline
    & $ghExe release upload $tag $z.FullName --repo $repo --clobber 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
    } else {
        Write-Host " FAILED" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Release: https://github.com/$repo/releases/tag/$tag" -ForegroundColor Yellow
Write-Host ""
