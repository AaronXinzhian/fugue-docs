#!/usr/bin/env python3
"""
[INPUT]: 依赖 json, os, pathlib, subprocess, sys, tempfile, unittest
[OUTPUT]: First-round shell entry point regression tests without model calls
[POS]: fugue-docs evaluation package - plan gating and settings preservation
[PROTOCOL]: Update when run-first-round.sh settings or execution gating changes
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("run-first-round.sh")
FAKE_PYTHON = r'''#!{python}
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["WRAPPER_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\n")
if "--verify-tasks" in args:
    print(json.dumps({{"ok": not os.environ.get("FAIL_PREFLIGHT")}}))
    sys.exit(1 if os.environ.get("FAIL_PREFLIGHT") else 0)
if "analyze_navigation.py" in args[1]:
    pathlib.Path(args[args.index("--output") + 1]).write_text("{{}}\n")
elif "--execute" in args:
    output = pathlib.Path(args[args.index("--output") + 1])
    output.mkdir()
    (output / "report.json").write_text("{{}}\n")
else:
    print("{{}}")
'''


class FirstRoundTests(unittest.TestCase):
    def run_wrapper(self, *args, settings=None, existing=False):
        with tempfile.TemporaryDirectory(prefix="fugue-wrapper-") as directory:
            root = Path(directory)
            fake = root / "fake-python"
            fake.write_text(FAKE_PYTHON.format(python=sys.executable))
            fake.chmod(0o700)
            calls = root / "calls.jsonl"
            output = root / "private output"
            if existing:
                output.mkdir()
            env = {key: value for key, value in os.environ.items() if key not in
                   ("MODEL", "EFFORT", "BUDGET", "TIMEOUT", "REPEATS", "TASKS", "DESIGN")}
            env.update(PILOT_PYTHON=str(fake), PILOT_OUTPUT=str(output), WRAPPER_CALLS=str(calls))
            env.update(settings or {})
            result = subprocess.run(["bash", str(SCRIPT), *args], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=20)
            invoked = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result, invoked, (output / "navigation.json").exists()

    def test_default_only_verifies_and_plans(self):
        result, calls, navigation = self.run_wrapper()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(calls))
        self.assertIn("--verify-tasks", calls[0])
        self.assertFalse(any("--execute" in call for call in calls))
        plan = calls[1]
        for flag, value in (("--model", "gpt-6.1-sol"), ("--effort", "xhigh"),
                            ("--max-total-tokens", "500000"), ("--repeats", "1"),
                            ("--design", "three-arm"), ("--tasks", "session-fallback")):
            self.assertEqual(value, plan[plan.index(flag) + 1])
        self.assertFalse(navigation)

    def test_preflight_failure_prevents_plan_and_execution(self):
        result, calls, _ = self.run_wrapper("--execute", settings={"FAIL_PREFLIGHT": "1"})
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(1, len(calls))
        self.assertIn("--verify-tasks", calls[0])

    def test_explicit_execution_also_analyzes_results(self):
        result, calls, navigation = self.run_wrapper("--execute")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(4, len(calls))
        self.assertIn("--execute", calls[2])
        self.assertIn("analyze_navigation.py", calls[3][1])
        self.assertTrue(navigation)

    def test_custom_settings_are_not_silently_changed(self):
        settings = {"MODEL": "fake-model", "EFFORT": "high", "BUDGET": "800000",
                    "TASKS": "session-fallback coverage-summary", "DESIGN": "two-arm",
                    "PILOT_CODEX": "/fake app/codex"}
        result, calls, _ = self.run_wrapper("--plan", settings=settings)
        self.assertEqual(0, result.returncode, result.stderr)
        plan = calls[1]
        self.assertEqual("fake-model", plan[plan.index("--model") + 1])
        self.assertEqual("high", plan[plan.index("--effort") + 1])
        self.assertEqual("800000", plan[plan.index("--max-total-tokens") + 1])
        self.assertEqual("/fake app/codex", plan[plan.index("--codex") + 1])
        self.assertEqual(["session-fallback", "coverage-summary"], plan[plan.index("--tasks") + 1:])

    def test_existing_output_and_invalid_arguments_stop_before_verification(self):
        for args, existing in (((), True), (("--typo",), False), (("--plan", "--execute"), False)):
            with self.subTest(args=args, existing=existing):
                result, calls, _ = self.run_wrapper(*args, existing=existing)
                self.assertEqual(2, result.returncode)
                self.assertEqual([], calls)
        result, calls, _ = self.run_wrapper(settings={"TASKS": "  "})
        self.assertEqual(2, result.returncode)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
