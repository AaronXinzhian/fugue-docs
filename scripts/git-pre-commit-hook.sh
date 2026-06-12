#!/bin/sh
# [INPUT]: 依赖 git, python3, geb_check.py(同目录或 ~/.claude/skills/fugue-docs/scripts/)
# [OUTPUT]: git pre-commit 钩子——两相不同构时拒绝提交(跨工具硬约束,不依赖任何 AI 助手)
# [POS]: fugue-docs 工具层-通用回环硬约束(Claude Code 之外的所有环境)
# [PROTOCOL]: 变更时更新此头部,然后检查 README 中对本钩子的描述
#
# 安装(在你的项目里):
#   cp /path/to/fugue-docs/scripts/git-pre-commit-hook.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
# 跳过一次检查:git commit --no-verify

REPO_ROOT="$(git rev-parse --show-toplevel)" || exit 0

# 与 Stop 钩子同一开关:项目未采用协议(无 PROJECT_INDEX.md)则零打扰
[ -f "$REPO_ROOT/PROJECT_INDEX.md" ] || exit 0

# 定位检查器:环境变量 > 本脚本同目录 > 全局安装路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for candidate in "$GEB_CHECK" "$SCRIPT_DIR/geb_check.py" \
    "$HOME/.claude/skills/fugue-docs/scripts/geb_check.py"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        GEB_CHECK_PATH="$candidate"
        break
    fi
done

if [ -z "$GEB_CHECK_PATH" ]; then
    echo "fugue-docs: 找不到 geb_check.py,跳过检查(可设 GEB_CHECK 环境变量指定路径)" >&2
    exit 0
fi

if ! python3 "$GEB_CHECK_PATH" "$REPO_ROOT"; then
    echo "" >&2
    echo "fugue-docs: 代码与文档两相不同构,提交被拒绝。" >&2
    echo "请完成 L3(文件头)→ L2(FOLDER_INDEX.md)→ L1(PROJECT_INDEX.md)回环后再提交。" >&2
    echo "(确需跳过本次检查:git commit --no-verify)" >&2
    exit 1
fi
