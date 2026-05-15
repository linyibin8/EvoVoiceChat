from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_TTS_REFERENCE_AUDIO = "/home/dell/tts-stack/voxcpm2-openai/assets/evo_voice_ref.wav"
DEFAULT_TTS_PROMPT_TEXT = "你好，我是 Evo Voice。接下来我会用自然、清楚、稳定的中文声音和你对话。"


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
    profile: str = os.getenv("EVOVOICE_PROFILE", "custom")
    host: str = os.getenv("EVOVOICE_HOST", "0.0.0.0")
    port: int = _int_env("EVOVOICE_PORT", 30190)

    openai_base_url: str = os.getenv("EVOWIT_OPENAI_BASE_URL", "http://192.168.0.11:50553/v1").rstrip("/")
    openai_api_key: str = os.getenv("EVOWIT_OPENAI_API_KEY", "")
    openai_model: str = os.getenv("EVOWIT_OPENAI_MODEL", "gpt-5.5")
    openai_timeout_seconds: float = _float_env("EVOWIT_OPENAI_TIMEOUT_SECONDS", 90)

    tts_base_url: str = os.getenv("DELL_TTS_BASE_URL", "http://192.168.0.13:39040").rstrip("/")
    tts_model: str = os.getenv("DELL_TTS_MODEL", "voxcpm2")
    tts_voice: str = os.getenv("DELL_TTS_VOICE", "default")
    tts_timeout_seconds: float = _float_env("DELL_TTS_TIMEOUT_SECONDS", 60)
    tts_inference_timesteps: int = _int_env("DELL_TTS_INFERENCE_TIMESTEPS", 6)
    tts_reference_audio: str = os.getenv("DELL_TTS_REFERENCE_AUDIO", DEFAULT_TTS_REFERENCE_AUDIO).strip()
    tts_prompt_audio: str = os.getenv(
        "DELL_TTS_PROMPT_AUDIO",
        os.getenv("DELL_TTS_REFERENCE_AUDIO", DEFAULT_TTS_REFERENCE_AUDIO),
    ).strip()
    tts_prompt_text: str = os.getenv("DELL_TTS_PROMPT_TEXT", DEFAULT_TTS_PROMPT_TEXT).strip()
    tts_cfg_value: float = _float_env("DELL_TTS_CFG_VALUE", 2.0)

    stt_base_url: str = os.getenv("DELL_STT_BASE_URL", "http://192.168.0.13:39050").rstrip("/")
    stt_model: str = os.getenv("DELL_STT_MODEL", "whisper-1")
    stt_timeout_seconds: float = _float_env("DELL_STT_TIMEOUT_SECONDS", 90)

    news_timeout_seconds: float = _float_env("NEWS_SEARCH_TIMEOUT_SECONDS", 12)
    news_max_results: int = _int_env("NEWS_SEARCH_MAX_RESULTS", 6)

    bing_search_base_url: str = os.getenv("BING_SEARCH_BASE_URL", "https://www.bing.com").rstrip("/")
    web_search_timeout_seconds: float = _float_env("WEB_SEARCH_TIMEOUT_SECONDS", 12)
    web_fetch_timeout_seconds: float = _float_env("WEB_FETCH_TIMEOUT_SECONDS", 12)
    web_fetch_top_results: int = _int_env("WEB_FETCH_TOP_RESULTS", 1)
    web_fetch_max_chars: int = _int_env("WEB_FETCH_MAX_CHARS", 1200)


settings = Settings()
