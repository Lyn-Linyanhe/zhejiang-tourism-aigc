param(
  [string]$ZipPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $projectRoot "tools\ffmpeg\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

if ((Test-Path -LiteralPath (Join-Path $binDir "ffmpeg.exe")) -and
    (Test-Path -LiteralPath (Join-Path $binDir "ffprobe.exe"))) {
  Write-Host "FFmpeg already present: $binDir" -ForegroundColor Green
  exit 0
}

# 源码仓库不含 FFmpeg 二进制（单文件约 242MB，超出 GitHub 单文件上限）。
# 优先 Gyan；部分 Windows 环境会因 TLS 吊销列表离线而在这里失败，因此再尝试
# GitHub 的 BtbN 官方构建。两者都提供相同的 ffmpeg / ffprobe 命令行接口。
$sources = @(
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)
$tmp = Join-Path $env:TEMP ("ffmpeg-" + [guid]::NewGuid().ToString("N") + ".zip")
$downloaded = $false

if ($ZipPath) {
  $resolvedZip = (Resolve-Path -LiteralPath $ZipPath -ErrorAction Stop).Path
  $tmp = $resolvedZip
  Write-Host "Using local FFmpeg archive: $tmp" -ForegroundColor Cyan
} else {
  foreach ($url in $sources) {
    Write-Host "Downloading $url ..." -ForegroundColor Cyan
    $downloadOk = $false
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
      # 30 秒内平均低于 64 KB/s 或超过 5 分钟就放弃当前源，避免一个慢镜像
      # 让安装脚本卡住数小时；随后自动尝试下一个源。
      & curl.exe --fail --location --silent --show-error --retry 2 --retry-delay 2 `
        --connect-timeout 20 --speed-limit 65536 --speed-time 30 --max-time 300 `
        --output $tmp $url
      $downloadOk = $LASTEXITCODE -eq 0
    } else {
      try {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        $downloadOk = $true
      } catch {
        Write-Warning $_.Exception.Message
      }
    }
    if ($downloadOk -and (Test-Path -LiteralPath $tmp) -and (Get-Item -LiteralPath $tmp).Length -gt 10MB) {
      $downloaded = $true
      break
    }
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    Write-Warning "Download failed: $url"
  }
  if (-not $downloaded) {
    throw "All FFmpeg download sources failed. Download a Windows FFmpeg ZIP manually and rerun: powershell -ExecutionPolicy Bypass -File scripts\setup-ffmpeg.ps1 -ZipPath .\your-ffmpeg.zip"
  }
}

try {
  if (-not (Test-Path -LiteralPath $tmp) -or (Get-Item -LiteralPath $tmp).Length -lt 10MB) {
    throw "FFmpeg archive is missing or suspiciously small: $tmp"
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($tmp)
  try {
    foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
      $entry = $zip.Entries | Where-Object { $_.Name -eq $name } | Select-Object -First 1
      if (-not $entry) { throw "Archive does not contain $name" }
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile(
        $entry, (Join-Path $binDir $name), $true)
      Write-Host "Extracted $name" -ForegroundColor Green
    }
  } finally { $zip.Dispose() }
} finally {
  if ($downloaded) {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
  }
}

Write-Host "FFmpeg installed to $binDir" -ForegroundColor Green
& (Join-Path $binDir "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
& (Join-Path $binDir "ffprobe.exe") -hide_banner -version | Select-Object -First 1
Write-Host "Note: downloaded builds are GPL. Do not commit the binaries; only tools\ffmpeg\LICENSE and README belong in Git." -ForegroundColor DarkGray
