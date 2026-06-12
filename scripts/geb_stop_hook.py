#!/usr/bin/env python3
"""
[INPUT]: 依赖 Python3 标准库 (sys, os, json); 同目录 geb_check 模块(run_checks, find_index_file, L1_NAMES)
[OUTPUT]: Claude Code Stop hook——项目不同构时输出 {"decision":"block"} 阻止收工
[POS]: fugue-docs 工具层-回环硬约束钩子
[PROTOCOL]: 变更时更新此头部,然后检查 SKILL.md 与 README 中对本钩子的描述

工作方式:
- 仅当会话目录被判定为已采用协议时才检查(与 geb_check 同一判定:
  存在 PROJECT_INDEX.md,或 CLAUDE.md 含 GEB 协议标识),其他项目零打扰。
- 发现违规则阻止 Claude 结束回合,并把违规清单作为理由回灌,
  迫使其完成 L3→L2→L1 回环后才能收工。
- stop_hook_active 为 true 时直接放行,避免无限循环。

安装(加入 ~/.claude/settings.json):
  "hooks": {"Stop": [{"hooks": [{"type": "command",
    "command": "python3 \"$HOME/.claude/skills/fugue-docs/scripts/geb_stop_hook.py\"",
    "timeout": 30}]}]}
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geb_check import L1_NAMES, find_index_file, run_checks  # noqa: E402

MAX_LISTED = 12


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0  # 输入异常时放行,钩子绝不能卡死正常工作

    if payload.get("stop_hook_active"):
        return 0

    cwd = payload.get("cwd") or os.getcwd()
    try:
        adopted = find_index_file(cwd, L1_NAMES)  # 与检查器同一套 L1 判定
    except Exception:  # noqa: BLE001
        return 0
    if not adopted:
        return 0  # 项目未采用协议,不打扰

    try:
        violations, _stats = run_checks(cwd)
    except Exception:  # noqa: BLE001
        return 0

    if not violations:
        return 0

    listed = "\n".join(
        "- [%(level)s] %(path)s — %(problem)s" % v
        for v in violations[:MAX_LISTED]
    )
    more = len(violations) - MAX_LISTED
    if more > 0:
        listed += "\n- …另有 %d 处" % more
    reason = (
        "GEB 回环未完成,两相不同构(%d 处违规):\n%s\n\n"
        "请执行正向回环:更新相关文件的 L3 头部 → 所属 FOLDER_INDEX.md → "
        "PROJECT_INDEX.md,直到检查通过再结束。"
        % (len(violations), listed)
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
