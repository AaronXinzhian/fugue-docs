#!/usr/bin/env python3
"""
[INPUT]: 依赖 copy, contextlib, json, os, pathlib, sqlite3, sys, tempfile, unittest, unittest.mock, uuid, geb_metrics, geb_telemetry
[OUTPUT]: 提供 token 账本缺失值、计数重置、对照与重复记录测试
[POS]: fugue-docs 评测包-计量可信度回归
[PROTOCOL]: 变更时同步 evals/FOLDER_INDEX.md 与计量说明
"""

import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import geb_metrics as metrics
import geb_telemetry as telemetry


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="geb-metrics-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "ledger"

    def snapshot(self, total=110, session="one", model="model-a"):
        return {"session_id": session, "model": model, "model_epoch": 0,
                "counter_epoch": 0, "usage": {"input_tokens": total - 10,
                "cached_input_tokens": 30, "output_tokens": 10, "total_tokens": total}}

    def run_record(self, condition, total, run_id, session):
        return {"schema": "geb.metrics.v1", "run_id": run_id, "root": str(self.root),
                "condition": condition, "task": "same-task", "status": "measured_interval",
                "experiment": {"model": "model-a", "reasoning_effort": "low", "acceptance": "synthetic"},
                "git": {"commit": "abc", "dirty": False}, "start": self.snapshot(session=session),
                "usage": {"total_tokens": total}}

    def pair(self):
        baseline = self.run_record("baseline", 100, "base", "session-a")
        fugue = self.run_record("fugue", 60, "fugue", "session-b")
        evidence = self.root / "review.json"
        metrics.save_json(evidence, {"baseline_run_id": "base", "fugue_run_id": "fugue",
                                    "quality_passed": True, "reviewer": "test-reviewer",
                                    "method": "synthetic unit-test input, not a real benchmark"})
        return baseline, fugue, evidence

    def test_missing_usage_is_unknown(self):
        delta, status = metrics.usage_delta(None, self.snapshot())
        self.assertIsNone(delta)
        self.assertEqual("missing_measurements", status)

    def test_delta_does_not_double_count_cache(self):
        start, end = self.snapshot(110), self.snapshot(210)
        end["usage"]["cached_input_tokens"] = 80
        delta, status = metrics.usage_delta(start, end)
        self.assertEqual("measured_interval", status)
        self.assertEqual(100, delta["total_tokens"])
        self.assertEqual(50, delta["uncached_input_tokens"])

    def test_counter_reset_is_unknown(self):
        self.assertEqual((None, "counter_reset"), metrics.usage_delta(self.snapshot(210), self.snapshot(110)))

    def test_model_switch_then_back_is_unknown(self):
        start, end = self.snapshot(), self.snapshot(210)
        end["model_epoch"] = 2
        self.assertEqual((None, "model_changed_or_unknown"), metrics.usage_delta(start, end))

    def test_session_mismatch_rejected(self):
        self.assertEqual((None, "session_mismatch"), metrics.usage_delta(self.snapshot(), self.snapshot(session="two")))

    def test_comparison_keeps_negative_savings(self):
        baseline, fugue, evidence = self.pair()
        fugue["usage"]["total_tokens"] = 150
        result = metrics.compare_records(baseline, fugue, evidence)
        self.assertEqual(-50, result["saved_tokens"])

    def test_positive_comparison_and_evidence_hash(self):
        result = metrics.compare_records(*self.pair())
        self.assertEqual(40, result["saved_tokens"])
        self.assertEqual(64, len(result["quality_sha256"]))

    def test_comparison_rejects_unmatched_experiments(self):
        baseline, original, evidence = self.pair()
        for key, value in (("task", "other"), ("status", "active"),
                           ("git", {"commit": "different", "dirty": False}),
                           ("start", self.snapshot(model="other")),
                           ("start", self.snapshot(session="session-a"))):
            fugue = copy.deepcopy(original)
            fugue[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                metrics.compare_records(baseline, fugue, evidence)

    def test_missing_quality_review_rejected(self):
        baseline, fugue, _ = self.pair()
        with self.assertRaises(ValueError):
            metrics.compare_records(baseline, fugue, None)

    def test_wrong_evidence_binding_rejected(self):
        baseline, fugue, evidence = self.pair()
        review = metrics.read_json(evidence)
        review["fugue_run_id"] = "wrong-run"
        metrics.save_json(evidence, review)
        with self.assertRaises(ValueError):
            metrics.compare_records(baseline, fugue, evidence)

    def test_unknown_savings_not_zero_in_summary(self):
        record = metrics.start_run(self.root, "test", self.ledger, session=self.root / "missing.jsonl")
        self.assertIsNone(record["saved_tokens"])
        metrics.finish_run(self.ledger, record["run_id"])
        report = metrics.summarize(self.ledger)
        self.assertIsNone(report["paired_token_difference"])
        self.assertIsNone(report["actual_total_tokens"])
        self.assertEqual(1, report["fugue_runs_without_baseline"])

    def test_finish_is_idempotent(self):
        record = metrics.start_run(self.root, "test", self.ledger, session=self.root / "missing.jsonl")
        first = metrics.finish_run(self.ledger, record["run_id"])
        self.assertEqual(first, metrics.finish_run(self.ledger, record["run_id"]))

    def test_session_parser_ignores_prompt_content(self):
        path = self.root / "session.jsonl"
        events = [{"type": "session_meta", "payload": {"id": "session", "cwd": str(self.root)}},
                  {"type": "turn_context", "payload": {"model": "model-a"}},
                  {"type": "response_item", "payload": {"text": "DO NOT COPY PROMPT"}},
                  {"type": "event_msg", "timestamp": "test-time", "payload": {"type": "token_count",
                   "info": {"total_token_usage": self.snapshot()["usage"]}}}]
        path.write_text("\n".join(json.dumps(e) for e in events) + '\n{"incomplete":', encoding="utf-8")
        result = metrics.session_snapshot(path)
        self.assertEqual(110, result["usage"]["total_tokens"])
        self.assertNotIn("DO NOT COPY", json.dumps(result))

    def log(self, path, session_id, total=110, timestamp="2026-09-30T00:00:00Z"):
        path.parent.mkdir(parents=True, exist_ok=True)
        events = [{"type": "session_meta", "payload": {"id": session_id, "cwd": str(self.root)}},
                  {"type": "turn_context", "payload": {"model": "model-a", "effort": "low"}},
                  {"type": "event_msg", "timestamp": timestamp, "payload": {"type": "token_count",
                   "info": {"total_token_usage": self.snapshot(total)["usage"]}}}]
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    def indexed_home(self):
        session_id = str(uuid.uuid4())
        home = self.root / "codex"
        old = home / "sessions" / (session_id + ".jsonl")
        current = home / "sessions" / (session_id + "_page2.jsonl")
        self.log(old, session_id)
        self.log(current, session_id, 210)
        with closing(sqlite3.connect(str(home / "state_5.sqlite"))) as db:
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES (?, ?)", (session_id, str(current)))
            db.commit()
        return home, session_id, old, current

    def test_pagination_uses_readonly_index_not_ambiguous_glob(self):
        home, session_id, _, current = self.indexed_home()
        with patch.dict(os.environ, {"CODEX_HOME": str(home), "CODEX_THREAD_ID": session_id}):
            binding, snapshot = telemetry.observe()
        self.assertEqual("codex_index", binding["method"])
        self.assertEqual(str(current.resolve()), binding["path"])
        self.assertEqual(210, snapshot["usage"]["total_tokens"])

    def test_multiple_pages_without_index_stay_unknown(self):
        home, session_id, _, _ = self.indexed_home()
        (home / "state_5.sqlite").unlink()
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            self.assertEqual("ambiguous_session_pages", telemetry.resolve_session(session_id=session_id)["status"])

    def test_index_cannot_bind_another_session(self):
        home, session_id, _, current = self.indexed_home()
        self.log(current, str(uuid.uuid4()))
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            result = telemetry.resolve_session(session_id=session_id)
        self.assertEqual("indexed_source_unavailable", result["status"])

    def test_session_id_environment_fallback(self):
        home, session_id, _, _ = self.indexed_home()
        with patch.dict(os.environ, {"CODEX_HOME": str(home), "CODEX_SESSION_ID": session_id}, clear=True):
            self.assertEqual("bound", telemetry.resolve_session()["status"])

    def test_archived_session_is_discovered(self):
        session_id = str(uuid.uuid4())
        home = self.root / "codex"
        path = home / "archived_sessions" / (session_id + ".jsonl")
        self.log(path, session_id)
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            self.assertEqual(str(path.resolve()), telemetry.resolve_session(session_id=session_id)["path"])

    def test_no_id_never_guesses_from_project(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("missing_session_id", telemetry.resolve_session()["status"])

    def test_invalid_id_is_diagnostic(self):
        self.assertEqual("invalid_session_id", telemetry.resolve_session(session_id="bad")["status"])

    def test_no_telemetry_still_binds_identity_and_prevents_overlap(self):
        session_id = str(uuid.uuid4())
        path = self.root / "empty.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": session_id}}) + "\n")
        record = metrics.start_run(self.root, "one", self.ledger, session=path)
        self.assertFalse(record["measurement_ready"])
        self.assertEqual("no_token_events", record["binding"]["status"])
        self.assertEqual(record, metrics.start_run(self.root, "one", self.ledger, session=path))
        with self.assertRaises(ValueError):
            metrics.start_run(self.root, "two", self.ledger, session=path)

    def test_rotation_is_not_silently_counted(self):
        start, end = self.snapshot(), self.snapshot(210)
        start["source"], end["source"] = "old.jsonl", "new.jsonl"
        self.assertEqual((None, "source_rotated_unverified"), metrics.usage_delta(start, end))

    def test_unchanged_snapshot_is_not_zero_usage(self):
        start, end = self.snapshot(), self.snapshot()
        start["timestamp"] = end["timestamp"] = "2026-09-30T00:00:00Z"
        self.assertEqual((None, "no_new_telemetry"), metrics.usage_delta(start, end))

    def test_checkpoint_and_receipt(self):
        session_id = str(uuid.uuid4())
        path = self.root / "session.jsonl"
        self.log(path, session_id)
        record = metrics.start_run(self.root, "receipt-test", self.ledger, session=path)
        self.log(path, session_id, 210, "2026-09-30T00:01:00Z")
        checkpoint = metrics.checkpoint_run(self.ledger, record["run_id"], "documentation")
        self.assertEqual(100, checkpoint["usage"]["total_tokens"])
        self.log(path, session_id, 310, "2026-09-30T00:02:00Z")
        result = metrics.finish_run(self.ledger, record["run_id"])
        self.assertEqual(200, result["usage"]["total_tokens"])
        receipt = metrics.receipt(self.ledger, record["run_id"])
        self.assertIn("文档维护阶段 token: 100", receipt)
        self.assertIn("净节省: 未知", receipt)
        self.assertNotIn("节省: 0", receipt)
        self.assertEqual(1, metrics.summarize(self.ledger)["measurement_coverage"])

    def test_acceptance_requires_evidence(self):
        record = metrics.start_run(self.root, "test", self.ledger, session=self.root / "missing.jsonl")
        with self.assertRaises(ValueError):
            metrics.finish_run(self.ledger, record["run_id"], outcome="passed")
        evidence = self.root / "validation.txt"
        evidence.write_text("synthetic test evidence")
        result = metrics.finish_run(self.ledger, record["run_id"], "passed", evidence)
        self.assertEqual(64, len(result["validation"]["sha256"]))

    def test_comparison_requires_shared_experiment_settings(self):
        baseline, fugue, evidence = self.pair()
        fugue["experiment"] = {"reasoning_effort": "high"}
        with self.assertRaisesRegex(ValueError, "experiment_settings"):
            metrics.compare_records(baseline, fugue, evidence)


if __name__ == "__main__":
    unittest.main()
