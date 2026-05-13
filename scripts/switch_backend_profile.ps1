param(
  [ValidateSet("local-lan", "local-tailscale", "server-to-local", "server-to-local-tailscale", "tailscale")]
  [string]$Profile = "local-lan",
  [string]$ApiKey,
  [switch]$PromptApiKey,
  [switch]$Restart,
  [string]$TaskName = $(if ($env:EVOVOICE_TASK_NAME) { $env:EVOVOICE_TASK_NAME } else { "EvoVoiceChat-Backend-Native" })
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$envFile = Join-Path $backend ".env"

function Get-ExistingEnvValue {
  param([string]$Name)
  if (-not (Test-Path $envFile)) { return $null }
  foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
    $key, $value = $trimmed.Split("=", 2)
    if ($key.Trim() -eq $Name) { return $value.Trim() }
  }
  return $null
}

function Write-EvoEnv {
  param(
    [string]$OpenAIBaseUrl,
    [string]$TTSBaseUrl,
    [string]$STTBaseUrl
  )

  $resolvedApiKey = $ApiKey
  if ($PromptApiKey -and -not $resolvedApiKey) {
    $secure = Read-Host "EVOWIT_OPENAI_API_KEY" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
      $resolvedApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
  }
  if (-not $resolvedApiKey) { $resolvedApiKey = $env:EVOWIT_OPENAI_API_KEY }
  if (-not $resolvedApiKey) { $resolvedApiKey = Get-ExistingEnvValue "EVOWIT_OPENAI_API_KEY" }
  if (-not $resolvedApiKey) { $resolvedApiKey = "" }

  $lines = @(
    "EVOVOICE_HOST=0.0.0.0",
    "EVOVOICE_PORT=30190",
    "",
    "EVOWIT_OPENAI_BASE_URL=$OpenAIBaseUrl",
    "EVOWIT_OPENAI_API_KEY=$resolvedApiKey",
    "EVOWIT_OPENAI_MODEL=gpt-5.5",
    "EVOWIT_OPENAI_TIMEOUT_SECONDS=90",
    "",
    "DELL_TTS_BASE_URL=$TTSBaseUrl",
    "DELL_TTS_MODEL=voxcpm2",
    "DELL_TTS_VOICE=default",
    "DELL_TTS_TIMEOUT_SECONDS=60",
    "",
    "DELL_STT_BASE_URL=$STTBaseUrl",
    "DELL_STT_MODEL=whisper-1",
    "DELL_STT_TIMEOUT_SECONDS=90",
    "",
    "NEWS_SEARCH_TIMEOUT_SECONDS=12",
    "NEWS_SEARCH_MAX_RESULTS=6",
    "WEB_SEARCH_TIMEOUT_SECONDS=12",
    "WEB_FETCH_TIMEOUT_SECONDS=12",
    "WEB_FETCH_TOP_RESULTS=1",
    "WEB_FETCH_MAX_CHARS=1200"
  )
  Set-Content -LiteralPath $envFile -Value $lines -Encoding UTF8

  $keyState = if ($resolvedApiKey) { "configured" } else { "missing" }
  [pscustomobject]@{
    Profile = $Profile
    EnvFile = $envFile
    OpenAIBaseUrl = $OpenAIBaseUrl
    TTSBaseUrl = $TTSBaseUrl
    STTBaseUrl = $STTBaseUrl
    ApiKey = $keyState
  }
}

switch ($Profile) {
  "local-lan" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "http://127.0.0.1:50553/v1" `
      -TTSBaseUrl "http://192.168.1.13:39040" `
      -STTBaseUrl "http://192.168.1.13:39050"
  }
  "local-tailscale" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "http://127.0.0.1:50553/v1" `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050"
  }
  "server-to-local" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "http://192.168.0.11:50553/v1" `
      -TTSBaseUrl "http://192.168.1.13:39040" `
      -STTBaseUrl "http://192.168.1.13:39050"
  }
  "server-to-local-tailscale" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "http://100.64.0.3:50553/v1" `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050"
  }
  "tailscale" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "https://sapi.evowit.com/v1" `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050"
  }
}

$result | Format-List

if ($Restart) {
  $env:EVOVOICE_TASK_NAME = $TaskName
  & (Join-Path $PSScriptRoot "restart_backend.ps1")
}
