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

## Remote-domain backend

This repository is the public-domain/server version of Evo Voice. Keep it separate from
`D:\AI\EvoVoiceChatLocal`, which is the LAN/TestFlight app that talks directly to the
local Windows backend.

For this app:

- iOS talks to the backend through `https://evovoice.evowit.com`.
- The Guangzhou VPS terminates HTTPS and reverse-proxies to `http://100.64.0.2:30190`.
- The Windows backend on `100.64.0.2` can call upstream services by Tailscale/private IP:
  - LLM: `http://100.64.0.3:50553/v1`
  - TTS: `http://100.64.0.5:39040`
  - STT: `http://100.64.0.5:39050`
- Do not point the iOS app at `localhost`, `127.0.0.1`, `192.168.*`, or `100.64.*`.

Copy `backend/.env.example` to `backend/.env` on the backend host and fill secrets locally.

Use the remote profile when preparing this repository:

```powershell
cd D:\AI\EvoVoiceChat
python -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile remote-server
powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
```

Default backend URL for the iOS app is `https://evovoice.evowit.com`.

## Backend profiles

Use `scripts/switch_backend_profile.ps1` to switch backend targets without committing secrets:

```powershell
# Recommended for this app: iOS uses the public domain; backend uses private upstream IPs.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile remote-server

# Alias kept for existing deployments; it now reports the iOS backend URL as the public domain.
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile server-to-local-tailscale
```

The local LAN profiles remain in the script only for emergency diagnostics and for older notes.
Do not use them for this app's iOS build. Runtime secrets stay in `backend/.env`.

To safely enter a local API key without putting it in shell history:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\switch_backend_profile.ps1 -Profile remote-server -PromptApiKey -Restart
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
