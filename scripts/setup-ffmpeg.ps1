$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $projectRoot "tools\ffmpeg\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

if ((Test-Path -LiteralPath (Join-Path $binDir "ffmpeg.exe")) -and
    (Test-Path -LiteralPath (Join-Path $binDir "ffprobe.exe"))) {
  Write-Host "FFmpeg already present: $binDir" -ForegroundColor Green
  exit 0
}

# 源码仓库不含 FFmpeg 二进制（单文件 242MB，超出 GitHub 单文件上限）。
# Release 完整包内是固定版本 8.1.2 full build；此脚本下载的是同一站点的当前发行版，
# 版本号可能不同，但保留相同的 ffmpeg/ffprobe 命令行接口。
$url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$tmp = Join-Path $env:TEMP ("ffmpeg-" + [guid]::NewGuid().ToString("N") + ".zip")

Write-Host "Downloading $url ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
try {
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
  Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}

Write-Host "FFmpeg installed to $binDir" -ForegroundColor Green
Write-Host "Note: the exact pinned build shipped in the Release package is 8.1.2 full build; GPL v3 either way." -ForegroundColor DarkGray
