#!/usr/bin/env bash
# 一键启动：没运行就后台启动服务，然后用浏览器打开
set -u
cd "$(dirname "$0")" || exit 1

PORT="${SEARCH_AGENT_PORT:-8899}"
URL="http://127.0.0.1:${PORT}/"

is_up() { curl -s --max-time 1 "${URL}api/health" >/dev/null 2>&1; }

if ! is_up; then
  if [ ! -x .venv/bin/python ]; then
    echo "还没安装运行环境，请先执行： ./install.sh"
    exit 1
  fi
  mkdir -p data
  setsid nohup ./run.sh > data/server.log 2>&1 < /dev/null &
  for _ in $(seq 1 60); do
    is_up && break
    sleep 0.25
  done
fi

if ! is_up; then
  echo "启动失败。日志：$(pwd)/data/server.log"
  exit 1
fi

xdg-open "$URL" >/dev/null 2>&1 || sensible-browser "$URL" >/dev/null 2>&1 || true
echo "万能搜索已启动：$URL"
