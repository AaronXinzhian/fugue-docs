#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, json, os, shutil, subprocess, sys, tempfile
[OUTPUT]: 提供 fugue-docs 确定性回归套件:多轮验证架构候选、同步、检查器、适配器、理解评分器与仓库自检
[POS]: fugue-docs 评测包-回归测试入口(面向发布前/外部复核的可复跑证据)
[PROTOCOL]: 测试项或输出结构变更时同步更新 evals/README.md、evals/FOLDER_INDEX.md 与测试结果文档
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

WS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(WS, ".."))


def run(cmd, cwd=ROOT, timeout=60):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def fail(name, detail):
    return {
        "name": name,
        "passed": False,
        "detail": str(detail)[-1000:],
    }


def ok(name, detail):
    return {
        "name": name,
        "passed": True,
        "detail": str(detail)[:1000],
    }


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def copy_fixture(name, tmp, label):
    src = os.path.join(WS, "fixtures", name)
    dst = os.path.join(tmp, "%s-%s" % (name, label))
    shutil.copytree(src, dst)
    return dst


def test_arch_fixture_b(tmp):
    project = copy_fixture("fixture-b", tmp, "arch")
    out = os.path.join(tmp, "arch.json")
    brief = os.path.join(tmp, "arch.md")
    r = run([sys.executable, os.path.join(ROOT, "scripts", "geb_arch.py"),
             project, "--out", out, "--brief", brief])
    require(r.returncode == 0, r.stderr or r.stdout)
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    require(data["schema"] == "geb.arch.v1", "schema mismatch")
    require(data["entrypoint_candidates"][0]["file"] == "app.py",
            "app.py should be top entrypoint")
    modules = {m["name"]: m for m in data["module_candidates"]}
    require({"root", "services", "storage"}.issubset(modules),
            "missing expected modules")
    require(modules["services"]["role_id"] == "app-facing",
            "services role should be app-facing")
    require(modules["storage"]["role_id"] == "persistence",
            "storage role should be persistence")
    edges = {(e["from"], e["to"]) for e in data["edges"]}
    require(("root", "services") in edges, "missing root->services edge")
    require(("services", "storage") in edges, "missing services->storage edge")
    require(any("legacy_format.py" in w for w in data["warnings"]),
            "missing legacy warning")
    require(os.path.getsize(brief) > 200, "brief too small")
    return ok("arch_fixture_b", "entrypoint=app.py modules=root/services/storage")


def test_sync_changed_delete(tmp):
    project = copy_fixture("fixture-b", tmp, "sync-delete")
    require(run(["git", "init", "-q"], cwd=project).returncode == 0, "git init")
    require(run(["git", "add", "."], cwd=project).returncode == 0, "git add")
    c = run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-q", "-m", "init"], cwd=project)
    require(c.returncode == 0, c.stderr)
    os.remove(os.path.join(project, "services", "legacy_format.py"))
    r = run([sys.executable, os.path.join(ROOT, "scripts", "geb_sync.py"),
             project, "--changed", "--dry-run"])
    require(r.returncode == 0, r.stderr)
    require("[L2] services/FOLDER_INDEX.md" in r.stdout,
            "changed sync should rebuild services L2 after deletion")
    return ok("sync_changed_delete", r.stdout.strip())


def test_check_l1_path_ghost(tmp):
    project = os.path.join(tmp, "ghost-project")
    os.makedirs(os.path.join(project, "services"))
    with open(os.path.join(project, "services", "a.py"), "w", encoding="utf-8") as f:
        f.write('"""\n[INPUT]: 依赖 (未检出外部依赖)\n[OUTPUT]: 提供 a()\n'
                '[POS]: 测试文件\n[PROTOCOL]: 变更时更新此头部\n"""\n'
                'def a():\n    return 1\n')
    with open(os.path.join(project, "PROJECT_INDEX.md"), "w", encoding="utf-8") as f:
        f.write("# test — 项目索引(L1)\n\n"
                "## 文件清单\n"
                "| 文件 | 职责 | 关键导出 |\n"
                "|------|------|----------|\n"
                "| services/a.py | 存在 | a() |\n"
                "| services/ghost.py | 幽灵 | ghost() |\n")
    r = run([sys.executable, os.path.join(ROOT, "scripts", "geb_check.py"),
             project, "--json"])
    require(r.returncode == 1, "geb_check should fail on ghost path")
    data = json.loads(r.stdout)
    require(any(v["path"] == "services/ghost.py" for v in data["violations"]),
            "missing services/ghost.py violation")
    return ok("check_l1_path_ghost", "detected services/ghost.py")


def test_adapt_copy_tools(tmp):
    project = os.path.join(tmp, "adapt-project")
    os.makedirs(project)
    r = run([sys.executable, os.path.join(ROOT, "scripts", "geb_adapt.py"),
             project, "--copy-tools"])
    require(r.returncode == 0, r.stderr)
    names = sorted(os.listdir(os.path.join(project, "scripts", "geb")))
    for name in ("geb_arch.py", "geb_check.py", "geb_scaffold.py", "geb_sync.py",
                 "FOLDER_INDEX.md"):
        require(name in names, "missing copied tool %s" % name)
    return ok("adapt_copy_tools", ", ".join(names))


def test_comprehension_grader(tmp):
    answers = {
        "runs": [
            {
                "condition": "docs_only",
                "token_count": 1000,
                "answers": {
                    "1": "命令行团队名册工具,通过 app.py 运行,本地 JSON 存储。",
                    "2": "改 user_service.py 和 app.py,读取经过 storage。",
                    "3": "只有 storage 层直接读写文件,services 不直接碰文件,这是全局约定。",
                    "4": "依赖 storage.store、legacy_format 的 format_date 和 time。",
                    "5": "report.py 还在用 format_date,先迁移并更新 import。",
                    "6": "utils.validate_email 校验,user_service.add_user 调用。"
                },
            },
            {
                "condition": "code_only",
                "token_count": 4000,
                "answers": {
                    "1": "命令行团队名册工具 app.py JSON",
                    "2": "user_service.py app.py storage",
                    "3": "storage, services 不直接操作文件,分层约定",
                    "4": "storage.store legacy_format format_date time",
                    "5": "report.py format_date import",
                    "6": "utils.validate_email add_user"
                },
            },
        ]
    }
    path = os.path.join(tmp, "answers.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False)
    r = run([sys.executable, os.path.join(WS, "grade_comprehension.py"), path])
    require(r.returncode == 0, r.stderr)
    data = json.loads(r.stdout)
    require(data["comparison"]["healthy"], "comparison should be healthy")
    return ok("comprehension_grader",
              "score_ratio=%s token_ratio=%s" % (
                  data["comparison"]["docs_vs_code_score_ratio"],
                  data["comparison"]["docs_vs_code_token_ratio"]))


def test_self_checks(_tmp):
    check = run([sys.executable, "-B", os.path.join(ROOT, "scripts", "geb_check.py"),
                 ROOT, "--strict", "--complete", "--report"], timeout=120)
    require(check.returncode == 0, check.stdout + check.stderr)
    sync = run([sys.executable, "-B", os.path.join(ROOT, "scripts", "geb_sync.py"),
                ROOT, "--dry-run", "--graph"], timeout=120)
    require(sync.returncode == 0, sync.stdout + sync.stderr)
    require("0 处" in sync.stdout, "sync dry-run should report 0 changes")
    return ok("self_checks", "geb_check strict complete + geb_sync dry-run")


TESTS = [
    test_arch_fixture_b,
    test_sync_changed_delete,
    test_check_l1_path_ghost,
    test_adapt_copy_tools,
    test_comprehension_grader,
    test_self_checks,
]


def run_round(round_no):
    results = []
    tmp = tempfile.mkdtemp(prefix="geb-regression-%d-" % round_no)
    try:
        for fn in TESTS:
            try:
                results.append(fn(tmp))
            except Exception as e:  # noqa: BLE001
                results.append(fail(fn.__name__.replace("test_", ""), repr(e)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {
        "round": round_no,
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results,
    }


def git_commit():
    r = run(["git", "rev-parse", "--short", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else ""


def main():
    parser = argparse.ArgumentParser(description="fugue-docs 确定性回归测试套件")
    parser.add_argument("--rounds", type=int, default=5,
                        help="重复轮数,默认 5")
    parser.add_argument("--out", help="写出 JSON 报告")
    args = parser.parse_args()

    report = {
        "suite": "fugue-docs deterministic regression suite",
        "rounds_requested": args.rounds,
        "repo": ROOT,
        "commit": git_commit(),
        "tests": [fn.__name__.replace("test_", "") for fn in TESTS],
        "rounds": [run_round(i + 1) for i in range(args.rounds)],
    }
    total = sum(len(r["results"]) for r in report["rounds"])
    failed = sum(r["failed"] for r in report["rounds"])
    report["summary"] = {
        "total_assertion_groups": total,
        "passed": total - failed,
        "failed": failed,
        "pass_rate": round((total - failed) / total, 4) if total else 0.0,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
