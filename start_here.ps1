$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$ffmpegBin = Join-Path $projectRoot "tools\ffmpeg\bin"
$env:PATH = "$ffmpegBin;$env:PATH"
if ((-not (Test-Path -LiteralPath (Join-Path $projectRoot ".venv-audio\Scripts\python.exe"))) -or
    (-not (Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue)) -or (-not (Get-Command ffprobe -CommandType Application -ErrorAction SilentlyContinue))) {
  Write-Host "First run: installing the small Edge TTS runtime..." -ForegroundColor Cyan
  & (Join-Path $projectRoot "first_setup.ps1")
}
Write-Host "Open http://127.0.0.1:8787 in a browser after startup." -ForegroundColor Green
& (Join-Path $projectRoot "scripts\start.ps1")
if ($LASTEXITCODE -ne 0) { throw "Service exited with code $LASTEXITCODE" }
