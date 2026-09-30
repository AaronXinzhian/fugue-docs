#!/usr/bin/env python3
"""
[INPUT]: 依赖 copy, json, pathlib, sys, tempfile, unittest, geb_metrics
[OUTPUT]: 提供 token 账本缺失值、计数重置、对照与重复记录测试
[POS]: fugue-docs 评测包-计量可信度回归
[PROTOCOL]: 变更时同步 evals/FOLDER_INDEX.md 与计量说明
"""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import geb_metrics as metrics


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


if __name__ == "__main__":
    unittest.main()
