#!/usr/bin/env bash
# 安装依赖（在项目内建独立环境，不动系统 Python，不需要 sudo）
set -euo pipefail
cd "$(dirname "$0")" || exit 1

PY="${PYTHON:-python3}"
MIRROR="${PIP_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
HOST="$(echo "$MIRROR" | sed -E 's#https?://([^/]+).*#\1#')"

echo "==> 使用 Python: $($PY --version 2>&1)"

if [ ! -d .venv ]; then
  echo "==> 创建虚拟环境 .venv"
  if $PY -m venv .venv 2>/dev/null && [ -x .venv/bin/pip ]; then
    :
  else
    # 系统缺 python3-venv / ensurepip 时，先建空环境再手动装 pip
    rm -rf .venv
    $PY -m venv --without-pip .venv
    echo "==> 引导 pip"
    curl -fsSL -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
    .venv/bin/python /tmp/get-pip.py -i "$MIRROR" --trusted-host "$HOST"
  fi
fi

echo "==> 安装依赖（镜像：$MIRROR）"
.venv/bin/pip install -q -r requirements.txt -i "$MIRROR" --trusted-host "$HOST"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

echo
echo "安装完成。启动方式： ./start.sh"
echo "（Windows/macOS 用户请参考 README 里的手动步骤）"
