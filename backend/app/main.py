from __future__ import annotations

import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from httpx import HTTPError

from .clients import chat_completion, synthesize_speech, transcribe_audio
from .config import settings
from .models import ChatRequest, ChatResponse, STTResponse, TTSRequest
from .news import search_latest_news


app = FastAPI(title="EvoVoiceChat API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "chat": {
            "base_url": settings.openai_base_url,
            "model": settings.openai_model,
            "configured": bool(settings.openai_api_key),
        },
        "tts": {
            "base_url": settings.tts_base_url,
            "model": settings.tts_model,
            "voice": settings.tts_voice,
        },
        "stt": {
            "base_url": settings.stt_base_url,
            "model": settings.stt_model,
        },
        "news": {
            "provider": "google-news-rss",
            "max_results": settings.news_max_results,
        },
    }


@app.post("/api/search")
async def search(request: ChatRequest) -> dict:
    query = request.search.query or _last_user_message(request) or ""
    if not query.strip():
        raise HTTPException(status_code=400, detail="Missing search query")
    try:
        results, elapsed_ms = await search_latest_news(
            query=query,
            source_domains=request.search.source_domains,
            max_results=request.search.max_results,
        )
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Search provider failed: {exc}") from exc
    return {"results": results, "timings_ms": {"search": round(elapsed_ms, 1)}}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    total_started = time.perf_counter()
    search_results = []
    timings: dict[str, float] = {}
    if request.search.enabled:
        query = request.search.query or _last_user_message(request)
        if query:
            try:
                search_results, search_ms = await search_latest_news(
                    query=query,
                    source_domains=request.search.source_domains,
                    max_results=request.search.max_results,
                )
                timings["search"] = round(search_ms, 1)
            except HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"Search provider failed: {exc}") from exc

    try:
        text, llm_ms = await chat_completion(request.messages, search_results)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"LLM provider failed: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    timings["llm"] = round(llm_ms, 1)
    timings["total"] = round((time.perf_counter() - total_started) * 1000, 1)
    return ChatResponse(
        assistant_text=text,
        search_results=search_results,
        timings_ms=timings,
        model=settings.openai_model,
    )


@app.post("/api/tts")
async def tts(request: TTSRequest) -> Response:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")
    try:
        audio, metrics = await synthesize_speech(text, request.voice)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"TTS provider failed: {exc}") from exc
    headers = {
        "X-Evo-TTS-Latency-Ms": str(metrics["latency_ms"]),
        "X-Evo-Audio-Duration-S": str(metrics["audio_duration_s"]),
        "X-Evo-TTS-RTF": str(metrics["rtf"]),
        "X-Evo-TTS-Chars-Per-Second": str(metrics["chars_per_second"]),
        "X-Evo-TTS-Bytes": str(int(metrics["bytes"])),
        "X-Evo-TTS-Model": str(metrics["model"]),
    }
    return Response(content=audio, media_type="audio/wav", headers=headers)


@app.post("/api/stt", response_model=STTResponse)
async def stt(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> STTResponse:
    content = await file.read()
    try:
        payload, elapsed_ms = await transcribe_audio(file.filename or "audio.wav", content, file.content_type, language)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"STT provider failed: {exc}") from exc
    return STTResponse(
        text=payload.get("text", ""),
        language=payload.get("language"),
        duration=payload.get("duration"),
        model=settings.stt_model,
        timings_ms={"stt": round(elapsed_ms, 1)},
    )


def _last_user_message(request: ChatRequest) -> str | None:
    for message in reversed(request.messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    return None
