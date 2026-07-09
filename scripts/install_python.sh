#!/bin/bash
# 在 Ubuntu x86_64 上安装 Python3 + pip + requests
# 用法: sudo bash install_python.sh

set -e

echo "📦 安装 Python3 和 pip..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv

echo "📦 安装 requests 库..."
pip3 install requests --break-system-packages -q

echo "✅ 安装完成"
python3 --version
python3 -c "import requests; print('requests', requests.__version__)"
