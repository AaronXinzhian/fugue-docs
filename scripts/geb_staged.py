#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, json, os, subprocess, sys, tempfile, geb_check
[OUTPUT]: 提供暂存快照检查命令,退出码 0=通过或未采纳,1=违规,2=执行失败
[POS]: fugue-docs 工具层-Git 提交内容校验,不修改工作区与暂存区
[PROTOCOL]: 变更时同步 scripts/FOLDER_INDEX.md 与钩子测试
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

from geb_check import L1_NAMES, find_index_file, run_checks


def check_staged(root, strict=False, complete=False):
    repo = subprocess.run(["git", "-C", root, "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, timeout=15)
    if repo.returncode:
        raise ValueError(repo.stderr.strip() or "Not a Git repository")
    with tempfile.TemporaryDirectory(prefix="geb-staged-") as snapshot:
        result = subprocess.run(
            ["git", "-C", repo.stdout.strip(), "checkout-index", "--all",
             "--prefix=" + snapshot + os.sep], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Cannot export Git index")
        if not find_index_file(snapshot, L1_NAMES):
            return {"adopted": False, "violations": [], "stats": None}
        violations, stats = run_checks(snapshot, strict=strict, complete=complete)
        return {"adopted": True, "violations": violations, "stats": stats}


def main():
    parser = argparse.ArgumentParser(description="Check the actual staged Git snapshot")
    parser.add_argument("root")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    try:
        report = check_staged(args.root, args.strict, args.complete)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("GEB staged check failed: %s" % error, file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
