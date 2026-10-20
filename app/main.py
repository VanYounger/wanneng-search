"""本地 Web 服务入口。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import cache, config, llm, research

WEB = config.ROOT / "web"
app = FastAPI(title="万能搜索助手", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

_sem = asyncio.Semaphore(3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(config.DATA_DIR / "server.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("wanneng")
# 第三方库的每条请求日志太吵，只保留警告
for noisy in ("httpx", "httpcore", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "config": config.summary(),
        "microlink_used_today": cache.counter("microlink"),
        "microlink_cap": config.MICROLINK_DAILY_CAP,
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/ping")
async def ping():
    """测一下模型接口是否可用，给小白一个"自检"按钮。"""
    t0 = time.time()
    try:
        txt = await llm.chat([{"role": "user", "content": "只回复两个字：正常"}], max_tokens=24, temperature=0)
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "reply": txt.strip()[:20]}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=200)


@app.post("/api/ask")
async def api_ask(req: Request):
    body = await req.json()
    question = (body.get("question") or "").strip()
    deep = bool(body.get("deep"))

    async def gen():
        async with _sem:
            try:
                async for ev in research.ask(question, deep=deep):
                    yield _sse(ev)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 兜底，保证前端一定收到结束信号
                log.error("ask failed: %s\n%s", exc, traceback.format_exc())
                yield _sse({"type": "error", "message": f"出错了：{type(exc).__name__}: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })


@app.post("/api/read")
async def api_read(req: Request):
    body = await req.json()
    url = (body.get("url") or "").strip()
    instruction = (body.get("instruction") or "").strip()

    async def gen():
        async with _sem:
            try:
                async for ev in research.read_and_summarize(url, instruction):
                    yield _sse(ev)
            except Exception as exc:
                log.error("read failed: %s\n%s", exc, traceback.format_exc())
                yield _sse({"type": "error", "message": f"出错了：{type(exc).__name__}: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
