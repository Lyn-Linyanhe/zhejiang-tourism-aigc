param([switch]$Cuda)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$basePython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
if (-not (Test-Path -LiteralPath $basePython)) { throw '请先安装 Python 3.11，再运行本脚本。' }
$runtimePython = Join-Path $projectRoot '.venv-tianyi\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $basePython -m venv .venv-tianyi
    if ($LASTEXITCODE -ne 0) { throw '创建天依运行时失败' }
}
& $runtimePython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw '更新 pip 失败' }
if ($Cuda) {
    & $runtimePython -m pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124 --timeout 90
} else {
    & $runtimePython -m pip install torch==2.6.0 torchaudio==2.6.0 --timeout 90
}
if ($LASTEXITCODE -ne 0) { throw '安装 PyTorch 依赖失败' }
& $runtimePython -m pip install -r requirements-tianyi.txt --timeout 90
if ($LASTEXITCODE -ne 0) { throw '安装推理依赖失败' }
& $basePython scripts/setup-tianyi.py
if ($LASTEXITCODE -ne 0) { throw '下载天依模型失败，可重新运行本脚本' }
$env:PYTHONUTF8 = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& $runtimePython backend/app/providers/tianyi_worker.py --check
if ($LASTEXITCODE -ne 0) { throw '模型加载校验失败，尚未标记为安装完成' }
& $runtimePython -c "import json,torch; from pathlib import Path; Path('runtime/tianyi/installed.json').write_text(json.dumps({'torch':torch.__version__,'cuda':torch.cuda.is_available()}),encoding='utf-8')"
if ($LASTEXITCODE -ne 0) { throw '验证运行时失败' }
Write-Host '天依本地社区声库已安装，首次合成会加载模型。' -ForegroundColor Green
