#!/usr/bin/env python3
"""
[INPUT]: 依赖 json, os, re, shutil, subprocess, sys, tempfile
[OUTPUT]: 为 iteration 目录下每个 run 生成 grading.json(expectations: text/passed/evidence)
[POS]: fugue-docs 评测包-自动评分器(对 iteration 目录下的 with/without 运行逐断言打分)
[PROTOCOL]: 断言变更时同步更新 evals/evals.json 与各 eval_metadata.json
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

WS = os.path.dirname(os.path.abspath(__file__))
GEB_CHECK = os.path.join(WS, "..", "scripts", "geb_check.py")

INDEX_NAMES = ("FOLDER_INDEX.md", "CLAUDE.md", "INDEX.md", "README.md")
L3_TAGS = ("[INPUT]", "[OUTPUT]", "[POS]")


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def head_lines(path, n=50):
    text = read(path)
    return "\n".join(text.splitlines()[:n]) if text is not None else ""


def first_docstring(path):
    """返回 Python 文件开头的 docstring 内容(无则空串)。"""
    text = read(path) or ""
    m = re.match(r'\s*(?:#[^\n]*\n)*\s*("""|\'\'\')(.*?)\1', text, re.S)
    return m.group(2) if m else ""


def find_index(dirpath):
    for name in INDEX_NAMES:
        p = os.path.join(dirpath, name)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


def run_geb_check(project):
    try:
        out = subprocess.run(
            [sys.executable, GEB_CHECK, project, "--json"],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout)
        return data["stats"]["violations"], data["violations"]
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def run_app(project, commands):
    """在临时副本中依次执行 app.py 命令,返回 (ok, transcript, tmpdir)。"""
    tmp = tempfile.mkdtemp(prefix="geb-grade-")
    proj_copy = os.path.join(tmp, "project")
    shutil.copytree(project, proj_copy)
    transcript = []
    ok = True
    for cmd in commands:
        r = subprocess.run(
            [sys.executable, "app.py"] + cmd,
            cwd=proj_copy, capture_output=True, text=True, timeout=30,
        )
        transcript.append("$ app.py %s -> exit %d\n%s%s"
                          % (" ".join(cmd), r.returncode, r.stdout, r.stderr))
        if r.returncode != 0:
            ok = False
    return ok, "\n".join(transcript), proj_copy


def expectation(text, passed, evidence):
    return {"text": text, "passed": bool(passed), "evidence": str(evidence)[:500]}


# ---------------- eval graders ----------------

def grade_eval0(project, assertions):
    exp = []
    # 1. L1
    l1 = None
    for name in ("PROJECT_INDEX.md", "CLAUDE.md"):
        p = os.path.join(project, name)
        if os.path.isfile(p) and os.path.getsize(p) > 200:
            l1 = p
            break
    l1_text = read(l1) or "" if l1 else ""
    ok1 = bool(l1) and "src" in l1_text
    exp.append(expectation(assertions[0], ok1,
                           "L1=%s, 长度=%d" % (l1 and os.path.basename(l1), len(l1_text))))
    # 2. L2 覆盖
    dir_files = {
        "src": ["index.js"],
        "src/routes": ["auth.js", "users.js"],
        "src/models": ["user.js"],
        "src/utils": ["hash.js"],
    }
    missing = []
    for d, files in dir_files.items():
        idx = find_index(os.path.join(project, d))
        text = read(idx) or "" if idx else ""
        if not idx:
            missing.append("%s: 无索引文件" % d)
        else:
            absent = [f for f in files if f not in text]
            if absent:
                missing.append("%s(%s): 未列出 %s"
                               % (d, os.path.basename(idx), ",".join(absent)))
    exp.append(expectation(assertions[1], not missing, missing or "4 个目录全部有索引且列全"))
    # 3. L3 覆盖
    js_files = ["src/index.js", "src/routes/auth.js", "src/routes/users.js",
                "src/models/user.js", "src/utils/hash.js"]
    bad = []
    for f in js_files:
        head = head_lines(os.path.join(project, f))
        lack = [t for t in L3_TAGS if t not in head]
        if lack:
            bad.append("%s 缺 %s" % (f, ",".join(lack)))
    exp.append(expectation(assertions[2], not bad, bad or "5 个文件均有完整 L3 头"))
    # 4. 自指声明
    all_index_text = l1_text
    for d in dir_files:
        idx = find_index(os.path.join(project, d))
        if idx:
            all_index_text += read(idx) or ""
    self_ref = any(k in all_index_text for k in
                   ("更新本文件", "更新我", "必须更新", "update this file", "[PROTOCOL]"))
    exp.append(expectation(assertions[3], self_ref, "自指关键词检索"))
    # 5. 代码未破坏
    markers = {
        "src/index.js": "app.listen",
        "src/routes/auth.js": "module.exports = router;",
        "src/routes/users.js": "module.exports = router;",
        "src/models/user.js": "module.exports = { createUser, findByEmail, findById, listUsers, deleteUser };",
        "src/utils/hash.js": "module.exports = { hashPassword, verifyPassword };",
    }
    broken = [f for f, m in markers.items()
              if m not in (read(os.path.join(project, f)) or "")]
    exp.append(expectation(assertions[4], not broken, broken or "导出标记全部完好"))
    return exp


def grade_eval1(project, assertions):
    exp = []
    svc = os.path.join(project, "services", "export_service.py")
    svc_text = read(svc) or ""
    head = head_lines(svc)
    ok1 = ("def export_users_to_csv" in svc_text
           and all(t in head for t in L3_TAGS))
    exp.append(expectation(assertions[0], ok1,
                           "存在=%s, 函数=%s, L3头=%s"
                           % (bool(svc_text), "def export_users_to_csv" in svc_text,
                              [t for t in L3_TAGS if t in head])))
    l2 = read(os.path.join(project, "services", "FOLDER_INDEX.md")) or ""
    exp.append(expectation(assertions[1], "export_service.py" in l2,
                           "FOLDER_INDEX.md 中检索 export_service.py"))
    app_doc = first_docstring(os.path.join(project, "app.py"))
    exp.append(expectation(assertions[2], "export" in app_doc.lower(),
                           "app.py 头部 docstring: %r" % app_doc[:200]))
    n, detail = run_geb_check(project)
    exp.append(expectation(assertions[3], n == 0, "违规数=%s %s" % (n, detail if n else "")))
    try:
        ok, transcript, proj_copy = run_app(project, [
            ["add", "Alice", "alice@example.com"],
            ["add", "Bob", "bob@example.com"],
            ["export", "out.csv"],
        ])
        csv_text = read(os.path.join(proj_copy, "out.csv")) or ""
        lines = [l for l in csv_text.strip().splitlines() if l.strip()]
        func_ok = (ok and lines and "id" in lines[0] and "name" in lines[0]
                   and "email" in lines[0] and len(lines) >= 3)
        evidence = "csv=%r" % csv_text[:200] if csv_text else transcript[-400:]
    except Exception as e:  # noqa: BLE001
        func_ok, evidence = False, repr(e)
    exp.append(expectation(assertions[4], func_ok, evidence))
    return exp


def grade_eval2(project, assertions):
    exp = []
    legacy = os.path.join(project, "services", "legacy_format.py")
    exp.append(expectation(assertions[0], not os.path.exists(legacy),
                           "exists=%s" % os.path.exists(legacy)))
    utils_text = read(os.path.join(project, "utils.py")) or ""
    utils_doc = first_docstring(os.path.join(project, "utils.py"))
    ok2 = "def format_date" in utils_text and "format_date" in utils_doc
    exp.append(expectation(assertions[1], ok2,
                           "def=%s, 头部提及=%s"
                           % ("def format_date" in utils_text, "format_date" in utils_doc)))
    report_text = read(os.path.join(project, "services", "report.py")) or ""
    report_doc = first_docstring(os.path.join(project, "services", "report.py"))
    ok3 = (re.search(r"from\s+utils\s+import\s+.*format_date", report_text)
           and "legacy_format" not in report_doc)
    exp.append(expectation(assertions[2], ok3,
                           "import=%s, 头部含legacy=%s"
                           % (bool(re.search(r'from\s+utils\s+import', report_text)),
                              "legacy_format" in report_doc)))
    l2 = read(os.path.join(project, "services", "FOLDER_INDEX.md")) or ""
    l1 = read(os.path.join(project, "PROJECT_INDEX.md")) or ""
    ok4 = "legacy_format" not in l2 and "legacy_format" not in l1
    exp.append(expectation(assertions[3], ok4,
                           "L2含=%s, L1含=%s"
                           % ("legacy_format" in l2, "legacy_format" in l1)))
    n, detail = run_geb_check(project)
    exp.append(expectation(assertions[4], n == 0, "违规数=%s %s" % (n, detail if n else "")))
    try:
        ok, transcript, _ = run_app(project, [
            ["add", "Alice", "alice@example.com"],
            ["report"],
        ])
        func_ok = ok and "Alice" in transcript
        evidence = transcript[-400:]
    except Exception as e:  # noqa: BLE001
        func_ok, evidence = False, repr(e)
    exp.append(expectation(assertions[5], func_ok, evidence))
    return exp


GRADERS = {
    "eval-0-init-legacy-js-project": grade_eval0,
    "eval-1-add-feature-loop": grade_eval1,
    "eval-2-delete-refactor-loop": grade_eval2,
}


def main():
    iteration = sys.argv[1] if len(sys.argv) > 1 else os.path.join(WS, "iteration-1")
    for eval_name, grader in GRADERS.items():
        eval_dir = os.path.join(iteration, eval_name)
        meta = json.loads(read(os.path.join(eval_dir, "eval_metadata.json")) or "{}")
        assertions = meta.get("assertions", [])
        if not assertions:
            print("%s: 跳过——缺少 %s/eval_metadata.json(assertions),"
                  "请按 README 从 evals.json 复制对应条目" % (eval_name, eval_name))
            continue
        for run in ("with_skill", "without_skill", "old_skill"):
            project = os.path.join(eval_dir, run, "outputs", "project")
            if not os.path.isdir(project):
                continue
            expectations = grader(project, assertions)
            passed = sum(1 for e in expectations if e["passed"])
            total = len(expectations)
            grading = {
                "expectations": expectations,
                "summary": {
                    "pass_rate": round(passed / total, 4) if total else 0.0,
                    "passed": passed,
                    "failed": total - passed,
                    "total": total,
                },
            }
            # 双写:config 层给 viewer 用,run-1 层给 aggregate_benchmark 用
            run_dir = os.path.join(eval_dir, run)
            os.makedirs(os.path.join(run_dir, "run-1"), exist_ok=True)
            for out_path in (os.path.join(run_dir, "grading.json"),
                             os.path.join(run_dir, "run-1", "grading.json")):
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(grading, f, ensure_ascii=False, indent=2)
            timing_src = os.path.join(run_dir, "timing.json")
            if os.path.isfile(timing_src):
                shutil.copy(timing_src, os.path.join(run_dir, "run-1", "timing.json"))
            print("%s/%s: %d/%d passed" % (eval_name, run, passed, total))


if __name__ == "__main__":
    main()
