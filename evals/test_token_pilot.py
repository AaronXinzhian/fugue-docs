#!/usr/bin/env python3
"""
[INPUT]: 依赖 io, json, pathlib, sys, tarfile, tempfile, types, unittest, unittest.mock, run_token_pilot
[OUTPUT]: 提供试点配对、失败保留、零基线与缓存计数回归
[POS]: fugue-docs 评测包-实验汇总可信度检查;不调用模型
[PROTOCOL]: 修改汇总与 token 口径时同步本测试
"""

import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_token_pilot as pilot


class PilotTests(unittest.TestCase):
    def trial(self, condition, total=100, accepted=True):
        return {"task": "test", "repeat": 1, "condition": condition,
                "accepted": accepted, "usage": {"total_tokens": total}, "elapsed_seconds": 10}

    def test_failed_trials_are_not_dropped_from_cost(self):
        report = pilot.summarize_trials([self.trial("baseline", 100), self.trial("fugue", 80, False)])
        self.assertEqual(180, report["experiment_total_tokens"])
        self.assertEqual(0, report["qualified_pairs"])
        self.assertIsNone(report["paired_difference"])
        self.assertEqual(1, report["accepted"])

    def test_negative_saving_is_preserved(self):
        report = pilot.summarize_trials([self.trial("baseline", 100), self.trial("fugue", 150)])
        self.assertEqual(-50, report["paired_difference"])
        self.assertEqual(-0.5, report["median_pair_saving_rate"])

    def test_missing_side_or_usage_does_not_make_a_pair(self):
        report = pilot.summarize_trials([self.trial("baseline")])
        self.assertEqual(0, report["qualified_pairs"])
        self.assertIsNone(report["by_condition"]["fugue"]["measured_tokens"])
        trial = self.trial("fugue")
        trial["usage"] = None
        report = pilot.summarize_trials([self.trial("baseline"), trial])
        self.assertEqual(1, report["unknown_usage_trials"])
        self.assertEqual(0, report["qualified_pairs"])
        self.assertIsNone(report["experiment_total_tokens"])
        self.assertEqual(100, report["known_total_tokens"])

    def test_zero_baseline_has_no_percentage(self):
        report = pilot.summarize_trials([self.trial("baseline", 0), self.trial("fugue", 50)])
        self.assertEqual(-50, report["paired_difference"])
        self.assertIsNone(report["median_pair_saving_rate"])

    def test_cache_is_not_added_twice(self):
        result = pilot.cli_usage([{"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20}}])
        self.assertEqual(120, result["total_tokens"])
        self.assertEqual(20, result["uncached_input_tokens"])

    def test_usage_requires_completion_and_valid_counters(self):
        self.assertIsNone(pilot.cli_usage([{"type": "turn.failed"}]))
        self.assertIsNone(pilot.cli_usage([{"type": "turn.completed", "usage": {
            "input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1}}]))

    def test_trial_uses_private_home_and_reproducible_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "fake-codex"
            executable.write_text("#!" + sys.executable + "\nimport json, os, sys\n"
                "prompt=sys.stdin.read()\n"
                "print(json.dumps({'type':'isolation','home':os.environ['HOME'],"
                "'thread':os.environ.get('CODEX_THREAD_ID'),'args':sys.argv,'prompt':prompt}))\n"
                "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,"
                "'cached_input_tokens':2,'output_tokens':3}}))\n")
            executable.chmod(0o700)
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as archive:
                content = b"pilot fixture\n"
                member = tarfile.TarInfo("README.md")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("Synthetic skill for runner unit test")
            args = SimpleNamespace(auth_home=str(root / "no-auth"), codex=str(executable),
                                   model="fake-model", effort="xhigh", timeout=10)
            with patch.object(pilot, "validate", return_value=[{"passed": True, "check": "synthetic"}]):
                baseline = pilot.trial(args, data.getvalue(), skill, "session-fallback", 1, "baseline", root)
                fugue = pilot.trial(args, data.getvalue(), skill, "session-fallback", 1, "fugue", root)
            self.assertEqual(baseline["base_commit"], fugue["base_commit"])
            self.assertEqual(13, baseline["usage"]["total_tokens"])
            events = (root / "session-fallback-1-baseline" / "events.jsonl").read_text().splitlines()
            probe = json.loads(events[0])
            self.assertIsNone(probe["thread"])
            self.assertIn("model_reasoning_effort=\"xhigh\"", probe["args"])
            self.assertFalse((Path(probe["home"]) / ".codex" / "AGENTS.md").exists())
            self.assertTrue((root / "session-fallback-1-fugue" / "home" / ".codex" / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
