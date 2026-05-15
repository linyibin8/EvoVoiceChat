from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def now_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 180) -> tuple[dict[str, Any], float]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    elapsed = now_ms(started)
    return json.loads(body.decode("utf-8")), elapsed


def post_bytes(url: str, payload: dict[str, Any], timeout: float = 120) -> tuple[bytes, dict[str, str], float]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        headers = dict(response.headers.items())
    return body, headers, now_ms(started)


def header_value(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return "0"


def make_chat_payload(prompt: str) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": prompt}],
        "search": {
            "enabled": False,
            "query": prompt,
            "source_domains": [],
            "max_results": 1,
        },
    }


def stream_chat(base_url: str, prompt: str) -> dict[str, Any]:
    data = json.dumps(make_chat_payload(prompt), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/chat/stream",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.perf_counter()
    last_line_at = started
    max_silent_ms = 0.0
    first_event_ms = None
    first_delta_ms = None
    ping_count = 0
    delta_chars = 0
    done_timings: dict[str, Any] = {}
    event_name = "message"
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal first_event_ms, first_delta_ms, ping_count, delta_chars, done_timings, event_name, data_lines
        if not data_lines:
            event_name = "message"
            return
        if first_event_ms is None:
            first_event_ms = now_ms(started)
        payload = json.loads("\n".join(data_lines))
        if event_name == "ping":
            ping_count += 1
        elif event_name == "delta":
            text = payload.get("text") or ""
            delta_chars += len(text)
            if text and first_delta_ms is None:
                first_delta_ms = now_ms(started)
        elif event_name == "done":
            done_timings = payload.get("timings_ms") or {}
        elif event_name == "error":
            raise RuntimeError(payload.get("message") or "stream error")
        event_name = "message"
        data_lines = []

    with urllib.request.urlopen(request, timeout=180) as response:
        while True:
            raw_line = response.readline()
            if not raw_line:
                break
            current = time.perf_counter()
            max_silent_ms = max(max_silent_ms, (current - last_line_at) * 1000)
            last_line_at = current
            line = raw_line.decode("utf-8").strip()
            if line.startswith("event:"):
                flush()
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
            elif not line:
                flush()
    flush()

    return {
        "wall_ms": now_ms(started),
        "first_event_ms": first_event_ms,
        "first_delta_ms": first_delta_ms,
        "max_silent_ms": round(max_silent_ms, 1),
        "ping_count": ping_count,
        "delta_chars": delta_chars,
        "backend_timings_ms": done_timings,
    }


def run(base_url: str, prompt: str, tts_text: str) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    health, health_ms = request_json("GET", f"{base_url}/health", timeout=10)
    chat, chat_wall_ms = request_json("POST", f"{base_url}/api/chat", make_chat_payload(prompt), timeout=180)
    stream = stream_chat(base_url, prompt)
    audio, headers, tts_wall_ms = post_bytes(f"{base_url}/api/tts", {"text": tts_text, "voice": "default"})
    return {
        "base_url": base_url,
        "health": {
            "wall_ms": health_ms,
            "profile": health.get("profile"),
            "chat_base_url": (health.get("chat") or {}).get("base_url"),
            "tts_base_url": (health.get("tts") or {}).get("base_url"),
            "stt_base_url": (health.get("stt") or {}).get("base_url"),
        },
        "chat_no_search": {
            "wall_ms": chat_wall_ms,
            "backend_timings_ms": chat.get("timings_ms"),
            "reply_chars": len(chat.get("assistant_text") or ""),
        },
        "stream_no_search": stream,
        "tts": {
            "wall_ms": tts_wall_ms,
            "provider_latency_ms": float(header_value(headers, "X-Evo-TTS-Latency-Ms") or 0),
            "audio_duration_s": float(header_value(headers, "X-Evo-Audio-Duration-S") or 0),
            "rtf": float(header_value(headers, "X-Evo-TTS-RTF") or 0),
            "bytes": len(audio),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure EvoVoiceChat local backend latency.")
    parser.add_argument("--base-url", default="http://127.0.0.1:30190")
    parser.add_argument("--prompt", default="只回复 OK，用最短中文回答。")
    parser.add_argument("--tts-text", default="本地网络语音合成速度测试。")
    args = parser.parse_args()

    try:
        result = run(args.base_url, args.prompt, args.tts_text)
    except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
        print(f"latency check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
