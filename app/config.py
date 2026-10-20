"""全局配置：密钥、模型、代理、限额。

设计原则：小白用户不配置也能跑。
- 密钥自动从 DSH 的 ~/.dsh/.credentials.yaml 读取，也可用 .env 覆盖
- 代理自动探测常见的本地端口（Clash / v2ray 等），探测到就自动使用
"""
from __future__ import annotations

import os
import re
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DB = DATA_DIR / "cache.sqlite3"
ENV_FILE = ROOT / ".env"

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _deepseek_key_from_dsh() -> str:
    """从 DSH 凭证文件里取 key，用户无需手动填写。"""
    p = Path.home() / ".dsh" / ".credentials.yaml"
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = re.search(r"DEEPSEEK_API_KEY\s*:\s*(\S+)", text)
    return m.group(1).strip() if m else ""


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_proxy() -> str:
    """优先用用户显式配置，其次自动探测本地代理端口。"""
    _load_env_file()
    for var in ("SEARCH_AGENT_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    for host in ("127.0.0.1",):
        for port in (7890, 7897, 1080, 10808, 8080, 8118, 20171, 33210):
            if _port_open(host, port):
                return f"http://{host}:{port}"
    return ""


_load_env_file()

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip() or _deepseek_key_from_dsh()
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)

PROXY = detect_proxy()
MICROLINK_KEY = os.environ.get("MICROLINK_API_KEY", "").strip()

HOST = os.environ.get("SEARCH_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("SEARCH_AGENT_PORT", "8899"))

# 每次提问最多读几个网页的正文
MAX_PAGES = int(os.environ.get("SEARCH_AGENT_MAX_PAGES", "6"))
# microlink 免费额度约 25 次/天，单独限流，避免一次提问就烧光
MICROLINK_DAILY_CAP = int(os.environ.get("SEARCH_AGENT_MICROLINK_CAP", "20"))
# 每个网页塞给模型的最大字符数
PAGE_CHARS = int(os.environ.get("SEARCH_AGENT_PAGE_CHARS", "2600"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)


def summary() -> dict:
    return {
        "key_ok": bool(API_KEY),
        "key_source": "DSH 凭证" if API_KEY and not os.environ.get("DEEPSEEK_API_KEY") else ("环境变量" if API_KEY else "缺失"),
        "base_url": BASE_URL,
        "model": MODEL,
        "proxy": PROXY or "无（直连）",
        "max_pages": MAX_PAGES,
    }
