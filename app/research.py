"""研究流程编排：规划检索 → 多引擎搜索 → 读正文 → 汇总作答。"""
from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import AsyncIterator
from urllib.parse import urlparse

from . import cache, config, fetch, llm, search

SYSTEM_PROMPT = """你是「万能搜索助手」，服务对象是普通中文用户，可能对技术完全不了解。
请用简体中文回答，语气自然、直接、好懂。

硬性规则：
1. 只依据下方【资料】作答，不要编造。资料不足时，直接说"资料里没有足够信息"，并说明还缺什么、建议怎么查。
2. 引用来源用方括号编号，例如 [1]、[2][3]，编号必须对应资料编号。
3. 关键数字、日期、人名、产品名要准确；不确定就写"不确定"。
4. 输出用 Markdown：开头一两句话给结论，然后分点展开；必要时用小标题。
5. 默认 300~800 字；用户明确要求"详细"时才展开写长。
6. 资料以英文为主时，用中文讲清楚，关键术语保留英文原词并加简短解释。
7. 不要输出"根据您提供的资料"这类套话，直接讲内容。
8. 如果问题涉及国内可访问性（比如某网站/软件在国内能不能用），要如实说明。"""


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def _now() -> str:
    return date.today().isoformat()


async def _collect(question: str, plan: dict) -> list[dict]:
    queries = [question] + [q for q in plan.get("queries", []) if q.lower() != question.lower()]
    queries = queries[:3]
    groups = await search.search_many(queries, wiki=plan.get("wiki", False), academic=plan.get("academic", False))
    return search.merge_and_rank(groups, question, limit=14)


def _pick_to_read(items: list[dict], max_pages: int) -> list[dict]:
    """挑要精读的页面：保证国内外来源都有，同一站点最多 2 篇。"""
    cn_cap = max(2, max_pages // 2)
    picked: list[dict] = []
    per_host: dict[str, int] = {}
    cn_count = 0

    def take(it: dict) -> bool:
        nonlocal cn_count
        h = _host(it.get("url", ""))
        if not h or per_host.get(h, 0) >= 2:
            return False
        cn = search.is_cn_host(it.get("url", ""))
        if cn and cn_count >= cn_cap:
            return False
        picked.append(it)
        per_host[h] = per_host.get(h, 0) + 1
        if cn:
            cn_count += 1
        return True

    for it in items:
        if len(picked) >= max_pages:
            break
        take(it)
    if len(picked) < max_pages:
        for it in items:
            if len(picked) >= max_pages:
                break
            if it not in picked:
                h = _host(it.get("url", ""))
                if per_host.get(h, 0) < 2:
                    picked.append(it)
                    per_host[h] = per_host.get(h, 0) + 1
    return picked[:max_pages]


def _brief(items: list[dict]) -> list[dict]:
    return [
        {
            "idx": i + 1,
            "title": it.get("title") or _host(it.get("url", "")),
            "url": it.get("url", ""),
            "host": _host(it.get("url", "")),
            "snippet": (it.get("snippet") or "")[:240],
            "engine": it.get("engine", ""),
        }
        for i, it in enumerate(items)
    ]


def _build_messages(question: str, docs: list[dict]) -> list[dict]:
    blocks = []
    for i, d in enumerate(docs, 1):
        head = f"[{i}] {d['title']}（{d['host']}）"
        body = d.get("text") or d.get("snippet") or ""
        body = body[: config.PAGE_CHARS]
        blocks.append(f"{head}\n{body}")
    material = "\n\n".join(blocks) if blocks else "（没有检索到任何资料）"
    user = (
        f"今天是 {_now()}。\n\n用户问题：{question}\n\n【资料】\n{material}\n\n"
        "请按要求作答。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


async def ask(question: str, *, deep: bool = False) -> AsyncIterator[dict]:
    """主流程，逐步 yield 事件给前端。"""
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "message": "请输入要查找的内容"}
        return

    max_pages = config.MAX_PAGES + (2 if deep else 0)
    yield {"type": "status", "text": "正在理解问题、规划检索…", "step": 1}

    fast_en = search.pick_en_query(question, [])
    plan_task = asyncio.create_task(llm.plan_queries(question))
    seed_task = asyncio.create_task(search.search_many([question], wiki=True, en_query=fast_en))
    try:
        plan = await asyncio.wait_for(plan_task, timeout=25)
    except Exception:
        plan = {"queries": [], "wiki": False, "academic": False}

    planned = [q for q in plan.get("queries", []) if q]
    en_query = search.pick_en_query(question, planned)
    cn_queries = [q for q in planned if not search.is_english(q)][:2]

    extra_groups: list[list[dict]] = []
    if cn_queries or (en_query and en_query != fast_en):
        label = " / ".join(cn_queries[:2]) or en_query
        yield {"type": "status", "text": f"正在检索：{label}", "step": 2}
        extra_groups = await search.search_many(
            cn_queries, wiki=False, academic=plan.get("academic", False),
            en_query=en_query if en_query != fast_en else "",
        )
    try:
        seed_groups = await asyncio.wait_for(seed_task, timeout=30)
    except Exception:
        seed_groups = []

    items = search.merge_and_rank(seed_groups + extra_groups, question, limit=14)
    if not items:
        yield {"type": "error", "message": "没有搜到结果。可能是网络问题，或关键词太特殊，换个说法试试。"}
        return

    t_search = time.time()
    yield {"type": "status", "text": f"找到 {len(items)} 条线索，正在读取正文…", "step": 3}
    yield {"type": "sources", "items": _brief(items), "staged": True}

    to_read = _pick_to_read(items, max_pages)
    remaining = config.MICROLINK_DAILY_CAP - cache.counter("microlink")
    if remaining <= 2:
        mic_budget = 0
    elif remaining <= 8:
        mic_budget = 1
    else:
        mic_budget = 3 if deep else 2
    if mic_budget == 0:
        yield {"type": "status", "text": "今天的云端阅读额度快用完了，读不了的页面只看摘要", "step": 3}
    urls = [it["url"] for it in to_read if not it.get("prefetched")]
    read_map: dict[str, dict] = {it["url"]: it for it in to_read if it.get("prefetched")}
    if urls:
        results = await fetch.read_many(urls, limit_microlink=mic_budget,
                                        max_chars=config.PAGE_CHARS * 4)
        for r in results:
            read_map[r["url"]] = r

    docs: list[dict] = []
    for i, it in enumerate(items, 1):
        url = it["url"]
        r = read_map.get(url)
        if r and r.get("ok"):
            docs.append({
                "idx": i, "title": r.get("title") or it.get("title"), "url": url,
                "host": _host(url), "text": r.get("text", ""), "via": r.get("via", ""),
                "snippet": it.get("snippet", ""), "read": True, "chars": len(r.get("text", "")),
            })
        else:
            docs.append({
                "idx": i, "title": it.get("title"), "url": url, "host": _host(url),
                "text": "", "via": "", "snippet": it.get("snippet", ""), "read": False, "chars": 0,
            })

    t_read = time.time()
    read_n = sum(1 for d in docs if d["read"])
    yield {"type": "sources", "items": [
        {k: d[k] for k in ("idx", "title", "url", "host", "snippet", "read", "chars")} for d in docs
    ], "staged": False}
    yield {"type": "status", "text": f"已精读 {read_n} 篇，正在整理答案…", "step": 4}

    messages = _build_messages(question, docs)
    try:
        async for piece in llm.stream_chat(messages, max_tokens=4000 if deep else 2800):
            yield {"type": "delta", "text": piece}
    except llm.LLMError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except Exception as exc:                      # 网络抖动等，保证前端收到结束信号
        yield {"type": "error", "message": f"生成答案时出错：{type(exc).__name__}: {exc}"}
        return

    yield {
        "type": "done",
        "elapsed": round(time.time() - t0, 1),
        "read": read_n,
        "sources": len(docs),
        "timing": {
            "search_s": round(t_search - t0, 1),
            "read_s": round(t_read - t_search, 1),
            "write_s": round(time.time() - t_read, 1),
        },
    }


async def read_and_summarize(url: str, instruction: str = "") -> AsyncIterator[dict]:
    """直接读一个链接并总结/翻译。"""
    t0 = time.time()
    yield {"type": "status", "text": "正在打开链接…", "step": 1}
    r = await fetch.read_page(url, allow_microlink=True, max_chars=30000)
    if not r.get("ok"):
        yield {"type": "error", "message": f"打不开这个链接（{r.get('error') or '被拦截或需要登录'}）。可以试试换个链接，或直接搜标题。"}
        return
    yield {"type": "status", "text": f"已读取 {len(r['text'])} 字，正在整理…", "step": 2}
    yield {"type": "sources", "items": [{
        "idx": 1, "title": r.get("title") or url, "url": url, "host": _host(url),
        "snippet": r["text"][:200], "read": True, "chars": len(r["text"]),
    }], "staged": False}

    prompt = instruction or "用中文总结这篇页面的核心内容：它讲什么、关键信息（数字/时间/人物/结论）、对普通读者有用的点。"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"页面地址：{url}\n\n【资料】\n[1] {r.get('title') or url}（{_host(url)}）\n{r['text'][:12000]}\n\n任务：{prompt}"},
    ]
    try:
        async for piece in llm.stream_chat(messages, max_tokens=2500):
            yield {"type": "delta", "text": piece}
    except llm.LLMError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "done", "elapsed": round(time.time() - t0, 1), "read": 1, "sources": 1}
