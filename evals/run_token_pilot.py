#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, hashlib, io, json, os, pathlib, random, shutil, signal, statistics, subprocess, sys, tarfile, time, geb_telemetry
[OUTPUT]: 提供预算受限、隔离配置、重复配对的 Codex 增量 token 试点
[POS]: fugue-docs 评测包-真实仓库小任务试验;不宣称普遍节省
[PROTOCOL]: 改任务与验收时更新 token-pilot.md 并保留失败运行
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from geb_telemetry import session_identity, session_snapshot

TASKS = {
    "session-fallback": (
        "In scripts/geb_metrics.py start_run, when CODEX_THREAD_ID is absent or empty, use "
        "CODEX_SESSION_ID to discover the session. Keep CODEX_THREAD_ID precedence. "
        "Preserve explicit --session behavior. Add focused tests.",
        '''
from unittest.mock import patch
import os
seen = []
with patch.dict(os.environ, {"CODEX_THREAD_ID": "", "CODEX_SESSION_ID": "fallback"}):
    with patch.object(m, "find_session", side_effect=lambda value: seen.append(value)):
        m.start_run(root, "fallback", root / "ledger1")
assert seen == ["fallback"], seen
seen.clear()
with patch.dict(os.environ, {"CODEX_THREAD_ID": "primary", "CODEX_SESSION_ID": "fallback"}):
    with patch.object(m, "find_session", side_effect=lambda value: seen.append(value)):
        m.start_run(root, "primary", root / "ledger2")
assert seen == ["primary"], seen
seen.clear()
with patch.object(m, "find_session", side_effect=lambda value: seen.append(value)):
    m.start_run(root, "explicit", root / "ledger3", session=root / "absent.jsonl")
assert not seen
'''),
    "coverage-summary": (
        "Extend scripts/geb_metrics.py summarize with finished_runs (all runs except active), "
        "status_counts (counts by status), and measurement_coverage (measured runs divided by "
        "finished runs; null when no finished runs). Preserve existing fields. Add focused tests.",
        '''
ledger = root / "ledger"
for i, status in enumerate(["active", "missing_measurements", "measured_interval"]):
    m.save_json(ledger / (str(i) + ".json"), {"schema":"geb.metrics.v1", "run_id":str(i),
        "condition":"fugue", "status":status, "usage":{"total_tokens":10} if i == 2 else None})
r = m.summarize(ledger)
assert r["finished_runs"] == 2 and r["measurement_coverage"] == 0.5, r
assert r["status_counts"] == {"active":1,"missing_measurements":1,"measured_interval":1}, r
assert r["actual_total_tokens"] == 10
assert m.summarize(root / "empty")["measurement_coverage"] is None
'''),
    "comparison-rate": (
        "Extend scripts/geb_metrics.py compare_records with saving_rate = "
        "(baseline total_tokens - fugue total_tokens) / baseline total_tokens. "
        "For a zero baseline return null, preserve negative rates, and retain all existing "
        "quality/evidence checks. Add focused tests.",
        '''
base = {"schema":"geb.metrics.v1", "run_id":"base", "task":"same", "condition":"baseline",
        "root":str(root), "status":"measured_interval", "git":{"commit":"abc","dirty":False},
        "start":{"model":"test","session_id":"a"}, "usage":{"total_tokens":100}}
fugue = dict(base, run_id="fugue", condition="fugue", start={"model":"test","session_id":"b"},
             usage={"total_tokens":150})
evidence = root / "review.json"
m.save_json(evidence, {"baseline_run_id":"base","fugue_run_id":"fugue","quality_passed":True,
                       "reviewer":"pilot-validator","method":"acceptance assertions"})
assert m.compare_records(base, fugue, evidence)["saving_rate"] == -0.5
base["usage"]["total_tokens"] = 0
assert m.compare_records(base, fugue, evidence)["saving_rate"] is None
try:
    m.compare_records(base, fugue, None)
except ValueError:
    pass
else:
    raise AssertionError("missing quality evidence accepted")
'''),
    "finish-duration": (
        "Extend scripts/geb_metrics.py finish_run to record elapsed_seconds as the wall-clock "
        "difference between finished_at and started_at, rounded to 3 decimals. Preserve finish "
        "idempotence and unknown token usage. Do not call duration model runtime. Add focused tests.",
        '''
from unittest.mock import patch
record = m.start_run(root, "duration", root / "ledger", session=root / "missing.jsonl")
record["started_at"] = "2026-09-30T00:00:00+00:00"
m.save_json(m.record_path(root / "ledger", record["run_id"]), record)
with patch.object(m, "now", return_value="2026-09-30T00:01:02.345000+00:00"):
    result = m.finish_run(root / "ledger", record["run_id"])
assert result["elapsed_seconds"] == 62.345, result
assert result["usage"] is None
assert m.finish_run(root / "ledger", record["run_id"]) == result
'''),
}


def summarize_trials(trials):
    groups = {}
    for trial in trials:
        groups.setdefault((trial["task"], trial["repeat"]), {})[trial["condition"]] = trial
    pairs = []
    for (task, repeat), group in groups.items():
        a, b = group.get("baseline"), group.get("fugue")
        if not a or not b or not all(t["accepted"] and t.get("usage") for t in (a, b)):
            continue
        ta, tb = a["usage"]["total_tokens"], b["usage"]["total_tokens"]
        pairs.append({"task": task, "repeat": repeat, "baseline_tokens": ta,
                      "fugue_tokens": tb, "difference": ta - tb,
                      "saving_rate": (ta - tb) / ta if ta else None,
                      "elapsed_difference_seconds": a["elapsed_seconds"] - b["elapsed_seconds"]})
    known = [t for t in trials if t.get("usage")]
    rates = [p["saving_rate"] for p in pairs if p["saving_rate"] is not None]
    return {"attempted": len(trials), "accepted": sum(t["accepted"] for t in trials),
            "unknown_usage_trials": len(trials) - len(known), "qualified_pairs": len(pairs),
            "experiment_total_tokens": sum(t["usage"]["total_tokens"] for t in known) if known and len(known) == len(trials) else None,
            "known_total_tokens": sum(t["usage"]["total_tokens"] for t in known) if known else None,
            "paired_difference": sum(p["difference"] for p in pairs) if pairs else None,
            "median_pair_saving_rate": statistics.median(rates) if rates else None,
            "pair_saving_rate_range": [min(rates), max(rates)] if rates else None,
            "by_condition": {condition: {
                "attempted": sum(t["condition"] == condition for t in trials),
                "accepted": sum(t["condition"] == condition and t["accepted"] for t in trials),
                "measured_tokens": (sum(t["usage"]["total_tokens"] for t in known if t["condition"] == condition)
                                    if any(t["condition"] == condition for t in known) else None)
            } for condition in ("baseline", "fugue")},
            "pairs": pairs}


def cli_usage(events):
    usages = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
    if not usages:
        return None
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(not isinstance(u, dict) or any(not isinstance(u.get(k), int) or isinstance(u[k], bool)
                                        or u[k] < 0 for k in keys) for u in usages):
        return None
    result = {k: sum(u[k] for u in usages) for k in keys}
    if result["cached_input_tokens"] > result["input_tokens"]:
        return None
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    result["uncached_input_tokens"] = result["input_tokens"] - result["cached_input_tokens"]
    return result


def partial_usage(home, events):
    ids = {e.get("thread_id") for e in events if e.get("type") == "thread.started" and e.get("thread_id")}
    if len(ids) != 1:
        return None
    session_id = ids.pop()
    candidates = [p for p in (home / "sessions").glob("**/*.jsonl")
                  if (session_identity(p) or {}).get("session_id") == session_id]
    if len(candidates) != 1:
        return None
    try:
        snapshot = session_snapshot(candidates[0])
    except (OSError, ValueError):
        return None
    return snapshot["usage"] if snapshot else None


def validate(workspace, task, evidence_dir):
    preamble = ("import sys, tempfile\nfrom pathlib import Path\n"
                "sys.path.insert(0, str(Path.cwd() / 'scripts'))\nimport geb_metrics as m\n"
                "tmp = tempfile.TemporaryDirectory()\nroot = Path(tmp.name)\n")
    commands = [[sys.executable, "-B", "-c", preamble + TASKS[task][1]],
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "evals", "-p", "test_*.py"]]
    results = []
    for index, command in enumerate(commands):
        try:
            run = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=60)
            log = run.stdout + run.stderr
            passed = run.returncode == 0
        except subprocess.TimeoutExpired:
            log, passed = "validation timed out", False
        path = evidence_dir / ("validation-%d.txt" % index)
        path.write_text(log, encoding="utf-8")
        results.append({"check": "task_acceptance" if index == 0 else "existing_and_added_tests",
                        "passed": passed, "sha256": hashlib.sha256(log.encode()).hexdigest()})
    return results


def trial(args, archive, skill_dir, task, repeat, condition, parent):
    trial_id = "%s-%d-%s" % (task, repeat, condition)
    directory = parent / trial_id
    directory.mkdir()
    workspace = directory / "workspace"
    workspace.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # The source is our own git archive; refuse traversal and links regardless.
        for member in tar.getmembers():
            if member.issym() or member.islnk() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                raise ValueError("unsafe archive member")
        tar.extractall(workspace)
    home = directory / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    auth = Path(args.auth_home).expanduser() / "auth.json"
    if auth.is_file():
        (codex_home / "auth.json").symlink_to(auth.resolve())
    if condition == "fugue":
        target = home / ".agents" / "skills" / "fugue-docs"
        target.parent.mkdir(parents=True)
        shutil.copytree(skill_dir, target)
        (codex_home / "AGENTS.md").write_text(
            "For this development task, use the fugue-docs skill at " + str(target / "SKILL.md") + ".\n", encoding="utf-8")
    original_tests = {p.relative_to(workspace).as_posix(): p.read_bytes()
                      for p in (workspace / "evals").glob("test_*.py")}
    prompt = (TASKS[task][0] + "\nKeep the change scoped, preserve existing behavior, and run relevant tests. "
              "Both source code and existing project documentation are available to you. "
              "Do not use the network, publish, commit, or read outside this workspace and your isolated home. "
              "Do not change pre-existing test files; add tests in a new file. "
              "Stop after the change and give a concise result.\n")
    if condition == "baseline":
        prompt += "Use your normal coding workflow without invoking the Fugue skill or its automation. Existing documentation remains available.\n"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    env.update(HOME=str(home), CODEX_HOME=str(codex_home), PYTHONDONTWRITEBYTECODE="1")
    git_env = dict(env, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="Fugue Pilot", GIT_COMMITTER_NAME="Fugue Pilot",
                   GIT_AUTHOR_EMAIL="pilot@example.invalid", GIT_COMMITTER_EMAIL="pilot@example.invalid",
                   GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")
    for command in (["git", "-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q"],
                    ["git", "add", "."], ["git", "-c", "core.hooksPath=" + os.devnull,
                                            "-c", "commit.gpgsign=false", "commit", "-qm", "Fixed pilot snapshot"]):
        subprocess.run(command, cwd=workspace, env=git_env, check=True, capture_output=True)
    base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace, env=git_env, text=True).strip()
    command = [args.codex, "exec", "--json", "--ignore-user-config",
               "--skip-git-repo-check", "--sandbox", "workspace-write", "--color", "never",
               "-m", args.model, "-c", 'model_reasoning_effort="' + args.effort + '"',
               "-c", 'approval_policy="never"', "-C", str(workspace), "-"]
    started = time.monotonic()
    with (directory / "events.jsonl").open("w") as out, (directory / "stderr.log").open("w") as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err, env=env,
                                   cwd=directory, text=True, start_new_session=True)
        try:
            process.communicate(prompt, timeout=args.timeout)
            status = "completed" if process.returncode == 0 else "runner_failed"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            status = "timeout"
    elapsed = round(time.monotonic() - started, 3)
    auth_link = codex_home / "auth.json"
    if auth_link.is_symlink() or auth_link.is_file():
        auth_link.unlink()
    events = []
    for line in (directory / "events.jsonl").read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    usage = cli_usage(events)
    partial = partial_usage(codex_home, events) if usage is None else None
    checks = validate(workspace, task, directory) if status == "completed" else []
    if checks:
        checks.append({"check": "original_tests_preserved", "passed": all(
            (workspace / name).is_file() and (workspace / name).read_bytes() == content
            for name, content in original_tests.items())})
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=workspace, env=git_env)
    (directory / "changes.patch").write_bytes(diff)
    return {"task": task, "repeat": repeat, "condition": condition, "status": status,
            "base_commit": base_commit, "changes_sha256": hashlib.sha256(diff).hexdigest(),
            "usage": usage, "elapsed_seconds": elapsed, "validation": checks,
            "partial_usage": partial,
            "usage_coverage": "complete_cli_turn" if usage else "incomplete_do_not_use_for_savings",
            "accepted": status == "completed" and bool(checks) and all(c["passed"] for c in checks),
            "events_sha256": hashlib.sha256((directory / "events.jsonl").read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description="Bounded incremental-skill token pilot, not a universal benchmark")
    parser.add_argument("--ref", required=True, help="Fixed source revision containing the original metrics API")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--tasks", nargs="+", choices=list(TASKS), default=list(TASKS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-total-tokens", type=int, default=300000)
    parser.add_argument("--seed", type=int, default=730)
    parser.add_argument("--auth-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--output", required=True, help="Local private output directory; raw logs are not public results")
    parser.add_argument("--execute", action="store_true", help="Without this, print the plan without model calls")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3 or args.timeout <= 0 or args.max_total_tokens <= 0:
        parser.error("require 1..3 repeats and positive budgets")
    revision = subprocess.check_output(["git", "rev-parse", args.ref], cwd=ROOT, text=True).strip()
    schedule = [(task, repeat, condition) for task in args.tasks for repeat in range(1, args.repeats + 1)
                for condition in ("baseline", "fugue")]
    random.Random(args.seed).shuffle(schedule)
    report = {"schema": "geb.token-pilot.v1", "source_commit": revision, "model": args.model,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "session_persistence": "isolated_codex_home",
              "effort": args.effort, "seed": args.seed, "planned_trials": len(schedule),
              "scope": "warm existing indexes; incremental skill workflow on four small repository tasks",
              "initialization_cost": None, "initialization_note": "not measured; both arms start with existing indexes",
              "token_budget": args.max_total_tokens, "timeout_per_trial": args.timeout,
              "budget_note": "token ceiling checked between trials, may overshoot once; timeout enforced per trial",
              "quality_note": "same deterministic acceptance and regression tests; not independent semantic review",
              "evidence_level": "test_passed_pairs_only_pending_independent_semantic_review",
              "trials": []}
    if not args.execute:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    archive = subprocess.check_output(["git", "archive", revision], cwd=ROOT)
    skill_dir = output / "skill"
    skill_dir.mkdir()
    for name in ("SKILL.md", "scripts", "references", "adapters", "agents"):
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, skill_dir / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, skill_dir / name)
    report["source_archive_sha256"] = hashlib.sha256(archive).hexdigest()
    report["skill_sha256"] = hashlib.sha256(b"".join(p.relative_to(skill_dir).as_posix().encode() + b"\0" +
        p.read_bytes() for p in sorted(skill_dir.rglob("*")) if p.is_file())).hexdigest()
    for task, repeat, condition in schedule:
        result = trial(args, archive, skill_dir, task, repeat, condition, output)
        report["trials"].append(result)
        report["summary"] = summarize_trials(report["trials"])
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result), flush=True)
        if result["usage"] is None or report["summary"]["experiment_total_tokens"] >= args.max_total_tokens:
            report["stop_reason"] = "unknown_usage" if result["usage"] is None else "token_budget"
            break
    else:
        report["stop_reason"] = "schedule_complete"
    report["summary"] = summarize_trials(report["trials"])
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"stop_reason": report["stop_reason"], "summary": report["summary"]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
