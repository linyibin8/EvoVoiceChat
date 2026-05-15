from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import settings
from .models import SearchResult


TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 EvoVoiceChat/0.2"
)
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


def _domain_from_url(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def _normalize_domain(value: str) -> str:
    domain = value.strip().lower()
    if "://" in domain:
        domain = urlparse(domain).netloc.lower()
    domain = domain.strip("/")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _domain_matches(candidate: str | None, allowed_domains: list[str]) -> bool:
    if not candidate:
        return False
    normalized = _normalize_domain(candidate)
    return any(normalized == domain or normalized.endswith(f".{domain}") for domain in allowed_domains)


def _result_matches_domains(result: SearchResult, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    return _domain_matches(_domain_from_url(result.link), allowed_domains) or _domain_matches(result.source, allowed_domains)


async def search_latest_news(
    query: str,
    source_domains: list[str] | None = None,
    max_results: int | None = None,
) -> tuple[list[SearchResult], float]:
    started = time.perf_counter()
    limit = max(1, min(max_results or settings.news_max_results, 12))
    domains = [_normalize_domain(d) for d in (source_domains or []) if d.strip()]
    base_query = query.strip()
    results: list[SearchResult] = []
    for provider in enabled_search_providers():
        if len(results) >= limit:
            break
        try:
            provider_results = await _search_provider(provider, base_query, domains, limit - len(results))
            if domains:
                provider_results = [item for item in provider_results if _result_matches_domains(item, domains)]
            results = _merge_results(results, provider_results, limit)
        except httpx.HTTPError:
            continue
    elapsed_ms = (time.perf_counter() - started) * 1000
    return results, elapsed_ms


def enabled_search_providers() -> list[str]:
    configured: list[str] = []
    seen: set[str] = set()
    for raw in settings.web_search_provider_order.split(","):
        provider = raw.strip().lower().replace("_", "-")
        if not provider or provider in seen:
            continue
        if provider == "brave" and not settings.brave_search_api_key:
            continue
        if provider == "tavily" and not settings.tavily_api_key:
            continue
        if provider == "searxng" and not settings.searxng_base_url:
            continue
        if provider not in {"brave", "tavily", "searxng", "duckduckgo", "bing", "google-news"}:
            continue
        configured.append(provider)
        seen.add(provider)
    return configured or ["duckduckgo", "bing", "google-news"]


async def _search_provider(provider: str, query: str, domains: list[str], limit: int) -> list[SearchResult]:
    if limit <= 0:
        return []
    if provider == "brave":
        return await _search_brave(query, domains, limit)
    if provider == "tavily":
        return await _search_tavily(query, domains, limit)
    if provider == "searxng":
        return await _search_searxng(query, domains, limit)
    if provider == "duckduckgo":
        return await _search_duckduckgo(query, domains, limit)
    if provider == "bing":
        return await _search_bing(query, domains, limit)
    if provider == "google-news":
        return await _search_google_news(query, domains, limit)
    return []


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = 3,
    **kwargs,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, **kwargs)
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < attempts - 1:
                last_error = httpx.HTTPStatusError(
                    f"transient upstream status {response.status_code}",
                    request=response.request,
                    response=response,
                )
                await _sleep_retry(attempt)
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in TRANSIENT_STATUS_CODES or attempt == attempts - 1:
                raise
            last_error = exc
            await _sleep_retry(attempt)
        except httpx.RequestError as exc:
            if attempt == attempts - 1:
                raise
            last_error = exc
            await _sleep_retry(attempt)
    if last_error:
        raise last_error
    raise RuntimeError("upstream GET failed without an exception")


async def _sleep_retry(attempt: int) -> None:
    import asyncio

    await asyncio.sleep(0.5 * (attempt + 1))


def _domain_queries(query: str, domains: list[str]) -> list[str]:
    return [query] if not domains else [f"{query} site:{domain}" for domain in domains]


async def _search_brave(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    queries = _domain_queries(query, domains)
    results: list[SearchResult] = []
    seen: set[str] = set()
    headers = {
        "Accept": "application/json",
        "User-Agent": SEARCH_USER_AGENT,
        "X-Subscription-Token": settings.brave_search_api_key,
    }
    async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=True) as client:
        for item_query in queries:
            if len(results) >= limit:
                break
            response = await _get_with_retries(
                client,
                f"{settings.brave_search_base_url}/search",
                params={"q": item_query, "count": min(limit, 20), "search_lang": "zh-hans"},
                headers=headers,
            )
            data = response.json()
            for item in data.get("web", {}).get("results", []):
                href = str(item.get("url") or "").strip()
                title = _clean(str(item.get("title") or ""))
                if not href or href in seen or not title:
                    continue
                candidate = SearchResult(
                    title=title,
                    link=href,
                    source=_domain_from_url(href) or item.get("profile", {}).get("name"),
                    published_at=item.get("age"),
                    snippet=_clean(str(item.get("description") or ""))[:500] or None,
                )
                if not _is_relevant(query, candidate):
                    continue
                seen.add(href)
                results.append(candidate)
                if len(results) >= limit:
                    break
    return results[:limit]


async def _search_tavily(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    payload: dict[str, object] = {
        "query": query,
        "search_depth": "basic",
        "max_results": min(limit, 10),
        "include_answer": False,
        "include_raw_content": True,
    }
    if domains:
        payload["include_domains"] = domains
    headers = {
        "Authorization": f"Bearer {settings.tavily_api_key}",
        "Content-Type": "application/json",
        "User-Agent": SEARCH_USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=True) as client:
        response = await client.post(
            f"{settings.tavily_search_base_url}/search",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in data.get("results", []):
        href = str(item.get("url") or "").strip()
        title = _clean(str(item.get("title") or ""))
        if not href or href in seen or not title:
            continue
        content = _clean(str(item.get("content") or ""))
        raw_content = _clean(str(item.get("raw_content") or ""))
        snippet = raw_content or content
        candidate = SearchResult(
            title=title,
            link=href,
            source=_domain_from_url(href),
            published_at=item.get("published_date"),
            snippet=snippet[: settings.web_fetch_max_chars] if snippet else None,
        )
        if not _is_relevant(query, candidate):
            continue
        seen.add(href)
        results.append(candidate)
        if len(results) >= limit:
            break
    return results


async def _search_searxng(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    queries = _domain_queries(query, domains)
    results: list[SearchResult] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=True) as client:
        for item_query in queries:
            if len(results) >= limit:
                break
            response = await _get_with_retries(
                client,
                f"{settings.searxng_base_url}/search",
                params={
                    "q": item_query,
                    "format": "json",
                    "language": "zh-CN",
                    "safesearch": 0,
                },
                headers={"User-Agent": SEARCH_USER_AGENT},
            )
            data = response.json()
            for item in data.get("results", []):
                href = str(item.get("url") or "").strip()
                title = _clean(str(item.get("title") or ""))
                if not href or href in seen or not title:
                    continue
                candidate = SearchResult(
                    title=title,
                    link=href,
                    source=_domain_from_url(href) or item.get("engine"),
                    published_at=item.get("publishedDate"),
                    snippet=_clean(str(item.get("content") or ""))[:500] or None,
                )
                if not _is_relevant(query, candidate):
                    continue
                seen.add(href)
                results.append(candidate)
                if len(results) >= limit:
                    break
    return results[:limit]


async def _search_duckduckgo(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    queries = _domain_queries(query, domains)
    results: list[SearchResult] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=True) as client:
        for item_query in queries:
            if len(results) >= limit:
                break
            response = await _get_with_retries(
                client,
                "https://duckduckgo.com/html/",
                params={"q": item_query, "kl": "cn-zh"},
                headers={"User-Agent": SEARCH_USER_AGENT},
            )
            soup = BeautifulSoup(response.text, "html.parser")
            for item in soup.select(".result"):
                link = item.select_one("a.result__a")
                if not link:
                    continue
                href = _duckduckgo_result_url(str(link.get("href") or ""))
                if not href.startswith(("http://", "https://")) or href in seen:
                    continue
                title = link.get_text(" ", strip=True)
                snippet_node = item.select_one(".result__snippet")
                snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
                candidate = SearchResult(
                    title=title,
                    link=href,
                    source=_domain_from_url(href),
                    published_at=None,
                    snippet=snippet[:500] if snippet else None,
                )
                if not title or not _is_relevant(query, candidate):
                    continue
                seen.add(href)
                results.append(candidate)
                if len(results) >= limit:
                    break
    return results[:limit]


def _duckduckgo_result_url(href: str) -> str:
    if href.startswith("//duckduckgo.com/l/?") or href.startswith("/l/?"):
        parsed = urlparse(href)
        encoded = parse_qs(parsed.query).get("uddg", [href])[0]
        return unquote(encoded)
    return href


async def _search_bing(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    queries = _domain_queries(query, domains)
    results: list[SearchResult] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=True) as client:
        for item_query in queries:
            if len(results) >= limit:
                break
            url = (
                f"{settings.bing_search_base_url}/search?"
                f"q={quote_plus(item_query)}&mkt=zh-CN&setlang=zh-Hans"
            )
            response = await _get_with_retries(client, url, headers={"User-Agent": SEARCH_USER_AGENT})
            soup = BeautifulSoup(response.text, "html.parser")
            for item in soup.select("li.b_algo"):
                link = item.find("a", href=True)
                if not link:
                    continue
                href = str(link["href"]).strip()
                if not href.startswith(("http://", "https://")) or href in seen:
                    continue
                title = link.get_text(" ", strip=True)
                snippet_node = item.select_one(".b_caption p") or item.find("p")
                snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
                candidate = SearchResult(
                    title=title,
                    link=href,
                    source=_domain_from_url(href),
                    published_at=None,
                    snippet=snippet[:300] if snippet else None,
                )
                if not title or not _is_relevant(query, candidate):
                    continue
                seen.add(href)
                results.append(candidate)
                if len(results) >= limit:
                    break
    return results[:limit]


async def _search_google_news(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    queries = _domain_queries(query, domains)
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
            response = await _get_with_retries(client, rss_url, headers={"User-Agent": SEARCH_USER_AGENT})
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
                if not _is_relevant(query, candidate):
                    continue
                results.append(candidate)
                if len(results) >= limit:
                    break
    return results[:limit]


def _merge_results(primary: list[SearchResult], secondary: list[SearchResult], limit: int) -> list[SearchResult]:
    merged: list[SearchResult] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        if item.link in seen:
            continue
        seen.add(item.link)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


async def enrich_results_with_page_text(results: list[SearchResult]) -> tuple[list[SearchResult], float]:
    started = time.perf_counter()
    if settings.web_fetch_top_results <= 0 or not results:
        return results, 0.0
    enriched = list(results)
    top_count = min(settings.web_fetch_top_results, len(enriched))
    async with httpx.AsyncClient(timeout=settings.web_fetch_timeout_seconds, follow_redirects=True) as client:
        for index in range(top_count):
            item = enriched[index]
            try:
                title, text = await read_web_page(client, item.link, settings.web_fetch_max_chars)
            except Exception:
                continue
            snippet_parts = [part for part in [item.snippet, text] if part]
            enriched[index] = SearchResult(
                title=item.title or title or item.link,
                link=item.link,
                source=item.source,
                published_at=item.published_at,
                snippet="\n".join(snippet_parts)[: settings.web_fetch_max_chars],
            )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return enriched, elapsed_ms


async def read_web_page(client: httpx.AsyncClient, url: str, max_chars: int | None = None) -> tuple[str | None, str]:
    max_length = max_chars or settings.web_fetch_max_chars
    try:
        title, text = await _read_web_page_direct(client, url, max_length)
    except Exception:
        if not settings.web_read_jina_fallback:
            raise
        return await _read_web_page_jina(client, url, max_length)
    if settings.web_read_jina_fallback and len(text) < settings.web_read_jina_min_chars:
        try:
            jina_title, jina_text = await _read_web_page_jina(client, url, max_length)
            if len(jina_text) > len(text):
                return jina_title or title, jina_text
        except Exception:
            pass
    return title, text


async def _read_web_page_direct(client: httpx.AsyncClient, url: str, max_chars: int) -> tuple[str | None, str]:
    response = await _get_with_retries(
        client,
        url,
        headers={
            "User-Agent": SEARCH_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        return None, ""
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = WHITESPACE_RE.sub(" ", main.get_text(" ", strip=True)).strip()
    return title, text[:max_chars]


async def _read_web_page_jina(client: httpx.AsyncClient, url: str, max_chars: int) -> tuple[str | None, str]:
    response = await _get_with_retries(
        client,
        f"https://r.jina.ai/http://{url}",
        headers={
            "User-Agent": SEARCH_USER_AGENT,
            "Accept": "text/plain, text/markdown, */*",
        },
    )
    text = response.text.strip()
    title = None
    for line in text.splitlines()[:8]:
        if line.startswith("Title:"):
            title = line.removeprefix("Title:").strip()
            break
    return title, WHITESPACE_RE.sub(" ", text).strip()[:max_chars]


def _query_terms(query: str) -> list[str]:
    lower = query.lower()
    terms = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", lower)
    for block in re.findall(r"[\u4e00-\u9fff]{2,}", lower):
        terms.append(block)
        for size in (2, 3):
            for index in range(0, max(len(block) - size + 1, 0)):
                terms.append(block[index : index + size])
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in STOPWORDS or term in seen:
            continue
        seen.add(term)
        cleaned.append(term)
    return cleaned


def _is_relevant(query: str, result: SearchResult) -> bool:
    terms = _query_terms(query)
    if not terms:
        return True
    haystack = " ".join(
        value or ""
        for value in [result.title, result.snippet, result.source]
    ).lower()
    matches = sum(1 for term in terms if term in haystack)
    if any(CJK_RE.search(term) for term in terms):
        return matches >= 1
    if len(terms) == 1:
        return matches >= 1
    return matches >= min(2, len(terms))
