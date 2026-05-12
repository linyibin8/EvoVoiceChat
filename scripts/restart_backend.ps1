$ErrorActionPreference = "Stop"

$taskName = if ($env:EVOVOICE_TASK_NAME) { $env:EVOVOICE_TASK_NAME } else { "EvoVoiceChat-Backend-Native" }
$port = if ($env:EVOVOICE_PORT) { [int]$env:EVOVOICE_PORT } else { 30190 }

$listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8

$health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec 10
$health.Content
