from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class SearchOptions(BaseModel):
    enabled: bool = True
    query: str | None = None
    source_domains: list[str] = Field(default_factory=list)
    max_results: int = 6


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    search: SearchOptions = Field(default_factory=SearchOptions)


class SearchResult(BaseModel):
    title: str
    link: str
    source: str | None = None
    published_at: str | None = None
    snippet: str | None = None


class ChatResponse(BaseModel):
    assistant_text: str
    search_results: list[SearchResult] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    model: str
    warnings: list[str] = Field(default_factory=list)


class WebSearchRequest(BaseModel):
    query: str
    source_domains: list[str] = Field(default_factory=list)
    max_results: int = 6


class WebReadRequest(BaseModel):
    url: str
    max_chars: int = 4000


class WebReadResponse(BaseModel):
    url: str
    title: str | None = None
    text: str
    timings_ms: dict[str, float] = Field(default_factory=dict)


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None


class STTResponse(BaseModel):
    text: str
    language: str | None = None
    duration: float | None = None
    provider: str = "dell-whisper"
    model: str
    timings_ms: dict[str, float] = Field(default_factory=dict)
