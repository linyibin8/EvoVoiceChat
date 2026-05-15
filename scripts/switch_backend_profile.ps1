param(
  [ValidateSet("remote-server", "local-lan", "local-loopback", "local-tailscale", "server-to-local", "server-to-local-lan", "server-to-local-tailscale", "tailscale")]
  [string]$Profile = "remote-server",
  [string]$ApiKey,
  [switch]$PromptApiKey,
  [switch]$Restart,
  [string]$LocalLanOpenAIBaseUrl = $(if ($env:EVOVOICE_LAN_OPENAI_BASE_URL) { $env:EVOVOICE_LAN_OPENAI_BASE_URL } else { "http://192.168.0.11:50553/v1" }),
  [string]$DellLanTTSBaseUrl = $(if ($env:EVOVOICE_LAN_TTS_BASE_URL) { $env:EVOVOICE_LAN_TTS_BASE_URL } else { "http://192.168.0.13:39040" }),
  [string]$DellLanSTTBaseUrl = $(if ($env:EVOVOICE_LAN_STT_BASE_URL) { $env:EVOVOICE_LAN_STT_BASE_URL } else { "http://192.168.0.13:39050" }),
  [string]$RemoteOpenAIBaseUrl = $(if ($env:EVOVOICE_REMOTE_OPENAI_BASE_URL) { $env:EVOVOICE_REMOTE_OPENAI_BASE_URL } else { "http://100.64.0.3:50553/v1" }),
  [string]$PublicBackendUrl = $(if ($env:EVOVOICE_PUBLIC_BACKEND_URL) { $env:EVOVOICE_PUBLIC_BACKEND_URL } else { "https://evovoice.evowit.com" }),
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
    [string]$STTBaseUrl,
    [string]$IosBackendUrl
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
    "EVOVOICE_PROFILE=$Profile",
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
    "DELL_TTS_VOICE=voxcpm:auto",
    "DELL_TTS_TIMEOUT_SECONDS=60",
    "DELL_TTS_INFERENCE_TIMESTEPS=6",
    "DELL_TTS_REFERENCE_AUDIO=/home/dell/tts-stack/voxcpm2-openai/assets/evo_voice_ref.wav",
    "",
    "DELL_STT_BASE_URL=$STTBaseUrl",
    "DELL_STT_MODEL=whisper-1",
    "DELL_STT_TIMEOUT_SECONDS=90",
    "",
    "NEWS_SEARCH_TIMEOUT_SECONDS=12",
    "NEWS_SEARCH_MAX_RESULTS=6",
    "WEB_SEARCH_PROVIDER_ORDER=brave,tavily,searxng,duckduckgo,bing,google-news",
    "WEB_SEARCH_TIMEOUT_SECONDS=12",
    "WEB_FETCH_TIMEOUT_SECONDS=12",
    "WEB_FETCH_TOP_RESULTS=2",
    "WEB_FETCH_MAX_CHARS=1200",
    "WEB_READ_JINA_FALLBACK=true",
    "WEB_READ_JINA_MIN_CHARS=500"
  )
  Set-Content -LiteralPath $envFile -Value $lines -Encoding UTF8

  $keyState = if ($resolvedApiKey) { "configured" } else { "missing" }
  [pscustomobject]@{
    Profile = $Profile
    EnvFile = $envFile
    IosBackendUrl = $IosBackendUrl
    OpenAIBaseUrl = $OpenAIBaseUrl
    TTSBaseUrl = $TTSBaseUrl
    STTBaseUrl = $STTBaseUrl
    ApiKey = $keyState
  }
}

switch ($Profile) {
  "remote-server" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl $RemoteOpenAIBaseUrl `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050" `
      -IosBackendUrl $PublicBackendUrl
  }
  "local-lan" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl $LocalLanOpenAIBaseUrl `
      -TTSBaseUrl $DellLanTTSBaseUrl `
      -STTBaseUrl $DellLanSTTBaseUrl `
      -IosBackendUrl $PublicBackendUrl
  }
  "local-loopback" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "http://127.0.0.1:50553/v1" `
      -TTSBaseUrl $DellLanTTSBaseUrl `
      -STTBaseUrl $DellLanSTTBaseUrl `
      -IosBackendUrl $PublicBackendUrl
  }
  "local-tailscale" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "http://127.0.0.1:50553/v1" `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050" `
      -IosBackendUrl $PublicBackendUrl
  }
  "server-to-local" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl $LocalLanOpenAIBaseUrl `
      -TTSBaseUrl $DellLanTTSBaseUrl `
      -STTBaseUrl $DellLanSTTBaseUrl `
      -IosBackendUrl $PublicBackendUrl
  }
  "server-to-local-lan" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl $LocalLanOpenAIBaseUrl `
      -TTSBaseUrl $DellLanTTSBaseUrl `
      -STTBaseUrl $DellLanSTTBaseUrl `
      -IosBackendUrl $PublicBackendUrl
  }
  "server-to-local-tailscale" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl $RemoteOpenAIBaseUrl `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050" `
      -IosBackendUrl $PublicBackendUrl
  }
  "tailscale" {
    $result = Write-EvoEnv `
      -OpenAIBaseUrl "https://sapi.evowit.com/v1" `
      -TTSBaseUrl "http://100.64.0.5:39040" `
      -STTBaseUrl "http://100.64.0.5:39050" `
      -IosBackendUrl $PublicBackendUrl
  }
}

$result | Format-List

if ($Restart) {
  $env:EVOVOICE_TASK_NAME = $TaskName
  & (Join-Path $PSScriptRoot "restart_backend.ps1")
}
