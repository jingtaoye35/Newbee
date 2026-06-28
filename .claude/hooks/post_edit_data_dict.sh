#!/usr/bin/env bash
# PostToolUse hook: 当 Edit/Write/MultiEdit 作用于 configs/data_dict/*.yaml 或
# alpha_backend/datasource/schemas/*.py 时, 自动跑 codegen + dict_sync.
#
# 用法: 在 .claude/settings.json 中注册 PostToolUse hook.
# 仅当文件路径匹配 configs/data_dict/*.yaml 或 alpha_backend/datasource/schemas/*.py 时触发.

set -u

INPUT="$(cat)"
TOOL_NAME="$(echo "$INPUT" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_name",""))' 2>/dev/null || true)"
FILE_PATH="$(echo "$INPUT" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"

# 仅对 Edit / Write / MultiEdit 触发
case "$TOOL_NAME" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

# 仅对 configs/data_dict/*.yaml 或 alpha_backend/datasource/schemas/*.py 触发
case "$FILE_PATH" in
  */configs/data_dict/*.yaml|*/alpha_backend/datasource/schemas/*.py) ;;
  *) exit 0 ;;
esac

# 激活 conda 环境并跑 codegen + dict_sync
# 用 source 显式激活 (zshenv 修复无效, 见 feedback memory)
CONDA_BASE="$(conda info --base 2>/dev/null || true)"
if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
  # shellcheck disable=SC1091
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  conda activate py312 2>/dev/null || true
fi

cd "$(dirname "$0")/../.." 2>/dev/null || cd /Users/yejingtao/JohnsonProject/Newbee

echo "[codegen-hook] triggered by $TOOL_NAME on $FILE_PATH"

# 跑 codegen (失败也继续, 便于调试)
if ! python -m alpha_backend.datasource.codegen 2>&1; then
  echo "[codegen-hook] WARN: codegen returned non-zero (may be expected if YAML is partial)"
fi

# 跑 dict_sync (失败 hook 输出 warning, 不阻断 Claude)
if ! python -m pytest tests/test_dict_sync.py -q 2>&1; then
  echo "[codegen-hook] FAIL: test_dict_sync.py failed. Please run codegen + tests manually."
fi

exit 0