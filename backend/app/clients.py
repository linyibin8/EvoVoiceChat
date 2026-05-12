from __future__ import annotations

import io
import time
import wave

import httpx

from .config import settings
from .models import ChatMessage, SearchResult


SYSTEM_PROMPT = """你是 Evo Voice，一个自然、可靠、适合语音对话的中文 AI 助手。
回答要像真人聊天一样顺畅，优先给结论，再给必要细节。
如果提供了联网搜索结果，必须基于搜索结果回答最新新闻或事实问题，并在关键句后标注来源编号，例如 [1]。
如果搜索结果不足，要直接说明不确定，不要编造。"""


def _search_context(results: list[SearchResult]) -> str:
    if not results:
        return ""
    lines = ["联网搜索结果："]
    for index, item in enumerate(results, start=1):
        parts = [f"[{index}] {item.title}"]
        if item.source:
            parts.append(f"来源：{item.source}")
        if item.published_at:
            parts.append(f"时间：{item.published_at}")
        parts.append(f"链接：{item.link}")
        if item.snippet:
            parts.append(f"摘要：{item.snippet}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


async def chat_completion(
    messages: list[ChatMessage],
    search_results: list[SearchResult],
) -> tuple[str, float]:
    if not settings.openai_api_key:
        raise RuntimeError("EVOWIT_OPENAI_API_KEY is not configured")

    started = time.perf_counter()
    openai_messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    context = _search_context(search_results)
    if context:
        openai_messages.append({"role": "system", "content": context})
    openai_messages.extend({"role": item.role, "content": item.content} for item in messages)

    payload = {
        "model": settings.openai_model,
        "messages": openai_messages,
        "temperature": 0.6,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
        response = await client.post(f"{settings.openai_base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected chat response shape: {data}") from exc
    return text or "", elapsed_ms


def wav_duration_seconds(audio: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except Exception:
        return None


async def synthesize_speech(text: str, voice: str | None = None) -> tuple[bytes, dict[str, float | str]]:
    started = time.perf_counter()
    payload = {
        "model": settings.tts_model,
        "voice": voice or settings.tts_voice,
        "input": text,
        "response_format": "wav",
    }
    async with httpx.AsyncClient(timeout=settings.tts_timeout_seconds) as client:
        response = await client.post(f"{settings.tts_base_url}/v1/audio/speech", json=payload)
        response.raise_for_status()
        audio = response.content
    latency_ms = (time.perf_counter() - started) * 1000
    duration_s = wav_duration_seconds(audio) or 0.0
    rtf = (latency_ms / 1000.0 / duration_s) if duration_s > 0 else 0.0
    chars_per_second = (len(text) / (latency_ms / 1000.0)) if latency_ms > 0 else 0.0
    metrics: dict[str, float | str] = {
        "latency_ms": round(latency_ms, 1),
        "audio_duration_s": round(duration_s, 3),
        "rtf": round(rtf, 3),
        "chars_per_second": round(chars_per_second, 2),
        "bytes": float(len(audio)),
        "model": settings.tts_model,
    }
    return audio, metrics


async def transcribe_audio(filename: str, content: bytes, content_type: str | None, language: str | None) -> tuple[dict, float]:
    started = time.perf_counter()
    files = {
        "file": (filename or "audio.wav", content, content_type or "audio/wav"),
    }
    data = {"model": settings.stt_model}
    if language:
        data["language"] = language
    async with httpx.AsyncClient(timeout=settings.stt_timeout_seconds) as client:
        response = await client.post(f"{settings.stt_base_url}/v1/audio/transcriptions", data=data, files=files)
        response.raise_for_status()
        payload = response.json()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return payload, elapsed_ms
