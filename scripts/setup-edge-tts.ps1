# 建一个只装 edge-tts 的隔离配音运行时。
#
# 和 scripts/setup-audio-runtime.ps1 的区别：那个会连 torch / whisperx 一起装
# （数 GB，且强制 Python 3.11），那套只有做字级对齐（whisperx）时才需要。
# 只想要好一点的配音，这个脚本就够了，任意 Python 3.9+ 都能建。
#
# .venv-audio 不能跨机器拷贝：venv 会把建立时的 base python 绝对路径写进
# pyvenv.cfg，换台机器就起不来。所以换机器后重跑一次本脚本即可。

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv-audio"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
  $base = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $base) {
    throw "PATH 里找不到 python。请先安装 Python 3.9 或更高版本。"
  }
  Write-Host "用 $base 创建 $venvRoot"
  & $base -m venv $venvRoot
  if ($LASTEXITCODE -ne 0) { throw "创建 .venv-audio 失败" }
}

& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet edge-tts==7.3.1
if ($LASTEXITCODE -ne 0) { throw "安装 edge-tts 失败，检查网络或代理" }

Write-Host "配音运行时已就绪：$venvPython" -ForegroundColor Green
Write-Host "现在 .env 里设 TTS_PROVIDER=edge（或 auto）即可。" -ForegroundColor Green
