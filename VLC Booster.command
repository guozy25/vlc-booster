#!/bin/bash
# 双击即可启动 VLC Booster
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "第一次运行,正在准备环境(需要联网,约 1~2 分钟)..."
  if ! /usr/bin/python3 -m venv .venv; then
    echo ""
    echo "缺少 Python 运行环境。请先在终端执行: xcode-select --install"
    echo "安装完成后再次双击本文件。"
    read -r -p "按回车关闭..."
    exit 1
  fi
  if ! ./.venv/bin/pip install --quiet -r requirements.txt; then
    echo "依赖安装失败,请检查网络后重试。"
    read -r -p "按回车关闭..."
    exit 1
  fi
fi

exec ./.venv/bin/python3 vlc_booster.py "$@"
