"""多引擎检索：必应(中国站) / 360 / 搜狗 / 中文维基 / OpenAlex 学术库。

每个引擎都是"能通就返回结果，失败就返回空"，绝不因为一个源挂掉影响整体。
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Iterable
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from . import config

_HEADERS = {
    "User-Agent": config.UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bing.com/",
}


def _client(timeout: float = 12.0) -> httpx.AsyncClient:
    kwargs: dict = {
        "timeout": httpx.Timeout(timeout, connect=8.0),
        "follow_redirects": True,
        "headers": _HEADERS,
    }
    if config.PROXY:
        kwargs["proxy"] = config.PROXY
    return httpx.AsyncClient(**kwargs)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _result(title: str, url: str, snippet: str, engine: str, extra: dict | None = None) -> dict:
    item = {
        "title": _clean(title)[:180],
        "url": url.strip(),
        "snippet": _clean(snippet)[:500],
        "engine": engine,
    }
    if extra:
        item.update(extra)
    return item


# ---------------------------------------------------------------- 必应（主引擎）
def _decode_bing_link(href: str) -> str:
    """必应结果有时包一层 /ck/a?...&u=a1<base64>，这里还原真实地址。"""
    if not href:
        return ""
    if "/ck/a" not in href:
        return href
    try:
        qs = parse_qs(urlparse(href).query)
        u = (qs.get("u") or [""])[0]
        if u.startswith("a1"):
            body = u[2:]
            body += "=" * (-len(body) % 4)
            return base64.urlsafe_b64decode(body).decode("utf-8", "ignore")
    except Exception:
        pass
    return href


async def bing(query: str, limit: int = 10) -> list[dict]:
    url = "https://cn.bing.com/search"
    params = {"q": query, "count": "20", "setlang": "zh-Hans"}
    async with _client() as c:
        r = await c.get(url, params=params)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a") or li.select_one("a[href]")
            if not a:
                continue
            href = _decode_bing_link(a.get("href", ""))
            if not href.startswith("http"):
                continue
            if "bing.com" in urlparse(href).netloc and "/ck/" in href:
                continue
            snip_el = li.select_one(".b_caption p") or li.select_one("p") or li.select_one(".b_snippet")
            out.append(_result(a.get_text(" ", strip=True), href,
                               snip_el.get_text(" ", strip=True) if snip_el else "", "bing"))
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------- 360 搜索
async def so360(query: str, limit: int = 8) -> list[dict]:
    async with _client() as c:
        r = await c.get("https://www.so.com/s", params={"q": query})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        for li in soup.select("li.res-list"):
            a = li.select_one("h3 a[data-mdurl]") or li.select_one("h3 a")
            if not a:
                continue
            href = a.get("data-mdurl") or a.get("href", "")
            if href.startswith("https://www.so.com/link") or href.startswith("/link"):
                continue
            if not href.startswith("http"):
                continue
            snip = ""
            el = li.select_one(".res-list-summary") or li.select_one(".res-desc") or li.select_one("p")
            if el:
                snip = el.get_text(" ", strip=True)
            out.append(_result(a.get_text(" ", strip=True), href, snip, "360"))
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------- 搜狗
async def sogou(query: str, limit: int = 6) -> list[dict]:
    async with _client() as c:
        r = await c.get("https://www.sogou.com/web", params={"query": query})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        pending: list[tuple[str, str, str]] = []
        for h3 in soup.select("h3"):
            a = h3.select_one("a[href]")
            if not a:
                continue
            title = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if not title:
                continue
            if href.startswith("http") and "sogou.com" not in urlparse(href).netloc:
                snip = ""
                nxt = h3.find_next(class_=re.compile("space-txt|fz-mid|str-text|summary"))
                if nxt:
                    snip = nxt.get_text(" ", strip=True)
                out.append(_result(title, href, snip, "sogou"))
            elif href.startswith("/link"):
                pending.append((title, href, ""))
            if len(out) >= limit:
                break

        # 搜狗的外链是跳转地址，需要跟一次重定向拿真实 URL（并行、有超时）
        async def resolve(title: str, href: str) -> dict | None:
            try:
                rr = await c.get("https://www.sogou.com" + href, timeout=6.0)
                final = str(rr.url)
                if "sogou.com" in urlparse(final).netloc:
                    m = re.search(r'src="(https?://[^"]+)"', rr.text[:4000])
                    final = m.group(1) if m else ""
                if final and "sogou.com" not in urlparse(final).netloc:
                    return _result(title, final, "", "sogou")
            except Exception:
                return None
            return None

        resolved = await asyncio.gather(*[resolve(t, h) for t, h, _ in pending[: max(0, limit - len(out))]])
        out.extend([x for x in resolved if x])
        return out[:limit]


# ---------------------------------------------------------------- 中文维基百科
async def wiki_zh(query: str, limit: int = 3) -> list[dict]:
    async with _client(10.0) as c:
        s = await c.get("https://zh.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": str(limit), "utf8": "1",
        })
        if s.status_code != 200:
            return []
        hits = (s.json().get("query", {}) or {}).get("search", []) or []
        titles = [h["title"] for h in hits][:limit]
        if not titles:
            return []
        e = await c.get("https://zh.wikipedia.org/w/api.php", params={
            "action": "query", "prop": "extracts", "explaintext": "1",
            "exintro": "1", "titles": "|".join(titles), "format": "json", "utf8": "1",
            "redirects": "1",
        })
        pages = {}
        if e.status_code == 200:
            pages = (e.json().get("query", {}) or {}).get("pages", {}) or {}
        out = []
        for t in titles:
            extract = ""
            for p in pages.values():
                if p.get("title") == t:
                    extract = (p.get("extract") or "")[:900]
                    break
            out.append(_result(t, f"https://zh.wikipedia.org/wiki/{quote(t)}",
                               extract or f"维基百科条目：{t}", "wiki",
                               {"prefetched": extract}))
        return out


# ---------------------------------------------------------------- OpenAlex 学术库
def _rebuild_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    return " ".join(w for _, w in pos)[:800]


async def openalex(query: str, limit: int = 4) -> list[dict]:
    async with _client(15.0) as c:
        r = await c.get("https://api.openalex.org/works", params={
            "search": query, "per-page": str(limit), "sort": "relevance_score:desc",
        })
        if r.status_code != 200:
            return []
        out = []
        for w in (r.json().get("results") or [])[:limit]:
            title = w.get("title") or w.get("display_name") or ""
            if not title:
                continue
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            landing = (w.get("primary_location") or {}).get("landing_page_url") or w.get("doi") or ""
            year = w.get("publication_year") or ""
            src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            abstract = _rebuild_abstract(w.get("abstract_inverted_index"))
            if not landing:
                continue
            snip = f"[{year} {src}] {abstract}".strip()
            out.append(_result(title, landing, snip, "openalex", {"doi": doi, "year": year}))
        return out


# ---------------------------------------------------------------- 英文源：Hacker News
async def hackernews(query: str, limit: int = 5) -> list[dict]:
    """HN Algolia 接口可直连，能搜到英文技术圈的真实讨论与原文链接。"""
    import time as _t
    since = int(_t.time()) - 400 * 86400          # 只要最近一年多，避免翻出老古董
    async with _client(12.0) as c:
        r = await c.get("https://hn.algolia.com/api/v1/search", params={
            "query": query, "tags": "story", "hitsPerPage": str(limit * 5),
            "numericFilters": f"created_at_i>{since}",
        })
        if r.status_code != 200:
            return []
        hits = [h for h in (r.json().get("hits") or []) if h.get("title")]
        hits.sort(key=lambda h: (h.get("points") or 0), reverse=True)
        out = []
        for h in hits:
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            title = h.get("title") or ""
            pts = h.get("points") or 0
            cmt = h.get("num_comments") or 0
            created = (h.get("created_at") or "")[:10]
            out.append(_result(title, url, f"Hacker News {created} · {pts} 分 · {cmt} 条评论", "hn"))
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------- 英文源：外媒 RSS
_RSS_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml"),
    ("Nature", "https://www.nature.com/nature.rss"),
]
_FEED_CACHE: dict[str, tuple[float, str]] = {}


async def _get_feed(c: httpx.AsyncClient, url: str) -> str:
    import time as _t
    hit = _FEED_CACHE.get(url)
    if hit and _t.time() - hit[0] < 600:
        return hit[1]
    try:
        r = await c.get(url, timeout=10.0)
        if r.status_code == 200:
            _FEED_CACHE[url] = (_t.time(), r.text)
            return r.text
    except Exception:
        pass
    return ""


def _rss_items(xml: str, source: str) -> list[dict]:
    if not xml:
        return []
    try:
        soup = BeautifulSoup(xml, "xml")
    except Exception:
        soup = BeautifulSoup(xml, "lxml")
    items = soup.find_all("item") or soup.find_all("entry")
    out = []
    for it in items[:40]:
        t = it.find("title")
        l = it.find("link")
        d = it.find("description") or it.find("summary") or it.find("content")
        title = t.get_text(" ", strip=True) if t else ""
        href = ""
        if l:
            href = (l.get("href") or l.get_text(strip=True) or "").strip()
        if not title or not href.startswith("http"):
            continue
        desc = BeautifulSoup(d.get_text(" ", strip=True), "lxml").get_text(" ", strip=True) if d else ""
        out.append(_result(title, href, desc, f"rss:{source}"))
    return out


async def rss_search(query: str, limit: int = 4) -> list[dict]:
    """在可直连的外媒 RSS 里按关键词找最近的文章。"""
    words = [w.lower() for w in re.split(r"\s+", query) if len(w) >= 3]
    if not words:
        return []
    async with _client(14.0) as c:
        feeds = await asyncio.gather(*[_get_feed(c, u) for _, u in _RSS_FEEDS], return_exceptions=True)
    scored: list[tuple[int, dict]] = []
    for (name, _), xml in zip(_RSS_FEEDS, feeds):
        if not isinstance(xml, str) or not xml:
            continue
        for item in _rss_items(xml, name):
            hay = (item["title"] + " " + item["snippet"]).lower()
            hits = sum(1 for w in words if w in hay)
            if hits:
                scored.append((hits, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    out = []
    for _, item in scored:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        out.append(item)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 合并与排序
_BAD_HOSTS = (
    "bing.com", "so.com", "sogou.com", "baidu.com/link", "google.com", "doubleclick",
    "zhihu.com/search", "so.toutiao.com", "csdn.net/download",
    # 词典/翻译站：只在查单词时才对，问事情时是噪音
    "iciba.com", "dict.cn", "dictionary.cambridge.org", "youdao.com", "hujiang.com",
    "dict.youdao", "oxfordlearnersdictionaries", "collinsdictionary", "merriam-webster",
    "dict.revised.moe.edu.tw", "moe.edu.tw", "zdic.net", "chazidian", "cidianwang",
    # 视频/短视频站，正文基本读不到
    "iqiyi.com", "youku.com", "bilibili.com", "douyin.com", "360kan.com", "v.qq.com",
)
_GOOD_HOSTS = ("wikipedia.org", ".gov", ".edu", ".org", "arxiv.org", "github.com", "nature.com",
               "sciencedirect.com", "springer.com", "ieee.org", "acm.org", "who.int", "un.org")


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower().replace("www.", "")
        path = p.path.rstrip("/")
        return f"{host}{path}"
    except Exception:
        return url


def _score(item: dict, question: str) -> float:
    score = 1.0
    url = item.get("url", "")
    host = urlparse(url).netloc.lower()
    if any(b in host for b in _BAD_HOSTS):
        score -= 2.0
    if any(g in host for g in _GOOD_HOSTS):
        score += 1.2
    if item.get("prefetched"):
        score += 0.8
    if item.get("engine") == "openalex":
        score += 0.6
    # 问题里点名的官网/品牌，优先
    brand = host.split(".")[0] if host else ""
    if brand and len(brand) >= 3 and brand in question.lower():
        score += 1.6
    if host.startswith(("docs.", "developers.", "developer.", "blog.", "api.")):
        score += 0.5
    # 与问题共享关键词的加分
    qwords = [w for w in re.split(r"[\s,，。？?、的]+", question) if len(w) >= 2]
    hay = (item.get("title", "") + " " + item.get("snippet", "")).lower()
    for w in qwords[:8]:
        if w.lower() in hay:
            score += 0.35
    return score


def merge_and_rank(groups: Iterable[list[dict]], question: str, limit: int = 12) -> list[dict]:
    seen: dict[str, dict] = {}
    seen_titles: set[str] = set()
    for group in groups:
        for item in group or []:
            key = _norm_url(item.get("url", ""))
            if not key:
                continue
            tkey = re.sub(r"[\W_]+", "", (item.get("title") or "").lower())[:40]
            if tkey and tkey in seen_titles and key not in seen:
                continue
            if key in seen:
                seen[key]["support"] = seen[key].get("support", 1) + 1
                if len(item.get("snippet", "")) > len(seen[key].get("snippet", "")):
                    seen[key]["snippet"] = item["snippet"]
                if item.get("prefetched") and not seen[key].get("prefetched"):
                    seen[key]["prefetched"] = item["prefetched"]
            else:
                item["support"] = 1
                seen[key] = item
                if tkey:
                    seen_titles.add(tkey)
    items = list(seen.values())
    for it in items:
        it["score"] = round(_score(it, question) + 0.5 * (it.get("support", 1) - 1), 2)
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


_CN_HOST_HINTS = (
    ".cn", "sina", "163.com", "qq.com", "zhihu", "weibo", "sohu", "ifeng", "eastmoney",
    "csdn", "36kr", "tmtpost", "huxiu", "baidu", "weixin", "toutiao", "bilibili",
    "douban", "jianshu", "juejin", "cnblogs", "ithome", "mydrivers", "51cto", "oschina",
    "gitee", "aliyun", "tencent", "alibaba", "sm.cn", "chinaz", "people.com", "xinhuanet",
    "cctv", "chinanews", "thepaper", "guancha", "hupu", "zol.com", "pconline",
)


def is_cn_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in _CN_HOST_HINTS)


async def search_many(queries: list[str], *, wiki: bool = False, academic: bool = False,
                      en_query: str = "") -> list[list[dict]]:
    """并行跑多个引擎 × 多条检索式。"""
    queries = [simplify_query(q) for q in (queries or [])]
    tasks = []
    for q in queries[:3]:
        tasks.append(bing(q))
        tasks.append(so360(q))
    if queries:
        tasks.append(sogou(queries[0]))
        tasks.append(wiki_zh(queries[0]))
        if academic:
            tasks.append(openalex(queries[0]))
    # 英文源：需要一条英文检索式，没有就尝试从问题里提取
    if en_query:
        tasks.append(hackernews(en_query))
        tasks.append(rss_search(en_query))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, list)]


# 中文口语/疑问词，会把搜索引擎的意图带偏
_FILLER_CN = (
    "怎么算", "怎么样", "怎么办", "怎么做", "是什么", "有哪些", "为什么", "多少钱", "多少",
    "如何", "怎么", "请问", "帮我", "帮忙", "查一下", "查查", "我想知道", "我想", "想知道",
    "能不能", "可不可以", "需要什么", "有什么", "值得关注", "最近", "目前", "现在", "一下",
    "今天", "今日", "昨天", "本周", "这周", "本月", "这个月",
)
_EN_STOP = ("latest", "newest", "current", "recent", "today", "what", "which", "how", "the", "a", "an", "is", "are")


def simplify_query(q: str) -> str:
    """把口语化问题压成关键词，搜索引擎对关键词的召回明显更好。"""
    out = str(q or "").strip()
    for w in _FILLER_CN:
        out = out.replace(w, " ")
    # 英文停用词只清掉开头的，避免误伤 "latest news" 这类有效搭配
    words = out.split()
    while words and words[0].lower().strip("?,.") in _EN_STOP:
        words.pop(0)
    out = " ".join(words)
    out = re.sub(r"[？?！!。，,、；;：:]+", " ", out)
    out = re.sub(r"(^|\s)的(\s|$)", " ", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out or str(q or "").strip()


def is_english(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(1, len(text)) < 0.2


def pick_en_query(question: str, planned: list[str]) -> str:
    """挑一条适合英文检索的关键词。"""
    for q in planned:
        if is_english(q):
            return q
    # 从问题里的英文词（产品名、人名）拼一条
    words = re.findall(r"[A-Za-z][A-Za-z0-9\.\-]{1,}", question)
    words = [w for w in words if len(w) >= 2][:3]
    return " ".join(words)
