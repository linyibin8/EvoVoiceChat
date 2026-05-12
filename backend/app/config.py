from __future__ import annotations

import os
from dataclasses import dataclass


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("EVOVOICE_HOST", "0.0.0.0")
    port: int = _int_env("EVOVOICE_PORT", 30190)

    openai_base_url: str = os.getenv("EVOWIT_OPENAI_BASE_URL", "https://sapi.evowit.com/v1").rstrip("/")
    openai_api_key: str = os.getenv("EVOWIT_OPENAI_API_KEY", "")
    openai_model: str = os.getenv("EVOWIT_OPENAI_MODEL", "gpt-5.5")
    openai_timeout_seconds: float = _float_env("EVOWIT_OPENAI_TIMEOUT_SECONDS", 90)

    tts_base_url: str = os.getenv("DELL_TTS_BASE_URL", "http://100.64.0.5:39040").rstrip("/")
    tts_model: str = os.getenv("DELL_TTS_MODEL", "voxcpm2")
    tts_voice: str = os.getenv("DELL_TTS_VOICE", "default")
    tts_timeout_seconds: float = _float_env("DELL_TTS_TIMEOUT_SECONDS", 60)

    stt_base_url: str = os.getenv("DELL_STT_BASE_URL", "http://100.64.0.5:39050").rstrip("/")
    stt_model: str = os.getenv("DELL_STT_MODEL", "whisper-1")
    stt_timeout_seconds: float = _float_env("DELL_STT_TIMEOUT_SECONDS", 90)

    news_timeout_seconds: float = _float_env("NEWS_SEARCH_TIMEOUT_SECONDS", 12)
    news_max_results: int = _int_env("NEWS_SEARCH_MAX_RESULTS", 6)


settings = Settings()
