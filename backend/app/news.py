from __future__ import annotations

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

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
    "when",
    "联网",
    "查一下",
    "帮我",
    "一下",
}
CURRENT_QUERY_TERMS = {
    "today",
    "latest",
    "current",
    "recent",
    "breaking",
    "now",
    "新闻",
    "最新",
    "今天",
    "今日",
    "刚刚",
    "最近",
    "实时",
    "快讯",
    "消息",
    "行情",
}
DOC_QUERY_TERMS = {
    "api",
    "docs",
    "doc",
    "documentation",
    "sdk",
    "reference",
    "guide",
    "endpoint",
    "文档",
    "官方",
    "接口",
    "怎么",
    "如何",
    "启用",
    "使用",
    "配置",
    "教程",
}
QUERY_NOISE_RE = re.compile(
    r"(联网查一下|帮我查一下|查一下|搜索一下|搜一下|联网|请问|帮我|今天|今日|最新|新闻|快讯|刚刚|最近|实时|消息)",
    re.IGNORECASE,
)
EN_QUERY_NOISE_RE = re.compile(r"\b(today|latest|current|recent|breaking|news|please|find)\b", re.IGNORECASE)
RECENT_QUERY_RE = re.compile(r"(今天|今日|刚刚|\btoday\b|\bnow\b)", re.IGNORECASE)
TRUSTED_SOURCE_DOMAINS = {
    "news.cn",
    "xinhuanet.com",
    "people.com.cn",
    "cctv.com",
    "cntv.cn",
    "sz.gov.cn",
    "gov.cn",
    "36kr.com",
    "caixin.com",
    "sina.com.cn",
    "sztv.com.cn",
    "thepaper.cn",
    "21jingji.com",
    "yicai.com",
    "wallstreetcn.com",
    "stcn.com",
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "theverge.com",
    "techcrunch.com",
    "developers.openai.com",
    "platform.openai.com",
    "openai.com",
}
LOW_QUALITY_DOMAINS = {
    "toolify.ai",
    "xix.ai",
    "ai-bot.cn",
    "aibase.com",
    "53ai.com",
    "article.9466.com",
    "apidog.com",
    "apifox.com",
}
LOW_QUALITY_TITLE_RE = re.compile(
    r"(每日AI|AI工具|工具导航|一网打尽|小白狂喜|暴打|手搓|镜像|破解版|合集|导航)",
    re.IGNORECASE,
)
LOW_VALUE_BROAD_NEWS_RE = re.compile(
    r"(建设工程规划许可证|总平面图|法律意见书|临时股东会|招标|中标|采购公告|更正公告|年报|季报)",
    re.IGNORECASE,
)
HIGH_QUALITY_PROVIDERS = {"brave", "tavily", "searxng"}
SITEMAP_DOMAIN_LIMIT = 4
SITEMAP_INDEX_LIMIT = 8
SITEMAP_URL_LIMIT = 3000


@dataclass(frozen=True)
class ProviderResult:
    result: SearchResult
    provider: str


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


def _normalise_domain(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).netloc or raw
    raw = raw.split("/", 1)[0].split(":", 1)[0]
    if raw.startswith("www."):
        raw = raw[4:]
    return raw


def _domain_matches(domain: str | None, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    normalised = _normalise_domain(domain or "")
    if not normalised:
        return False
    return any(normalised == allowed or normalised.endswith(f".{allowed}") for allowed in allowed_domains)


def _result_matches_domains(result: SearchResult, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    link_domain = _domain_from_url(result.link)
    if _domain_matches(link_domain, allowed_domains):
        return True
    source = result.source or ""
    for match in re.findall(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)", source.lower()):
        if _domain_matches(match, allowed_domains):
            return True
    return False


def _has_any_term(query: str, terms: set[str]) -> bool:
    lower = query.lower()
    return any(term in lower for term in terms)


def _is_docs_query(query: str) -> bool:
    return _has_any_term(query, DOC_QUERY_TERMS)


def _is_current_query(query: str) -> bool:
    return _has_any_term(query, CURRENT_QUERY_TERMS)


def _use_google_news(query: str) -> bool:
    return _is_current_query(query) and not _is_docs_query(query)


def _strip_query_noise(query: str) -> str:
    cleaned = QUERY_NOISE_RE.sub(" ", query)
    cleaned = EN_QUERY_NOISE_RE.sub(" ", cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned or query.strip()


def _google_news_query(query: str) -> str:
    cleaned = _strip_query_noise(query)
    window = "when:1d" if RECENT_QUERY_RE.search(query) else "when:3d"
    if re.search(r"\bwhen:\d+[hdmy]\b", cleaned, re.IGNORECASE):
        return cleaned
    return f"{cleaned} {window}".strip()


def _google_news_original_query(query: str) -> str:
    window = "when:1d" if RECENT_QUERY_RE.search(query) else "when:3d"
    if re.search(r"\bwhen:\d+[hdmy]\b", query, re.IGNORECASE):
        return query.strip()
    return f"{query.strip()} {window}".strip()


def _providers_for_query(providers: list[str], query: str, has_domains: bool) -> list[str]:
    high_quality = [provider for provider in providers if provider in HIGH_QUALITY_PROVIDERS]
    if high_quality:
        selected = list(high_quality)
        if _use_google_news(query) and "google-news" in providers:
            selected.append("google-news")
        return selected
    if has_domains:
        return ["google-news"] if _use_google_news(query) and "google-news" in providers else []
    if _is_docs_query(query):
        non_news = [provider for provider in providers if provider != "google-news"]
        return non_news or providers
    return providers


async def search_latest_news(
    query: str,
    source_domains: list[str] | None = None,
    max_results: int | None = None,
) -> tuple[list[SearchResult], float]:
    started = time.perf_counter()
    limit = max(1, min(max_results or settings.news_max_results, 12))
    domains = [_normalise_domain(d) for d in (source_domains or []) if _normalise_domain(d)]
    base_query = query.strip()
    provider_limit = min(max(limit * 2, 8), 12)
    tasks = [
        _safe_provider_search(provider, base_query, domains, provider_limit)
        for provider in _providers_for_query(enabled_search_providers(), base_query, bool(domains))
    ]
    if domains:
        tasks.insert(0, _safe_provider_search("sitemap", base_query, domains, provider_limit))
    raw_results: list[ProviderResult] = []
    for provider, provider_results in await asyncio.gather(*tasks):
        raw_results.extend(ProviderResult(result=item, provider=provider) for item in provider_results)
    results = _rank_results(raw_results, base_query, domains, limit)
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


async def _safe_provider_search(
    provider: str,
    query: str,
    domains: list[str],
    limit: int,
) -> tuple[str, list[SearchResult]]:
    try:
        if provider == "sitemap":
            return provider, await _search_sitemaps(query, domains, limit)
        if provider == "google-news" and _use_google_news(query):
            provider_results = await _search_provider(provider, _google_news_query(query), domains, limit)
            if len(provider_results) < limit:
                original_results = await _search_provider(provider, _google_news_original_query(query), domains, limit)
                provider_results = _merge_results(provider_results, original_results, limit)
            return provider, provider_results
        provider_query = query
        provider_results = await _search_provider(provider, provider_query, domains, limit)
        return provider, provider_results
    except Exception:
        return provider, []


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
                if not _result_matches_domains(candidate, domains):
                    continue
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
        if not _result_matches_domains(candidate, domains):
            continue
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
                if not _result_matches_domains(candidate, domains):
                    continue
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
                if not _result_matches_domains(candidate, domains):
                    continue
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
                if not _result_matches_domains(candidate, domains):
                    continue
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
                source_domain = _domain_from_url(source.get("url") if source is not None else "")
                if domains and not _domain_matches(source_domain, domains):
                    continue
                source_label = source_name or source_domain
                if source_name and source_domain and source_domain not in source_name.lower():
                    source_label = f"{source_name} ({source_domain})"
                description = _clean(item.findtext("description"))
                candidate = SearchResult(
                    title=title,
                    link=link,
                    source=source_label,
                    published_at=_published(item.findtext("pubDate")),
                    snippet=description[:300] if description else None,
                )
                if not _is_relevant(query, candidate):
                    continue
                results.append(candidate)
                if len(results) >= limit:
                    break
    return results[:limit]


async def _search_sitemaps(query: str, domains: list[str], limit: int) -> list[SearchResult]:
    if not domains or limit <= 0:
        return []
    candidates: list[tuple[float, str, str]] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=True) as client:
        for domain in domains[:SITEMAP_DOMAIN_LIMIT]:
            try:
                sitemap_urls = await _discover_sitemaps(client, domain)
                urls = await _collect_sitemap_urls(client, sitemap_urls)
            except Exception:
                continue
            for url in urls:
                if url in seen_urls or not _domain_matches(_domain_from_url(url), [domain]):
                    continue
                seen_urls.add(url)
                score = _score_sitemap_url(query, url)
                if score <= 0:
                    continue
                candidates.append((score, domain, url))
    candidates.sort(key=lambda item: item[0], reverse=True)
    results: list[SearchResult] = []
    for _, domain, url in candidates[:limit]:
        results.append(
            SearchResult(
                title=_title_from_url(url),
                link=url,
                source=domain,
                published_at=None,
                snippet=None,
            )
        )
    return results


async def _discover_sitemaps(client: httpx.AsyncClient, domain: str) -> list[str]:
    discovered: list[str] = []
    for scheme in ("https", "http"):
        base_url = f"{scheme}://{domain}/"
        try:
            response = await _get_with_retries(client, urljoin(base_url, "robots.txt"), attempts=1)
        except Exception:
            continue
        for line in response.text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap = line.split(":", 1)[1].strip()
                if sitemap:
                    discovered.append(urljoin(base_url, sitemap))
        if discovered:
            return _dedupe_strings(discovered)[:SITEMAP_INDEX_LIMIT]
    return [f"https://{domain}/sitemap.xml", f"https://{domain}/sitemap-index.xml"]


async def _collect_sitemap_urls(client: httpx.AsyncClient, sitemap_urls: list[str]) -> list[str]:
    collected: list[str] = []
    seen_sitemaps: set[str] = set()
    pending = list(sitemap_urls[:SITEMAP_INDEX_LIMIT])
    while pending and len(collected) < SITEMAP_URL_LIMIT:
        sitemap_url = pending.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            response = await _get_with_retries(client, sitemap_url, attempts=1)
            root = ET.fromstring(response.content)
        except Exception:
            continue
        locs = [_clean(loc.text) for loc in root.iter() if loc.tag.endswith("loc") and loc.text]
        if root.tag.endswith("sitemapindex"):
            for loc in locs:
                if len(seen_sitemaps) + len(pending) >= SITEMAP_INDEX_LIMIT:
                    break
                pending.append(loc)
            continue
        for loc in locs:
            if loc.startswith(("http://", "https://")):
                collected.append(loc)
                if len(collected) >= SITEMAP_URL_LIMIT:
                    break
    return _dedupe_strings(collected)


def _score_sitemap_url(query: str, url: str) -> float:
    terms = _query_terms(_strip_query_noise(query))
    if not terms:
        return 1.0
    parsed = urlparse(url)
    path = unquote(parsed.path).lower()
    slug_text = re.sub(r"[\W_]+", " ", path)
    score = 0.0
    for term in terms:
        if len(term) < 2:
            continue
        if term in path:
            score += 5.0
        elif term in slug_text:
            score += 2.0
    if _is_docs_query(query):
        if any(part in path for part in ("/docs/", "/api/", "/reference/", "/guides/")):
            score += 8.0
        if any(part in path for part in ("web-search", "web_search", "responses")):
            score += 8.0
    if path in {"", "/"}:
        score -= 6.0
    return score


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).strip("/")
    if not path:
        return parsed.netloc
    parts = [part for part in re.split(r"[/_-]+", path) if part]
    title = " ".join(parts[-5:])
    return title[:120] or url


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _rank_results(
    provider_results: list[ProviderResult],
    query: str,
    domains: list[str],
    limit: int,
) -> list[SearchResult]:
    scored: dict[str, tuple[float, SearchResult]] = {}
    for item in provider_results:
        if domains and item.provider != "sitemap" and not _result_matches_domains(item.result, domains):
            continue
        key = _result_key(item.result)
        score = _score_result(item.result, item.provider, query, domains)
        previous = scored.get(key)
        if previous is None or score > previous[0]:
            scored[key] = (score, item.result)
    ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)
    return [item for _, item in ranked[:limit]]


def _result_key(result: SearchResult) -> str:
    link = result.link.rstrip("/").lower()
    if _is_google_news_redirect(link):
        return f"google-news:{result.title.lower()}:{(result.source or '').lower()}"
    return link


def _is_google_news_redirect(url: str) -> bool:
    return "news.google.com/rss/articles/" in url.lower()


def _score_result(result: SearchResult, provider: str, query: str, domains: list[str]) -> float:
    score = 0.0
    if provider == "sitemap":
        score += 20.0
    elif provider in HIGH_QUALITY_PROVIDERS:
        score += 12.0
    elif provider == "google-news":
        score += 10.0 if _use_google_news(query) else -8.0
    elif provider == "bing":
        score += 3.0
    elif provider == "duckduckgo":
        score += 2.0

    if domains and _result_matches_domains(result, domains):
        score += 30.0

    terms = _query_terms(_strip_query_noise(query))
    title = (result.title or "").lower()
    snippet = (result.snippet or "").lower()
    link = result.link.lower()
    source = (result.source or "").lower()
    for term in terms:
        if term in title:
            score += 4.0
        if term in snippet:
            score += 1.5
        if term in link:
            score += 1.0
        if term in source:
            score += 1.0

    domain = _domain_from_url(result.link)
    source_domains = re.findall(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)", source)
    if _domain_matches(domain, list(TRUSTED_SOURCE_DOMAINS)) or any(
        _domain_matches(item, list(TRUSTED_SOURCE_DOMAINS)) for item in source_domains
    ):
        score += 6.0
    if _domain_matches(domain, list(LOW_QUALITY_DOMAINS)) or any(
        _domain_matches(item, list(LOW_QUALITY_DOMAINS)) for item in source_domains
    ):
        score -= 12.0
    if LOW_QUALITY_TITLE_RE.search(result.title or ""):
        score -= 10.0
    if _use_google_news(query) and LOW_VALUE_BROAD_NEWS_RE.search(result.title or ""):
        score -= 18.0

    published = _parse_published_at(result.published_at)
    if published:
        age_days = max((datetime.now(timezone.utc) - published).total_seconds() / 86400.0, 0.0)
        if _is_current_query(query):
            if age_days <= 1:
                score += 24.0
            elif age_days <= 3:
                score += 16.0
            elif age_days <= 7:
                score += 8.0
            elif age_days > 30:
                score -= min(age_days / 7.0, 35.0)
        elif age_days <= 30:
            score += 3.0
    elif _use_google_news(query):
        score -= 2.0

    if _is_docs_query(query) and provider == "google-news":
        score -= 18.0
    return score


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


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
        read_attempts = 0
        for index, item in enumerate(enriched):
            if read_attempts >= top_count:
                break
            if _is_google_news_redirect(item.link):
                continue
            read_attempts += 1
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
    if read_attempts == 0:
        return enriched, 0.0
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
    terms = _query_terms(_strip_query_noise(query))
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
