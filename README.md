# EvoVoiceChat

Native iOS voice chat MVP for a Yuanbao/Doubao-style assistant.

## What is included

- SwiftUI iOS app with text chat, push-to-talk voice mode, live speech recognition, Dell TTS playback, and real-time latency/RTF display.
- FastAPI backend proxy for:
  - OpenAI-compatible chat completions through the evowit endpoint.
  - Dell VoxCPM2 TTS (`/v1/audio/speech`) with speed headers.
  - Dell Whisper STT (`/v1/audio/transcriptions`) for fallback transcription.
  - Latest-news search via Google News RSS fallback, with source-domain filters.

## Local backend

Copy `backend/.env.example` to `backend/.env` and fill secrets locally.

```powershell
cd D:\AI\EvoVoiceChat
python -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
```

Default backend URL for the iOS app is `http://100.64.0.2:30190`. Change it in the app settings for local testing.

## iOS build

The project uses XcodeGen.

```bash
cd /path/to/EvoVoiceChat
xcodegen generate
xcodebuild -project EvoVoiceChat.xcodeproj -scheme EvoVoiceChat -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO build
```

## Notes

Do not commit API keys, certificates, private keys, or signing assets. Runtime secrets belong in `backend/.env` or the deployment environment.
