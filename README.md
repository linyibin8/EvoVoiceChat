# EvoVoiceChat

Native iOS voice chat MVP for a Yuanbao/Doubao-style assistant.

## What is included

- SwiftUI iOS app with text chat, push-to-talk voice mode, live speech recognition, Dell TTS playback, and real-time latency/RTF display.
- FastAPI backend proxy for:
  - OpenAI-compatible chat completions through the evowit endpoint.
  - Streaming chat completions via Server-Sent Events (`/api/chat/stream`) for incremental UI updates.
  - Dell VoxCPM2 TTS (`/v1/audio/speech`) with speed headers.
  - Dell Whisper STT (`/v1/audio/transcriptions`) for fallback transcription.
  - Latest-news search via Google News RSS fallback, with source-domain filters.
  - iOS segmented TTS playback: stream text first, synthesize completed sentence chunks, and play the queue while later text is still arriving.

## Local backend

Copy `backend/.env.example` to `backend/.env` and fill secrets locally.

```powershell
cd D:\AI\EvoVoiceChat
python -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
```

Default backend URL for the iOS app is `http://192.168.0.11:30190`, so a phone on the same LAN can call this machine directly. Change it in the app settings when using the public HTTPS endpoint or the Tailscale fallback.

## Backend profiles

Use `scripts/switch_backend_profile.ps1` to switch backend targets without committing secrets:

```powershell
# Local machine backend calls LAN services only:
#   LLM http://192.168.0.11:50553/v1
#   TTS http://192.168.0.13:39040
#   STT http://192.168.0.13:39050
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile local-lan

# Local simulator loopback for the LLM, while TTS/STT still use Dell LAN.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile local-loopback

# Local machine backend calls the local OpenAI-compatible proxy and Dell over Tailscale.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile local-tailscale

# 100.64.0.2 backend tries to call this machine and Dell over 192.168 LAN.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile server-to-local-lan

# 100.64.0.2 backend calls this machine and Dell over Tailscale.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile server-to-local-tailscale

# Original deployment profile.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile tailscale
```

`server-to-local` is kept as an alias for `server-to-local-lan`. Use it only when `100.64.0.2` can route to `192.168.0.11` and `192.168.0.13`; otherwise use the local backend directly from the phone with `http://192.168.0.11:30190`. Runtime secrets stay in `backend/.env`.

To safely enter a local API key without putting it in shell history:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile local-lan -PromptApiKey -Restart
```

## iOS build

The project uses XcodeGen.

```bash
cd /path/to/EvoVoiceChat
xcodegen generate
xcodebuild -project EvoVoiceChat.xcodeproj -scheme EvoVoiceChat -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO build
```

## Notes

Do not commit API keys, certificates, private keys, or signing assets. Runtime secrets belong in `backend/.env` or the deployment environment.
