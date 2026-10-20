"""DeepSeek 模型调用（流式 + 非流式）。"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator

import httpx

from . import config


class LLMError(RuntimeError):
    pass


def _client(timeout: float = 120.0) -> httpx.AsyncClient:
    kwargs: dict = {"timeout": httpx.Timeout(timeout, connect=15.0)}
    if config.PROXY:
        kwargs["proxy"] = config.PROXY
    return httpx.AsyncClient(**kwargs)


def _headers() -> dict:
    if not config.API_KEY:
        raise LLMError("缺少 DeepSeek API Key")
    return {
        "Authorization": f"Bearer {config.API_KEY}",
        "Content-Type": "application/json",
    }


async def chat(messages: list[dict], *, model: str | None = None, temperature: float = 0.3,
               max_tokens: int = 1200, json_mode: bool = False) -> str:
    payload: dict = {
        "model": model or config.MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with _client(90.0) as c:
        r = await c.post(f"{config.BASE_URL}/chat/completions", headers=_headers(), json=payload)
        if r.status_code != 200:
            raise LLMError(f"模型接口返回 {r.status_code}: {r.text[:300]}")
        data = r.json()
        return data["choices"][0]["message"]["content"] or ""


async def stream_chat(messages: list[dict], *, model: str | None = None, temperature: float = 0.35,
                      max_tokens: int = 3000) -> AsyncIterator[str]:
    payload = {
        "model": model or config.MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with _client(180.0) as c:
        async with c.stream("POST", f"{config.BASE_URL}/chat/completions",
                            headers=_headers(), json=payload) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise LLMError(f"模型接口返回 {r.status_code}: {body[:300].decode('utf-8', 'ignore')}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece


_DATE_RE = re.compile(r"(19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?|"
                      r"(19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*月?|"
                      r"(今天|今日|昨天|本周|这周|最近|目前|现在)")


def _clean_query(q: str) -> str:
    """去掉检索词里的日期和“今天/最近”这类词，搜索引擎对它们很敏感。"""
    q = _DATE_RE.sub(" ", str(q))
    q = re.sub(r"\s{2,}", " ", q).strip(" ,，。?？")
    return q


async def plan_queries(question: str) -> dict:
    """让模型把问题拆成多条检索式，并判断是否需要百科/学术检索。"""
    from datetime import date
    sys = (
        f"你是检索规划器。今天是 {date.today().isoformat()}。根据用户问题，输出 JSON："
        '{"queries": ["...", "..."], "wiki": true/false, "academic": true/false}。'
        "要求：\n"
        "1) queries 为 2-3 条互补的检索词，简洁、无标点、适合搜索引擎；\n"
        "2) 至少一条中文检索式；并且必须再给一条英文检索式（用于检索国外技术媒体）；\n"
        "3) 检索词里不要写具体日期、不要写“今天/最近/目前”这类词；\n"
        "4) 问题问“新闻/最新动态”时，用“主题 + 新闻/发布”这类词，不要加日期。\n"
        "wiki=true 表示名词/人物/公司/概念类问题；academic=true 表示涉及论文、研究、医学、学术。"
        "只输出 JSON。"
    )
    try:
        raw = await chat(
            [{"role": "system", "content": sys}, {"role": "user", "content": question}],
            temperature=0.1, max_tokens=260, json_mode=True,
        )
        obj = json.loads(raw)
        qs = []
        for q in obj.get("queries", []):
            c = _clean_query(q)
            if c and c not in qs:
                qs.append(c)
        return {"queries": qs[:3], "wiki": bool(obj.get("wiki")), "academic": bool(obj.get("academic"))}
    except Exception:
        return {"queries": [], "wiki": False, "academic": False}
