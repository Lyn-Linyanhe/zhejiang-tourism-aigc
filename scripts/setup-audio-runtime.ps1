$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv-audio"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$requirements = Join-Path $projectRoot "requirements-audio.txt"
$diagnostics = Join-Path $projectRoot "runtime\diagnostics"

function Resolve-Python311 {
  try {
    $resolved = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $resolved) { return $resolved.Trim() }
  } catch {}

  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "Python 3.11 is missing and winget is unavailable. Install Python 3.11.9 first."
  }
  & winget install --id Python.Python.3.11 --exact --version 3.11.9 --scope user --silent `
    --accept-package-agreements --accept-source-agreements --disable-interactivity
  if ($LASTEXITCODE -ne 0) { throw "winget could not install Python 3.11.9" }
  $resolved = & py -3.11 -c "import sys; print(sys.executable)"
  if ($LASTEXITCODE -ne 0 -or -not $resolved) { throw "Python 3.11 was installed but cannot be resolved" }
  return $resolved.Trim()
}

$python311 = Resolve-Python311
if (-not (Test-Path -LiteralPath $venvPython)) {
  & $python311 -m venv $venvRoot
  if ($LASTEXITCODE -ne 0) { throw "Could not create .venv-audio" }
}

$version = & $venvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0 -or -not $version.StartsWith("3.11.")) {
  throw ".venv-audio exists but does not use Python 3.11: $version"
}

& $venvPython -m pip install --timeout 120 --retries 20 --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Could not update audio runtime packaging tools" }
& $venvPython -m pip install --timeout 120 --retries 20 --requirement $requirements
if ($LASTEXITCODE -ne 0) { throw "Could not install audio runtime requirements" }

New-Item -ItemType Directory -Force -Path $diagnostics | Out-Null
$lockPath = Join-Path $diagnostics "requirements-audio.lock.txt"
$freeze = & $venvPython -m pip freeze --all
[System.IO.File]::WriteAllLines($lockPath, $freeze, [System.Text.UTF8Encoding]::new($false))

Write-Host "Audio runtime ready: $venvPython" -ForegroundColor Green
Write-Host "Frozen dependency list: $lockPath"
