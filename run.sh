#!/usr/bin/env bash
# 启动万能搜索助手（后台常驻由 start.sh 负责）
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${SEARCH_AGENT_PORT:-8899}" --log-level warning
