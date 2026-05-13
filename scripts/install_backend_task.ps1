param(
  [string]$TaskName = $(if ($env:EVOVOICE_TASK_NAME) { $env:EVOVOICE_TASK_NAME } else { "EvoVoiceChat-Backend-Native" }),
  [switch]$Start
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
  python -m venv (Join-Path $backend ".venv")
}

& $python -m pip install -r (Join-Path $backend "requirements.txt")

$runScript = Join-Path $PSScriptRoot "run_backend.ps1"
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Force | Out-Null

if ($Start) {
  Start-ScheduledTask -TaskName $TaskName
}

[pscustomobject]@{
  TaskName = $TaskName
  Root = $root
  Python = $python
  Started = [bool]$Start
} | Format-List
