from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

from .config import settings
from .models import SearchResult


TAG_RE = re.compile(r"<[^>]+>")
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "latest",
    "news",
    "today",
    "about",
    "最新",
    "新闻",
    "今天",
    "关于",
}


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(TAG_RE.sub("", value)).strip()


def _published(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def _domain_from_source(source: str | None) -> str | None:
    cleaned = _clean(source)
    return cleaned or None


async def search_latest_news(
    query: str,
    source_domains: list[str] | None = None,
    max_results: int | None = None,
) -> tuple[list[SearchResult], float]:
    started = time.perf_counter()
    limit = max(1, min(max_results or settings.news_max_results, 12))
    domains = [d.strip().lower() for d in (source_domains or []) if d.strip()]
    base_query = query.strip()
    queries = [base_query]
    if domains:
        queries = [f"{base_query} site:{domain}" for domain in domains]
        queries.append(base_query)

    results: list[SearchResult] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.news_timeout_seconds, follow_redirects=True) as client:
        for item_query in queries:
            if len(results) >= limit:
                break
            rss_url = (
                "https://news.google.com/rss/search?"
                f"q={quote_plus(item_query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            )
            response = await client.get(rss_url, headers={"User-Agent": "EvoVoiceChat/0.1"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall("./channel/item"):
                title = _clean(item.findtext("title"))
                link = _clean(item.findtext("link"))
                if not title or not link or link in seen:
                    continue
                seen.add(link)
                source = item.find("source")
                source_name = _domain_from_source(source.text if source is not None else None)
                description = _clean(item.findtext("description"))
                candidate = SearchResult(
                    title=title,
                    link=link,
                    source=source_name,
                    published_at=_published(item.findtext("pubDate")),
                    snippet=description[:300] if description else None,
                )
                if not _is_relevant(base_query, candidate):
                    continue
                results.append(candidate)
                if len(results) >= limit:
                    break
    elapsed_ms = (time.perf_counter() - started) * 1000
    return results, elapsed_ms


def _query_terms(query: str) -> list[str]:
    lower = query.lower()
    terms = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", lower)
    terms.extend(re.findall(r"[\u4e00-\u9fff]{2,}", lower))
    return [term for term in terms if term not in STOPWORDS]


def _is_relevant(query: str, result: SearchResult) -> bool:
    terms = _query_terms(query)
    if not terms:
        return True
    haystack = " ".join(
        value or ""
        for value in [result.title, result.snippet, result.source]
    ).lower()
    matches = sum(1 for term in terms if term in haystack)
    if len(terms) == 1:
        return matches >= 1
    return matches >= min(2, len(terms))
