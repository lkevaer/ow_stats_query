#!/bin/bash
# ============================================================================
# 配置 pip 国内镜像源（清华 TUNA）
# 在项目根目录运行: chmod +x setup_pip_mirror.sh && ./setup_pip_mirror.sh
# ============================================================================

set -e

MIRROR_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
MIRROR_HOST="pypi.tuna.tsinghua.edu.cn"

PIP_CONF_CONTENT="[global]
index-url = ${MIRROR_URL}
trusted-host = ${MIRROR_HOST}
timeout = 60

[install]
trusted-host = ${MIRROR_HOST}
"

echo "=========================================="
echo "  配置 pip 国内镜像源 (清华 TUNA)"
echo "  镜像地址: ${MIRROR_URL}"
echo "=========================================="

# --- 1. ~/.pip/pip.conf (传统位置) ---
mkdir -p ~/.pip
echo "${PIP_CONF_CONTENT}" > ~/.pip/pip.conf
echo "[1/3] ~/.pip/pip.conf ✓"

# --- 2. ~/.config/pip/pip.conf (XDG 标准位置) ---
mkdir -p ~/.config/pip
echo "${PIP_CONF_CONTENT}" > ~/.config/pip/pip.conf
echo "[2/3] ~/.config/pip/pip.conf ✓"

# --- 3. pyenv 解释器级别（如果有）---
if [ -d ~/.pyenv/versions ]; then
    for vdir in ~/.pyenv/versions/*/; do
        echo "${PIP_CONF_CONTENT}" > "${vdir}pip.conf" 2>/dev/null || true
    done
    echo "[3/3] pyenv 各版本 pip.conf ✓"
else
    echo "[3/3] 未检测到 pyenv，跳过"
fi

echo ""
echo "=========================================="
echo "  配置完成！验证效果："
echo "  pip3 config list"
echo "=========================================="

pip3 config list
