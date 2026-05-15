from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import httpx
from fastapi.responses import Response, StreamingResponse
from httpx import HTTPError

from .clients import chat_completion, chat_completion_stream, synthesize_speech, transcribe_audio
from .config import settings
from .models import ChatRequest, ChatResponse, STTResponse, TTSRequest, WebReadRequest, WebReadResponse, WebSearchRequest
from .news import enrich_results_with_page_text, read_web_page, search_latest_news


app = FastAPI(title="EvoVoiceChat API", version="0.2.4")
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
        "profile": settings.profile,
        "chat": {
            "base_url": settings.openai_base_url,
            "model": settings.openai_model,
            "configured": bool(settings.openai_api_key),
        },
        "tts": {
            "base_url": settings.tts_base_url,
            "model": settings.tts_model,
            "voice": settings.tts_voice,
            "inference_timesteps": settings.tts_inference_timesteps,
            "reference_voice": bool(settings.tts_reference_audio),
        },
        "stt": {
            "base_url": settings.stt_base_url,
            "model": settings.stt_model,
        },
        "news": {
            "provider": "bing-web+google-news-rss",
            "max_results": settings.news_max_results,
            "fetch_top_results": settings.web_fetch_top_results,
        },
    }


@app.post("/api/search")
async def search(request: WebSearchRequest) -> dict:
    query = request.query
    if not query.strip():
        raise HTTPException(status_code=400, detail="Missing search query")
    try:
        results, elapsed_ms = await search_latest_news(
            query=query,
            source_domains=request.source_domains,
            max_results=request.max_results,
        )
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Search provider failed: {exc}") from exc
    return {"results": results, "timings_ms": {"search": round(elapsed_ms, 1)}}


@app.post("/api/tools/read", response_model=WebReadResponse)
async def read_tool(request: WebReadRequest) -> WebReadResponse:
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=settings.web_fetch_timeout_seconds, follow_redirects=True) as client:
        try:
            title, text = await read_web_page(client, request.url, request.max_chars)
        except HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Read provider failed: {exc}") from exc
    return WebReadResponse(
        url=request.url,
        title=title,
        text=text,
        timings_ms={"read": round((time.perf_counter() - started) * 1000, 1)},
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    total_started = time.perf_counter()
    search_results = []
    timings: dict[str, float] = {}
    warnings: list[str] = []
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
                if search_results:
                    search_results, read_ms = await enrich_results_with_page_text(search_results)
                    if read_ms:
                        timings["read"] = round(read_ms, 1)
            except HTTPError as exc:
                timings["search_failed"] = 1.0
                warnings.append(f"联网搜索暂时失败，已先用模型知识回答：{exc}")

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
        warnings=warnings,
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    async def prepare_search() -> tuple[list, dict[str, float], list[str]]:
        search_results = []
        timings: dict[str, float] = {}
        warnings: list[str] = []

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
                    if search_results:
                        search_results, read_ms = await enrich_results_with_page_text(search_results)
                        if read_ms:
                            timings["read"] = round(read_ms, 1)
                except HTTPError as exc:
                    timings["search_failed"] = 1.0
                    warnings.append(f"联网搜索暂时失败，已先用模型知识回答：{exc}")

        return search_results, timings, warnings

    async def events():
        total_started = time.perf_counter()
        yield _sse("ping", {"message": "start"})

        search_task = asyncio.create_task(prepare_search())
        try:
            while True:
                try:
                    search_results, timings, warnings = await asyncio.wait_for(asyncio.shield(search_task), timeout=3)
                    break
                except asyncio.TimeoutError:
                    yield _sse("ping", {"message": "search"})
        finally:
            if not search_task.done():
                search_task.cancel()

        yield _sse("ping", {"message": "llm"})

        yield _sse(
            "metadata",
            {
                "search_results": [_model_dump(item) for item in search_results],
                "timings_ms": timings,
                "model": settings.openai_model,
                "warnings": warnings,
            },
        )

        llm_started = time.perf_counter()
        llm_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

        async def pump_llm_stream() -> None:
            try:
                async for delta in chat_completion_stream(request.messages, search_results):
                    await llm_queue.put(("delta", delta))
            except HTTPError as exc:
                await llm_queue.put(("error", f"LLM provider failed: {exc}"))
            except RuntimeError as exc:
                await llm_queue.put(("error", str(exc)))
            except Exception as exc:
                await llm_queue.put(("error", f"LLM provider failed: {exc}"))
            finally:
                await llm_queue.put(("done", None))

        llm_task = asyncio.create_task(pump_llm_stream())
        try:
            while True:
                try:
                    kind, value = await asyncio.wait_for(llm_queue.get(), timeout=3)
                except asyncio.TimeoutError:
                    yield _sse("ping", {"message": "llm"})
                    continue

                if kind == "delta" and value:
                    yield _sse("delta", {"text": value})
                elif kind == "error":
                    yield _sse("error", {"message": value or "流式回答失败"})
                    return
                elif kind == "done":
                    break
        finally:
            if not llm_task.done():
                llm_task.cancel()

        timings["llm"] = round((time.perf_counter() - llm_started) * 1000, 1)
        timings["total"] = round((time.perf_counter() - total_started) * 1000, 1)
        yield _sse("done", {"timings_ms": timings, "model": settings.openai_model})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


def _sse(event: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _model_dump(item) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item.dict()
