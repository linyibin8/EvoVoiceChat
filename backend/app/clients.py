from __future__ import annotations

import io
import ipaddress
import json
import time
import wave
import asyncio
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx

from .config import settings
from .models import ChatMessage, SearchResult


SYSTEM_PROMPT = """你是 Evo Voice，一个自然、可靠、适合语音对话的中文 AI 助手。
回答要像真人聊天一样顺畅，优先给结论，再给必要细节。
如果提供了联网搜索结果，必须基于搜索结果回答最新新闻或事实问题，并在关键句后标注来源编号，例如 [1]。
搜索结果可能包含网页正文摘录；优先使用正文摘录，其次使用标题和摘要。
如果搜索结果不足，要说“这次联网搜索没有找到足够可靠的新结果”，不要说自己没有联网工具，也不要编造。"""

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def _trust_env_for_url(url: str) -> bool:
    host = urlparse(url).hostname
    if not host or host.lower() == "localhost":
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    if address in TAILSCALE_NET:
        return False
    return not (address.is_loopback or address.is_private or address.is_link_local)


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = 3,
    **kwargs,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.post(url, **kwargs)
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < attempts - 1:
                last_error = httpx.HTTPStatusError(
                    f"transient upstream status {response.status_code}",
                    request=response.request,
                    response=response,
                )
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in TRANSIENT_STATUS_CODES or attempt == attempts - 1:
                raise
            last_error = exc
            await asyncio.sleep(0.6 * (attempt + 1))
        except httpx.RequestError as exc:
            if attempt == attempts - 1:
                raise
            last_error = exc
            await asyncio.sleep(0.6 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("upstream request failed without an exception")


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


def _openai_messages(
    messages: list[ChatMessage],
    search_results: list[SearchResult],
) -> list[dict[str, str]]:
    openai_messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    context = _search_context(search_results)
    if context:
        openai_messages.append({"role": "system", "content": context})
    openai_messages.extend({"role": item.role, "content": item.content} for item in messages)
    return openai_messages


async def chat_completion(
    messages: list[ChatMessage],
    search_results: list[SearchResult],
) -> tuple[str, float]:
    if not settings.openai_api_key:
        raise RuntimeError("EVOWIT_OPENAI_API_KEY is not configured")

    started = time.perf_counter()
    payload = {
        "model": settings.openai_model,
        "messages": _openai_messages(messages, search_results),
        "temperature": 0.6,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    endpoint = f"{settings.openai_base_url}/chat/completions"
    async with httpx.AsyncClient(
        timeout=settings.openai_timeout_seconds,
        trust_env=_trust_env_for_url(endpoint),
    ) as client:
        response = await _post_with_retries(
            client,
            endpoint,
            json=payload,
            headers=headers,
        )
        data = response.json()
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected chat response shape: {data}") from exc
    return text or "", elapsed_ms


async def chat_completion_stream(
    messages: list[ChatMessage],
    search_results: list[SearchResult],
) -> AsyncIterator[str]:
    if not settings.openai_api_key:
        raise RuntimeError("EVOWIT_OPENAI_API_KEY is not configured")

    payload = {
        "model": settings.openai_model,
        "messages": _openai_messages(messages, search_results),
        "temperature": 0.6,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    endpoint = f"{settings.openai_base_url}/chat/completions"
    async with httpx.AsyncClient(
        timeout=settings.openai_timeout_seconds,
        trust_env=_trust_env_for_url(endpoint),
    ) as client:
        async with client.stream(
            "POST",
            endpoint,
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = chunk["choices"][0].get("delta", {}).get("content")
                except (KeyError, IndexError, TypeError):
                    delta = None
                if delta:
                    yield delta


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
    if settings.tts_inference_timesteps > 0:
        payload["inference_timesteps"] = settings.tts_inference_timesteps
    endpoint = f"{settings.tts_base_url}/v1/audio/speech"
    async with httpx.AsyncClient(
        timeout=settings.tts_timeout_seconds,
        trust_env=_trust_env_for_url(endpoint),
    ) as client:
        response = await _post_with_retries(client, endpoint, json=payload)
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
    endpoint = f"{settings.stt_base_url}/v1/audio/transcriptions"
    async with httpx.AsyncClient(
        timeout=settings.stt_timeout_seconds,
        trust_env=_trust_env_for_url(endpoint),
    ) as client:
        response = await _post_with_retries(
            client,
            endpoint,
            data=data,
            files=files,
        )
        payload = response.json()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return payload, elapsed_ms
