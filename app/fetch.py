"""网页正文抓取。

两条通道：
1) 直连（大多数国内站、部分国外站）
2) microlink 云端读取（能读到被墙的页面，比如英文维基、外媒），有每日额度，需节约使用
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

from . import cache, config

_SKIP_EXT = (".pdf", ".zip", ".rar", ".7z", ".mp4", ".mp3", ".exe", ".dmg", ".apk",
             ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".doc", ".docx", ".xls", ".xlsx", ".ppt")

_BLOCK_HINTS = ("just a moment", "cf-browser-verification", "attention required", "captcha",
                "access denied", "人机验证", "安全验证", "请开启javascript", "enable javascript",
                "验证码", "访问验证", "滑动验证", "人机身份验证", "环境异常")

# 反爬/验证页特征（命中就认为没读到正文）
_CHALLENGE_MARKERS = (
    "cf-chl", "cf_chl_opt", "just a moment...", "enable javascript and cookies to continue",
    "verifying you are human", "attention required! | cloudflare", "checking your browser",
    "ddos protection by", "please turn javascript on and reload the page",
    "window._cf_chl_opt", "challenge-platform",
)


def is_challenge(html: str) -> bool:
    low = (html or "")[:20000].lower()
    return any(m in low for m in _CHALLENGE_MARKERS)


def _client(timeout: float = 10.0) -> httpx.AsyncClient:
    kwargs: dict = {
        "timeout": httpx.Timeout(timeout, connect=5.0),
        "follow_redirects": True,
        "headers": {
            "User-Agent": config.UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    }
    if config.PROXY:
        kwargs["proxy"] = config.PROXY
    return httpx.AsyncClient(**kwargs)


def extract_text(html: str, url: str = "") -> str:
    """先用 trafilatura 抽正文，失败再退回 BeautifulSoup。"""
    text = ""
    try:
        text = trafilatura.extract(
            html, url=url or None, include_comments=False, include_tables=True,
            favor_recall=True, no_fallback=False,
        ) or ""
    except Exception:
        text = ""
    if len(text) < 200:
        try:
            soup = BeautifulSoup(html, "lxml")
            for bad in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]):
                bad.decompose()
            body = soup.find("article") or soup.find("main") or soup.body or soup
            alt = body.get_text("\n", strip=True)
            if len(alt) > len(text):
                text = alt
        except Exception:
            pass
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def page_title(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        if soup.title and soup.title.string:
            return soup.title.string.strip()[:200]
    except Exception:
        pass
    return ""


async def _direct(url: str) -> dict:
    async with _client() as c:
        r = await c.get(url)
        html = r.text or ""
        blocked = (r.status_code >= 400 or len(html) < 300 or is_challenge(html)
                   or any(h in html[:3000].lower() for h in _BLOCK_HINTS))
        if blocked:
            return {"ok": False, "status": r.status_code, "html": html, "via": "direct"}
        return {"ok": True, "status": r.status_code, "html": html, "via": "direct"}


async def _microlink(url: str) -> dict:
    """云端读取：返回页面 body 的 HTML。"""
    used = cache.counter("microlink")
    if used >= config.MICROLINK_DAILY_CAP:
        return {"ok": False, "status": 0, "html": "", "via": "microlink", "quota": True}
    params = {
        "url": url,
        "data.text.selector": "body",
        "data.text.type": "text",
        "meta": "false",
    }
    if config.MICROLINK_KEY:
        params["apiKey"] = config.MICROLINK_KEY
    try:
        async with _client(28.0) as c:
            r = await c.get("https://api.microlink.io/", params=params)
            cache.bump_counter("microlink")
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "html": "", "via": "microlink"}
            data = r.json()
            if data.get("status") != "success":
                return {"ok": False, "status": r.status_code, "html": "", "via": "microlink"}
            html = (data.get("data") or {}).get("text") or ""
            return {"ok": bool(html), "status": 200, "html": html, "via": "microlink"}
    except Exception as exc:  # 网络超时等
        return {"ok": False, "status": 0, "html": "", "via": "microlink", "error": str(exc)[:120]}


def _looks_blocked(url: str, text: str) -> bool:
    host = urlparse(url).netloc.lower()
    low = (text or "").lower()
    if len(text) < 200:
        return True
    if any(h in low[:600] for h in _BLOCK_HINTS):
        return True
    if "just a moment" in low[:300] or "enable javascript" in low[:300]:
        return True
    # 常见"页面不存在/需要登录"页
    if len(text) < 600 and ("404" in host or "not found" in low[:300]):
        return True
    return False


async def read_page(url: str, *, allow_microlink: bool = True, max_chars: int | None = None) -> dict:
    """读一个网页，返回 {ok, via, title, text, url, cached}"""
    url = (url or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "url": url, "via": "none", "title": "", "text": "", "error": "无效链接"}
    if urlparse(url).path.lower().endswith(_SKIP_EXT):
        return {"ok": False, "url": url, "via": "none", "title": "", "text": "", "error": "非网页文件"}

    hit = cache.get_page(url)
    if hit and hit.get("ok") and hit.get("text") and not _looks_blocked(url, hit["text"]):
        text = hit["text"]
        return {"ok": True, "url": url, "via": hit["via"] + "+cache", "title": hit.get("title", ""),
                "text": text[: max_chars or 20000], "cached": True}

    try:
        res = await _direct(url)
    except Exception as exc:
        res = {"ok": False, "status": 0, "html": "", "via": "direct", "error": str(exc)[:120]}

    html, via = res.get("html", ""), res.get("via", "direct")
    if html and is_challenge(html):
        html = ""
        res["ok"] = False
    text = extract_text(html, url) if html else ""
    title = page_title(html) if html else ""
    if title.lower().startswith("just a moment") or "验证" in title[:12]:
        text, title = "", ""

    if (not res.get("ok")) or _looks_blocked(url, text):
        if allow_microlink:
            m = await _microlink(url)
            if m.get("ok") and m.get("html") and not is_challenge(m["html"]):
                mtext = extract_text(m["html"], url)
                if len(mtext) > len(text):
                    text, via = mtext, "microlink"
                    title = title or page_title(m["html"])

    ok = bool(text) and not _looks_blocked(url, text)
    if ok and title:
        text = f"{title}\n\n{text}" if title not in text[:200] else text
    if ok:
        cache.put_page(url, True, via, title, text[:60000])
    return {
        "ok": ok,
        "url": url,
        "via": via,
        "title": title[:200],
        "text": text[: max_chars or 20000],
        "error": "" if ok else (res.get("error") or f"HTTP {res.get('status')}"),
    }


async def read_many(urls: list[str], *, limit_microlink: int = 2, max_chars: int | None = None) -> list[dict]:
    """并行读多个页面：先全部直连，失败的再依次用 microlink 补读，额度不够就放弃。"""
    sem = asyncio.Semaphore(4)

    async def direct(u: str) -> dict:
        async with sem:
            return await read_page(u, allow_microlink=False, max_chars=max_chars)

    results = await asyncio.gather(*[direct(u) for u in urls], return_exceptions=True)
    out = [r for r in results if isinstance(r, dict)]

    failed = [r for r in results if isinstance(r, dict) and not r.get("ok")][:limit_microlink]
    if failed:
        async def rescue(r: dict) -> dict:
            async with sem:
                ret = await read_page(r["url"], allow_microlink=True, max_chars=max_chars)
                return ret if ret.get("ok") else r

        rescued = await asyncio.gather(*[rescue(r) for r in failed], return_exceptions=True)
        by_url = {r["url"]: r for r in rescued if isinstance(r, dict)}
        out = [by_url.get(r["url"], r) for r in out]
    return out
