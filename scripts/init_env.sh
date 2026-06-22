#!/bin/bash
set -euo pipefail

# ---------- 路径与常量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_NAME="${ENV_NAME:-py312}"
PY_VERSION="3.12"

# ---------- conda 可用性检查 ----------
if ! command -v conda >/dev/null 2>&1; then
    echo "[init_env] ❌ conda 不在 PATH, 请先安装 Miniconda/Anaconda" >&2
    exit 1
fi

# ---------- 环境存在性检测 ----------
# conda env list 输出形如 "py312    /opt/.../envs/py312", 用锚定 ^ENV_NAME 加空格避免前缀误判
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    echo "[init_env] ✓ conda 环境 '${ENV_NAME}' 已存在, 跳过创建"
else
    echo "[init_env] → 创建 conda 环境 '${ENV_NAME}' (python=${PY_VERSION})"
    conda create -n "${ENV_NAME}" "python=${PY_VERSION}" -y
fi

# ---------- 同步依赖 ----------
echo "[init_env] → pip install -e '${PROJECT_ROOT}[dev] --upgrade'"
conda run -n "${ENV_NAME}" pip install -e "${PROJECT_ROOT}[dev]" --upgrade

echo ""
echo "[init_env] ✅ 完成. 激活方式:"
echo "    conda activate ${ENV_NAME}"
