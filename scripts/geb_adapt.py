#!/usr/bin/env python3
"""
[INPUT]: 依赖 Python3 标准库 (argparse, os, re, shutil, stat, sys); ../adapters/PROTOCOL*.md; 同目录 geb_check.py、geb_scaffold.py
[OUTPUT]: 提供一键适配命令——把 GEB 协议注入各 AI 工具的规则文件,并可安装 pre-commit / CI 硬约束
[POS]: fugue-docs 工具层-通用性适配器(让 Codex/Cursor/Cline/Copilot/任意模型用户一条命令接入)
[PROTOCOL]: 变更时更新此头部,然后检查 SKILL.md 与 README 中对本脚本的描述

设计:adapters/PROTOCOL.md 是协议的单一事实来源,本脚本只做"注入与装订"。
注入使用 BEGIN/END 标记,幂等——重复运行会原地更新标记之间的内容,不破坏
规则文件里用户自己的其他内容。

用法示例:
  python3 geb_adapt.py /path/to/project --tool cursor codex
  python3 geb_adapt.py /path/to/project --tool all --lang en
  python3 geb_adapt.py /path/to/project --pre-commit --ci
"""

import argparse
import os
import re
import shutil
import stat
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADAPTERS_DIR = os.path.join(SCRIPT_DIR, "..", "adapters")

BEGIN = "<!-- GEB-PROTOCOL BEGIN (managed by fugue-docs geb_adapt.py) -->"
END = "<!-- GEB-PROTOCOL END -->"

# 工具名 → 规则文件相对路径
TOOLS = {
    "codex": "AGENTS.md",                            # OpenAI Codex CLI(也被多家工具支持)
    "cursor": ".cursorrules",                        # Cursor
    "windsurf": ".windsurfrules",                    # Windsurf
    "cline": ".clinerules",                          # Cline / Roo Code(可接 DeepSeek 等任意模型)
    "copilot": os.path.join(".github", "copilot-instructions.md"),  # GitHub Copilot
    "claude": "CLAUDE.md",                           # Claude Code(未装 skill 时的轻量方案)
    "generic": "GEB_PROTOCOL.md",                    # 任意聊天模型:粘贴为系统提示词
}

PRE_COMMIT_SH = """#!/bin/sh
# GEB-MANAGED-HOOK v2(由 fugue-docs geb_adapt.py 安装;此标记用于安全更新,请勿删除)
# 跳过一次检查:git commit --no-verify
REPO_ROOT="$(git rev-parse --show-toplevel)" || exit 0
# 采纳判定与 geb_check 一致:PROJECT_INDEX.md,或含 GEB 标识的 CLAUDE.md
if [ ! -f "$REPO_ROOT/PROJECT_INDEX.md" ]; then
    grep -q -e "GEB" -e "FOLDER_INDEX" "$REPO_ROOT/CLAUDE.md" 2>/dev/null || exit 0
fi
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECKER="${GEB_CHECK:-$HOOK_DIR/geb_check.py}"
if ! python3 "$CHECKER" "$REPO_ROOT"; then
    echo "" >&2
    echo "GEB: 代码与文档两相不同构,提交被拒绝。请完成 L3->L2->L1 回环后再提交。" >&2
    exit 1
fi
"""

CI_YML = """name: GEB isomorphism check
# 由 fugue-docs geb_adapt.py 生成:两相不同构时使 CI 变红
on:
  push:
  pull_request:
jobs:
  geb-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run geb_check
        run: python3 scripts/geb/geb_check.py .
"""


def load_protocol(lang):
    name = "PROTOCOL.md" if lang == "zh" else "PROTOCOL_EN.md"
    path = os.path.join(ADAPTERS_DIR, name)
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def inject(target_path, protocol_text):
    """把协议写入目标规则文件的标记区间;无标记则追加;幂等。

    以 newline="" 读写,保持目标文件原有行尾(CRLF 文件不被静默改为 LF)。
    """
    block = "%s\n%s\n%s" % (BEGIN, protocol_text, END)
    if os.path.isfile(target_path):
        with open(target_path, encoding="utf-8", errors="replace", newline="") as f:
            existing = f.read()
        nl = "\r\n" if "\r\n" in existing else "\n"
        if nl != "\n":
            block = block.replace("\n", nl)
        if BEGIN in existing and END in existing:
            pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
            updated = pattern.sub(lambda _m: block, existing)
            action = "更新"
        else:
            sep = "" if existing.endswith(nl * 2) else (nl if existing.endswith(nl) else nl * 2)
            updated = existing + sep + block + nl
            action = "追加到"
    else:
        updated = block + "\n"
        action = "创建"
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    with open(target_path, "w", encoding="utf-8", newline="") as f:
        f.write(updated)
    return action


TOOLS_L2 = """# scripts/geb/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../../PROJECT_INDEX.md](../../PROJECT_INDEX.md)

## 模块定位
GEB 协议配套工具(由 fugue-docs geb_adapt.py 复制安装),供本项目的 CI 与本地检查使用,不参与业务逻辑。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| geb_check.py | 同构性检查器(L1/L2/L3 覆盖与台账对账) | 命令行工具,--json 供 CI |
| geb_scaffold.py | 确定性脚手架(静态分析生成 L3/L2/L1 骨架) | 命令行工具,--dry-run 预览 |
"""


def copy_tools(root):
    dest = os.path.join(root, "scripts", "geb")
    os.makedirs(dest, exist_ok=True)
    for name in ("geb_check.py", "geb_scaffold.py"):
        shutil.copy(os.path.join(SCRIPT_DIR, name), os.path.join(dest, name))
    # 工具自带 L2 索引——安装工具这件事本身不能破坏同构
    l2 = os.path.join(dest, "FOLDER_INDEX.md")
    if not os.path.isfile(l2):
        with open(l2, "w", encoding="utf-8") as f:
            f.write(TOOLS_L2)
    return dest


def install_pre_commit(root):
    hooks_dir = os.path.join(root, ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        return "跳过 pre-commit:%s 不是 git 仓库(先 git init)" % root
    # 自包含:检查器随钩子一起放进 .git/hooks/
    shutil.copy(os.path.join(SCRIPT_DIR, "geb_check.py"),
                os.path.join(hooks_dir, "geb_check.py"))
    target = os.path.join(hooks_dir, "pre-commit")
    if os.path.isfile(target):
        with open(target, encoding="utf-8", errors="replace") as f:
            existing = f.read()
        if "GEB-MANAGED-HOOK" in existing:
            note = "(更新托管版本)"
        else:
            # 不是本工具托管的钩子(可能是用户手装的完整模板或其他工具的钩子),
            # 绝不静默覆盖——写到旁路文件,由用户决定如何合并
            target = os.path.join(hooks_dir, "pre-commit.geb")
            note = "(已存在非托管 pre-commit,未覆盖;新钩子写入 pre-commit.geb,请自行合并)"
    else:
        note = ""
    with open(target, "w", encoding="utf-8") as f:
        f.write(PRE_COMMIT_SH)
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return "pre-commit 硬约束已安装:%s %s" % (os.path.relpath(target, root), note)


def install_ci(root):
    copy_tools(root)
    wf_dir = os.path.join(root, ".github", "workflows")
    os.makedirs(wf_dir, exist_ok=True)
    wf = os.path.join(wf_dir, "geb-check.yml")
    with open(wf, "w", encoding="utf-8") as f:
        f.write(CI_YML)
    return "CI 工作流已生成:.github/workflows/geb-check.yml(检查器已复制到 scripts/geb/)"


def main():
    parser = argparse.ArgumentParser(
        description="GEB 协议通用适配器:注入规则文件 / 安装硬约束")
    parser.add_argument("root", help="目标项目根目录")
    parser.add_argument("--tool", nargs="+", choices=sorted(TOOLS) + ["all"],
                        default=[], help="要适配的工具(可多选;all=全部)")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh",
                        help="注入协议的语言(默认中文)")
    parser.add_argument("--pre-commit", action="store_true",
                        help="安装 git pre-commit 硬约束(自包含,不依赖本仓库)")
    parser.add_argument("--ci", action="store_true",
                        help="生成 GitHub Actions 检查工作流,并复制工具到项目 scripts/geb/")
    parser.add_argument("--copy-tools", action="store_true",
                        help="把 geb_check.py / geb_scaffold.py 复制到项目 scripts/geb/")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出将写入的位置,不做任何修改")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误: %s 不是目录" % root, file=sys.stderr)
        return 2
    if not (args.tool or args.pre_commit or args.ci or args.copy_tools):
        parser.error("至少指定一个动作:--tool / --pre-commit / --ci / --copy-tools")

    tools = sorted(TOOLS) if "all" in args.tool else args.tool

    # 写入位置预告:本工具会修改目标项目的以下位置,先亮牌
    planned = [TOOLS[t] for t in tools]
    if args.pre_commit:
        planned.append(".git/hooks/pre-commit(+ geb_check.py 副本)")
    if args.ci:
        planned.append(".github/workflows/geb-check.yml")
    if args.ci or args.copy_tools:
        planned.append("scripts/geb/(geb_check.py、geb_scaffold.py、FOLDER_INDEX.md)")
    print("将写入 %s 下的:" % root)
    for p in planned:
        print("  - %s" % p)
    if args.dry_run:
        print("\n(dry-run:未做任何修改)")
        return 0
    print()

    if tools:
        protocol = load_protocol(args.lang)
        for t in tools:
            rel = TOOLS[t]
            action = inject(os.path.join(root, rel), protocol)
            print("[%s] %s %s" % (t, action, rel))
        if "generic" in tools:
            print("    generic:把 GEB_PROTOCOL.md 内容粘贴为系统提示词即可用于任意聊天模型")
    if args.copy_tools and not args.ci:
        print("工具已复制到 %s" % copy_tools(root))
    if args.pre_commit:
        print(install_pre_commit(root))
    if args.ci:
        print(install_ci(root))
    print("\n完成。建议初始化:python3 scripts/geb/geb_scaffold.py . && 让你的 AI 补全 TODO(语义)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
