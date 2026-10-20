#!/usr/bin/env bash
# 停止后台服务
cd "$(dirname "$0")" || exit 1
pkill -f "uvicorn app.main:app" 2>/dev/null && echo "已停止" || echo "服务本来就没在运行"
