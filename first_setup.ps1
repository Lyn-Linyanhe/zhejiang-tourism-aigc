$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$ffmpegBin = Join-Path $projectRoot "tools\ffmpeg\bin"
$env:PATH = "$ffmpegBin;$env:PATH"
if (-not (Test-Path -LiteralPath (Join-Path $ffmpegBin "ffmpeg.exe")) -or
    -not (Test-Path -LiteralPath (Join-Path $ffmpegBin "ffprobe.exe"))) {
  # 完整离线包自带 FFmpeg；从 GitHub 源码克隆时这里为空，自动补下载。
  Write-Host "FFmpeg not found; downloading a standalone build..." -ForegroundColor Cyan
  & (Join-Path $projectRoot "scripts\setup-ffmpeg.ps1")
  if ($LASTEXITCODE -ne 0) { throw "FFmpeg setup failed. See README.md." }
}
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python was not found. Install Python 3.11 or 3.12 and enable Add python.exe to PATH." }
& python -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,14), 'Python 3.11-3.13 required'; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "Python version is unsupported; use Python 3.11 or 3.12." }
& ffmpeg -hide_banner -version | Select-Object -First 1
& ffprobe -hide_banner -version | Select-Object -First 1
& (Join-Path $projectRoot "scripts\setup-edge-tts.ps1")
if ($LASTEXITCODE -ne 0) { throw "Edge TTS setup failed." }
& python -m unittest tests.test_focus tests.test_rendering -q
if ($LASTEXITCODE -ne 0) { throw "Package smoke test failed." }
Write-Host "Setup complete. Run START.cmd." -ForegroundColor Green
