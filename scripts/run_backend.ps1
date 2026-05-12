$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$envFile = Join-Path $backend ".env"

if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
    $name, $value = $line.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
  }
}

$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

$port = if ($env:EVOVOICE_PORT) { $env:EVOVOICE_PORT } else { "30190" }
Set-Location $backend
$args = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", $port)
if ($env:EVOVOICE_RELOAD -eq "1") {
  $args += "--reload"
}
& $python @args
