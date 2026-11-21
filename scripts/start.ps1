$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot "backend"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  throw "FFmpeg was not found. Install FFmpeg and add it to PATH."
}
Write-Host "Zhejiang tourism video service: http://127.0.0.1:8787" -ForegroundColor Green
python .\backend\main.py
