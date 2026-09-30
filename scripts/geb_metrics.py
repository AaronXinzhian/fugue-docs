#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, collections, datetime, hashlib, json, os, pathlib, subprocess, sys, uuid, geb_telemetry
[OUTPUT]: 提供绑定诊断、任务用量与阶段快照、收益简报和可复核对照
[POS]: fugue-docs 工具层-可追溯用量记录;无对照时节省量保持未知
[PROTOCOL]: 修改计量口径时更新 references/token-accounting.md 与指标测试
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from geb_telemetry import COUNTERS, codex_home, find_session, observe, session_snapshot


def now():
    return datetime.now(timezone.utc).isoformat()


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


def usage_delta(start, end):
    if not start or not end:
        return None, "missing_measurements"
    if not start.get("session_id") or start["session_id"] != end.get("session_id"):
        return None, "session_mismatch"
    if start.get("source") != end.get("source"):
        return None, "source_rotated_unverified"
    if (not start.get("model") or start["model"] != end.get("model")
            or start.get("model_epoch") != end.get("model_epoch")):
        return None, "model_changed_or_unknown"
    if start.get("counter_epoch") != end.get("counter_epoch"):
        return None, "counter_reset"
    if start.get("timestamp") and start["timestamp"] == end.get("timestamp"):
        return None, "no_new_telemetry"
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


def start_run(root, task, ledger, session=None, condition="fugue", experiment=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("project root does not exist")
    settings = read_json(experiment) if experiment else None
    if settings is not None and (not isinstance(settings, dict) or not settings):
        raise ValueError("experiment settings must be a nonempty JSON object")
    binding, snapshot = observe(session=session)
    if snapshot and snapshot.get("cwd") and Path(snapshot["cwd"]).resolve() != root:
        raise ValueError("session cwd differs from project; provide the correct session")
    for existing_path in Path(ledger).glob("*.json"):
        existing = read_json(existing_path)
        existing_id = existing.get("session_id") or (existing.get("start") or {}).get("session_id")
        if (existing.get("status") == "active" and binding.get("session_id")
                and existing_id == binding["session_id"]):
            if existing["root"] == str(root) and existing["task"] == task and existing["condition"] == condition:
                if existing.get("experiment") != settings:
                    raise ValueError("active run has different experiment settings")
                return existing
            raise ValueError("finish the active run first: " + existing["run_id"])
    run_id = str(uuid.uuid4())
    record = {"schema": "geb.metrics.v2", "run_id": run_id, "root": str(root),
              "task": task, "condition": condition, "started_at": now(),
              "git": git_state(root), "status": "active", "start": snapshot,
              "session_source": binding.get("path"), "session_id": binding.get("session_id"),
              "binding": binding, "explicit_session": bool(session),
              "measurement_ready": snapshot is not None, "checkpoints": [],
              "experiment": settings,
              "end": None, "usage": None, "saved_tokens": None,
              "saving_status": "no_comparable_baseline"}
    save_json(record_path(ledger, run_id), record, exclusive=True)
    return record


def current_observation(record):
    session_id = record.get("session_id") or (record.get("start") or {}).get("session_id")
    if record.get("explicit_session", "binding" not in record) or not session_id:
        return observe(session=record.get("session_source")) if record.get("session_source") else (
            {"status": "missing_original_session_binding", "path": None}, None)
    return observe(session_id=session_id)


def checkpoint_run(ledger, run_id, phase):
    path = record_path(ledger, run_id)
    record = read_json(path)
    if record["status"] != "active":
        raise ValueError("checkpoint requires an active run")
    binding, snapshot = current_observation(record)
    previous = (record.get("checkpoints") or [{"snapshot": record["start"]}])[-1]["snapshot"]
    usage, status = usage_delta(previous, snapshot)
    checkpoint = {"phase": phase, "observed_at": now(), "snapshot": snapshot,
                  "usage": usage, "status": status, "binding": binding}
    record.setdefault("checkpoints", []).append(checkpoint)
    save_json(path, record)
    return checkpoint


def finish_run(ledger, run_id, outcome="unreviewed", evidence=None):
    path = record_path(ledger, run_id)
    record = read_json(path)
    if record["status"] != "active":
        return record
    if outcome != "unreviewed" and not evidence:
        raise ValueError("an outcome requires a validation evidence file")
    validation = {"outcome": outcome, "kind": "caller_reported_not_independent_review"}
    if evidence:
        data = Path(evidence).read_bytes()
        validation.update(evidence=str(Path(evidence).resolve()), sha256=hashlib.sha256(data).hexdigest())
    record["end_binding"], record["end"] = current_observation(record)
    record["usage"], record["status"] = usage_delta(record["start"], record["end"])
    record["finished_at"] = now()
    record["elapsed_seconds"] = round((datetime.fromisoformat(record["finished_at"]) -
                                       datetime.fromisoformat(record["started_at"])).total_seconds(), 3)
    record["validation"] = validation
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
    if not baseline.get("experiment") or baseline.get("experiment") != fugue.get("experiment"):
        reasons.append("matching_experiment_settings_required")
    for key in ("reasoning_effort", "model_provider"):
        if (baseline.get("start") or {}).get(key) != (fugue.get("start") or {}).get(key):
            reasons.append(key + "_mismatch")
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
            "saving_rate": difference / baseline["usage"]["total_tokens"] if baseline["usage"]["total_tokens"] else None,
            "kind": "paired_observed_difference_not_causal_proof", "created_at": now(),
            "quality_evidence": str(evidence_path), "quality_sha256": hashlib.sha256(data).hexdigest()}


def summarize(ledger, root=None):
    records = [read_json(path) for path in sorted(Path(ledger).glob("*.json"))]
    if root:
        records = [r for r in records if r.get("root") == str(Path(root).resolve())]
    runs = [r for r in records if r.get("schema") in ("geb.metrics.v1", "geb.metrics.v2")]
    comparisons = [r for r in records if r.get("schema") == "geb.comparison.v1"]
    measured = [r for r in runs if r.get("usage") is not None]
    paired = {r["fugue_run_id"] for r in comparisons}
    finished = [r for r in runs if r.get("status") != "active"]
    return {"runs": len(runs), "measured_runs": len(measured),
            "finished_runs": len(finished),
            "measurement_coverage": len(measured) / len(finished) if finished else None,
            "status_counts": dict(Counter(r["status"] for r in runs)),
            "actual_total_tokens": sum(r["usage"]["total_tokens"] for r in measured) if measured else None,
            "comparisons": len(comparisons),
            "paired_token_difference": sum(r["saved_tokens"] for r in comparisons) if comparisons else None,
            "fugue_runs_without_baseline": sum(r["condition"] == "fugue" and r["run_id"] not in paired for r in runs),
            "active_runs": [r["run_id"] for r in runs if r["status"] == "active"]}


def receipt(ledger, run_id):
    record = read_json(record_path(ledger, run_id))
    usage = record.get("usage")
    fmt = lambda value: "未知" if value is None else format(value, ",")
    lines = ["赋格任务收益单 | " + record["task"],
             "计量状态: " + record["status"],
             "实际 token: " + (fmt(usage["total_tokens"]) if usage else "未知")]
    if usage:
        lines.append("输入 %s (其中缓存 %s) | 输出 %s" % (
            fmt(usage.get("input_tokens")), fmt(usage.get("cached_input_tokens")), fmt(usage.get("output_tokens"))))
        if usage.get("input_tokens"):
            lines.append("缓存输入占比: %.1f%% (非赋格节省率)" % (usage.get("cached_input_tokens", 0) / usage["input_tokens"] * 100))
    lines.append("记录区间耗时: %s 秒 (含等待,不等于模型运行时间)" % fmt(record.get("elapsed_seconds")))
    lines.append("验收: " + record.get("validation", {}).get("outcome", "unreviewed") + " (调用方记录)")
    overhead = [p for p in record.get("checkpoints", []) if p["phase"] == "documentation"]
    amount = sum(p["usage"]["total_tokens"] for p in overhead) if overhead and all(p.get("usage") for p in overhead) else None
    lines.append("文档维护阶段 token: " + fmt(amount) + " (需显式阶段边界,不代表全部技能开销)")
    comparison_path = Path(ledger) / ("comparison-" + run_id + ".json")
    if comparison_path.is_file():
        comparison = read_json(comparison_path)
        lines.append("配对观察差值: %s token (正数减少,负数增加;非普遍收益)" % fmt(comparison["saved_tokens"]))
    else:
        lines.append("净节省: 未知 (暂无合格对照,不是 0)")
    if not usage:
        lines.append("诊断: " + record.get("end_binding", record.get("binding", {})).get("status", "legacy_record"))
    lines.append("覆盖: 仅已记录快照区间,不含之后回复及未汇总的独立子会话。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Record observed token usage; unknown savings stay null")
    parser.add_argument("--ledger", default=str(default_ledger()))
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("root")
    start.add_argument("--task", required=True, help="Comparable task key, not a full prompt")
    start.add_argument("--session", help="Explicit Codex JSONL; otherwise use CODEX_THREAD_ID")
    start.add_argument("--condition", choices=("fugue", "baseline"), default="fugue")
    start.add_argument("--experiment", help="Shared experiment settings JSON, required for comparisons")
    finish = sub.add_parser("finish")
    finish.add_argument("run_id")
    finish.add_argument("--outcome", choices=("unreviewed", "passed", "failed", "partial"), default="unreviewed")
    finish.add_argument("--evidence", help="Local validation output; stores path and SHA-256, not content")
    finish.add_argument("--receipt", action="store_true")
    brief = sub.add_parser("receipt")
    brief.add_argument("run_id")
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("run_id")
    checkpoint.add_argument("--phase", choices=("locate", "implement", "documentation", "validate", "other"), required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--session")
    doctor.add_argument("--session-id")
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--fugue", required=True)
    compare.add_argument("--quality-evidence", required=True)
    report = sub.add_parser("report")
    report.add_argument("--root")
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start_run(args.root, args.task, args.ledger, args.session, args.condition, args.experiment)
            if not result.get("measurement_ready"):
                print("WARNING: token measurement unavailable: " + result["binding"]["status"], file=sys.stderr)
        elif args.command == "finish":
            result = finish_run(args.ledger, args.run_id, args.outcome, args.evidence)
            if args.receipt:
                print(receipt(args.ledger, args.run_id))
                return 0
        elif args.command == "checkpoint":
            result = checkpoint_run(args.ledger, args.run_id, args.phase)
        elif args.command == "receipt":
            print(receipt(args.ledger, args.run_id))
            return 0
        elif args.command == "doctor":
            binding, snapshot = observe(args.session, args.session_id)
            result = {"binding": binding, "measurement_ready": snapshot is not None,
                      "model": (snapshot or {}).get("model"), "timestamp": (snapshot or {}).get("timestamp")}
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
