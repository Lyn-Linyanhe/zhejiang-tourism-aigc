$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot "backend"
$port = 8788
$job = Start-Job -ScriptBlock {
  param($root, $port)
  Set-Location $root
  $env:PYTHONPATH = Join-Path $root "backend"
  $env:APP_PORT = "$port"
  python .\backend\main.py
} -ArgumentList $projectRoot, $port
try {
  $healthy = $false
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 300
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health"
      if ($health.ok) { $healthy = $true; break }
    } catch {}
  }
  if (-not $healthy) { throw "health check failed" }
  $body = '{"city":"\u676d\u5dde","landmark":"\u897f\u6e56","theme":"\u6625\u65e5\u57ce\u5e02\u6f2b\u6e38","culture":"\u5b8b\u97f5\u6587\u5316","festival":"\u6625\u5b63\u6587\u65c5\u63a8\u5e7f","audience":"\u5e74\u8f7b\u6e38\u5ba2","duration":15,"style":"\u8bd7\u610f\u7eaa\u5b9e","voice":"\u5973\u58f0","music_mood":"\u8212\u7f13","include_ai_label":true}'
  $task = Invoke-RestMethod "http://127.0.0.1:$port/api/tasks" -Method Post -ContentType "application/json" -Body $body
  Write-Host "created $($task.id)"
  $final = $null
  for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    $current = Invoke-RestMethod "http://127.0.0.1:$port/api/tasks/$($task.id)"
    Write-Host "$($current.stage) $($current.progress)% $($current.status)"
    if ($current.status -in @("completed", "failed")) { $final = $current; break }
  }
  if ($null -eq $final -or $final.status -ne "completed") { throw "task failed: $($final.error)" }
  $videoPath = Join-Path $projectRoot ("runtime\media\" + $task.id + "\final.mp4")
  if (-not (Test-Path -LiteralPath $videoPath)) { throw "video does not exist: $videoPath" }
  Write-Host "SMOKE TEST PASSED: $videoPath" -ForegroundColor Green
} finally {
  Stop-Job $job -ErrorAction SilentlyContinue
  Remove-Job $job -Force -ErrorAction SilentlyContinue
}
