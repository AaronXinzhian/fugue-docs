#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, datetime, hashlib, json, os, pathlib, subprocess, sys, uuid
[OUTPUT]: 提供任务 token 起止快照、对照差值与跨项目本地账本
[POS]: fugue-docs 工具层-可追溯用量记录;无对照时节省量保持未知
[PROTOCOL]: 修改计量口径时更新 references/token-accounting.md 与指标测试
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

COUNTERS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")


def now():
    return datetime.now(timezone.utc).isoformat()


def codex_home():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def default_ledger():
    return codex_home() / "fugue" / "metrics"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    else:
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(str(temporary), str(path))
        finally:
            if temporary.exists():
                temporary.unlink()


def find_session(session_id):
    if not session_id:
        return None
    uuid.UUID(session_id)
    matches = list((codex_home() / "sessions").glob("**/*" + session_id + "*.jsonl"))
    if len(matches) != 1:
        return None
    return matches[0]


def session_snapshot(path):
    """Read numeric telemetry only. No prompts or tool outputs enter the ledger."""
    if path is None or not Path(path).is_file():
        return None
    session_id, cwd, model, latest = None, None, None, None
    model_epoch, counter_epoch = 0, 0
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # Live session may end in an incomplete JSONL record.
            payload = event.get("payload", {})
            if event.get("type") == "session_meta":
                session_id, cwd = payload.get("id"), payload.get("cwd")
            if event.get("type") == "turn_context":
                new_model = payload.get("model", model)
                if model and new_model != model:
                    model_epoch += 1
                model = new_model
            if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            usage = (payload.get("info") or {}).get("total_token_usage")
            if not usage or not all(isinstance(usage.get(k), int) and not isinstance(usage[k], bool)
                                    and usage[k] >= 0 for k in COUNTERS):
                continue
            if latest and usage["total_tokens"] < latest["usage"]["total_tokens"]:
                counter_epoch += 1
            latest = {"session_id": session_id, "cwd": cwd, "model": model,
                      "model_epoch": model_epoch, "counter_epoch": counter_epoch,
                      "timestamp": event.get("timestamp"), "source": str(Path(path).resolve()),
                      "usage": {k: usage[k] for k in COUNTERS}}
    return latest


def usage_delta(start, end):
    if not start or not end:
        return None, "missing_measurements"
    if not start.get("session_id") or start["session_id"] != end.get("session_id"):
        return None, "session_mismatch"
    if (not start.get("model") or start["model"] != end.get("model")
            or start.get("model_epoch") != end.get("model_epoch")):
        return None, "model_changed_or_unknown"
    if start.get("counter_epoch") != end.get("counter_epoch"):
        return None, "counter_reset"
    delta = {k: end["usage"][k] - start["usage"][k] for k in COUNTERS}
    if any(value < 0 for value in delta.values()):
        return None, "counter_reset"
    if delta["cached_input_tokens"] > delta["input_tokens"]:
        return None, "invalid_cache_counters"
    delta["uncached_input_tokens"] = delta["input_tokens"] - delta["cached_input_tokens"]
    return delta, "measured_interval"


def git_state(root):
    def run(args):
        r = subprocess.run(["git", "-C", str(root)] + args, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return {"commit": run(["rev-parse", "HEAD"]), "dirty": bool(run(["status", "--porcelain"]))}


def record_path(ledger, run_id):
    uuid.UUID(run_id)
    return Path(ledger) / (run_id + ".json")


def start_run(root, task, ledger, session=None, condition="fugue"):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("project root does not exist")
    path = Path(session) if session else find_session(os.environ.get("CODEX_THREAD_ID"))
    snapshot = session_snapshot(path)
    if snapshot and snapshot.get("cwd") and Path(snapshot["cwd"]).resolve() != root:
        raise ValueError("session cwd differs from project; provide the correct session")
    for existing_path in Path(ledger).glob("*.json"):
        existing = read_json(existing_path)
        if (existing.get("status") == "active" and snapshot
                and (existing.get("start") or {}).get("session_id") == snapshot["session_id"]):
            if existing["root"] == str(root) and existing["task"] == task and existing["condition"] == condition:
                return existing
            raise ValueError("finish the active run first: " + existing["run_id"])
    run_id = str(uuid.uuid4())
    record = {"schema": "geb.metrics.v1", "run_id": run_id, "root": str(root),
              "task": task, "condition": condition, "started_at": now(),
              "git": git_state(root), "status": "active", "start": snapshot,
              "session_source": str(path) if path else None,
              "end": None, "usage": None, "saved_tokens": None,
              "saving_status": "no_comparable_baseline"}
    save_json(record_path(ledger, run_id), record, exclusive=True)
    return record


def finish_run(ledger, run_id):
    path = record_path(ledger, run_id)
    record = read_json(path)
    if record["status"] != "active":
        return record
    record["end"] = session_snapshot(record.get("session_source"))
    record["usage"], record["status"] = usage_delta(record["start"], record["end"])
    record["finished_at"] = now()
    record["coverage"] = "start-to-finish telemetry interval; excludes later messages and unmerged subagent sessions"
    save_json(path, record)
    return record


def compare_records(baseline, fugue, evidence):
    reasons = []
    if baseline.get("condition") != "baseline" or fugue.get("condition") != "fugue":
        reasons.append("conditions_must_be_baseline_and_fugue")
    if not baseline.get("task") or baseline.get("task") != fugue.get("task"):
        reasons.append("task_mismatch")
    for record in (baseline, fugue):
        if record.get("status") != "measured_interval" or record.get("usage") is None:
            reasons.append("missing_measured_interval")
        if not record.get("git", {}).get("commit") or record["git"].get("dirty"):
            reasons.append("baseline_revision_unverified")
    if baseline.get("git", {}).get("commit") != fugue.get("git", {}).get("commit"):
        reasons.append("revision_mismatch")
    if (baseline.get("start") or {}).get("model") != (fugue.get("start") or {}).get("model"):
        reasons.append("model_mismatch")
    if (baseline.get("start") or {}).get("session_id") == (fugue.get("start") or {}).get("session_id"):
        reasons.append("independent_sessions_required")
    if not evidence or not Path(evidence).is_file():
        reasons.append("quality_evidence_required")
    if reasons:
        raise ValueError(", ".join(sorted(set(reasons))))
    evidence_path = Path(evidence).resolve()
    data = evidence_path.read_bytes()
    review = json.loads(data)
    if (review.get("baseline_run_id") != baseline["run_id"]
            or review.get("fugue_run_id") != fugue["run_id"]
            or review.get("quality_passed") is not True
            or not review.get("reviewer") or not review.get("method")):
        raise ValueError("quality evidence must bind both run ids, reviewer, method and quality_passed=true")
    difference = baseline["usage"]["total_tokens"] - fugue["usage"]["total_tokens"]
    return {"schema": "geb.comparison.v1", "baseline_run_id": baseline["run_id"],
            "fugue_run_id": fugue["run_id"], "task": fugue["task"], "root": fugue["root"],
            "saved_tokens": difference, "baseline_tokens": baseline["usage"]["total_tokens"],
            "fugue_tokens": fugue["usage"]["total_tokens"],
            "kind": "paired_observed_difference_not_causal_proof", "created_at": now(),
            "quality_evidence": str(evidence_path), "quality_sha256": hashlib.sha256(data).hexdigest()}


def summarize(ledger, root=None):
    records = [read_json(path) for path in sorted(Path(ledger).glob("*.json"))]
    if root:
        records = [r for r in records if r.get("root") == str(Path(root).resolve())]
    runs = [r for r in records if r.get("schema") == "geb.metrics.v1"]
    comparisons = [r for r in records if r.get("schema") == "geb.comparison.v1"]
    measured = [r for r in runs if r.get("usage") is not None]
    paired = {r["fugue_run_id"] for r in comparisons}
    return {"runs": len(runs), "measured_runs": len(measured),
            "actual_total_tokens": sum(r["usage"]["total_tokens"] for r in measured) if measured else None,
            "comparisons": len(comparisons),
            "paired_token_difference": sum(r["saved_tokens"] for r in comparisons) if comparisons else None,
            "fugue_runs_without_baseline": sum(r["condition"] == "fugue" and r["run_id"] not in paired for r in runs),
            "active_runs": [r["run_id"] for r in runs if r["status"] == "active"]}


def main():
    parser = argparse.ArgumentParser(description="Record observed token usage; unknown savings stay null")
    parser.add_argument("--ledger", default=str(default_ledger()))
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("root")
    start.add_argument("--task", required=True, help="Comparable task key, not a full prompt")
    start.add_argument("--session", help="Explicit Codex JSONL; otherwise use CODEX_THREAD_ID")
    start.add_argument("--condition", choices=("fugue", "baseline"), default="fugue")
    finish = sub.add_parser("finish")
    finish.add_argument("run_id")
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--fugue", required=True)
    compare.add_argument("--quality-evidence", required=True)
    report = sub.add_parser("report")
    report.add_argument("--root")
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start_run(args.root, args.task, args.ledger, args.session, args.condition)
        elif args.command == "finish":
            result = finish_run(args.ledger, args.run_id)
        elif args.command == "compare":
            baseline = read_json(record_path(args.ledger, args.baseline))
            fugue = read_json(record_path(args.ledger, args.fugue))
            result = compare_records(baseline, fugue, args.quality_evidence)
            # A run contributes once, avoiding inflated aggregate savings on repeated comparisons.
            save_json(Path(args.ledger) / ("comparison-" + args.fugue + ".json"), result, exclusive=True)
        else:
            result = summarize(args.ledger, args.root)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
