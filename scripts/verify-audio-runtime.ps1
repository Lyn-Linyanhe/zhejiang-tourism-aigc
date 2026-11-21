$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot "backend"
$env:PYTHONIOENCODING = "utf-8"
python .\scripts\verify_audio_runtime.py
if ($LASTEXITCODE -ne 0) { throw "Audio runtime verification failed" }
