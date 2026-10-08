#!/usr/bin/env python3
"""
[INPUT]: 依赖 io, json, os, pathlib, subprocess, sys, tarfile, tempfile, types, unittest, unittest.mock, run_token_pilot
[OUTPUT]: 提供试点配对块、三组/A-A 汇总、成本口径、索引剥离、失败保留与假执行器端到端回归
[POS]: fugue-docs 评测包-实验设计与汇总可信度检查;不调用模型
[PROTOCOL]: 修改设计、汇总、剥离或 token 口径时同步本测试
"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_token_pilot as pilot

TWO_ARM = [("baseline", "fugue", "workflow_effect")]
# 浅克隆里没有内置任务的固定源码提交,依赖它的测试跳过而不是报错
HAS_SOURCE_REF = subprocess.run(["git", "cat-file", "-e", "fdf4810^{commit}"], cwd=str(pilot.ROOT),
                                capture_output=True).returncode == 0
needs_source_ref = unittest.skipUnless(HAS_SOURCE_REF, "fdf4810 not in this clone (shallow checkout)")


def usage(total=100, cached=0, output=0):
    return {"input_tokens": total - output, "cached_input_tokens": cached, "output_tokens": output,
            "total_tokens": total}


def trial(condition, total=100, accepted=True, repeat=1, cached=0, output=0, task="test"):
    return {"task": task, "repeat": repeat, "condition": condition, "accepted": accepted,
            "usage": usage(total, cached, output), "elapsed_seconds": 10}


FAKE_CODEX = r'''#!{python}
import json, os, subprocess, sys
prompt = sys.stdin.read()
workspace = sys.argv[sys.argv.index("-C") + 1]
home = os.environ["HOME"]
fugue = os.path.isfile(os.path.join(home, ".codex", "AGENTS.md"))
indexed = os.path.isfile(os.path.join(workspace, "PROJECT_INDEX.md"))
mode = os.environ.get("FAKE_MODE", "ok")
def emit(event):
    print(json.dumps(event), flush=True)
emit({"type": "thread.started", "thread_id": "fake"})
emit({"type": "probe", "home": home, "thread": os.environ.get("CODEX_THREAD_ID"), "args": sys.argv, "prompt": prompt})
if indexed:
    emit({"type": "item.completed", "item": {"id": "1", "type": "command_execution",
          "command": "bash -lc 'cat PROJECT_INDEX.md'", "aggregated_output": "x" * 50, "exit_code": 0}})
else:
    for i, path in enumerate(["app.py", "other.py", "more.py"]):
        emit({"type": "item.completed", "item": {"id": "s%d" % i, "type": "command_execution",
              "command": "bash -lc 'rg -n VALUE . && cat %s'" % path, "aggregated_output": "y" * 400, "exit_code": 0}})
if fugue:
    emit({"type": "item.completed", "item": {"id": "w", "type": "command_execution",
          "command": "bash -lc 'python3 ~/.agents/skills/fugue-docs/scripts/geb_metrics.py start . --task t'",
          "aggregated_output": "{}", "exit_code": 0}})
subprocess.run(["git", "apply", os.environ["FAKE_PATCH"]], cwd=workspace, check=True)
open(os.path.join(workspace, "new_helper.py"), "w").write("HELPER = True\n")
emit({"type": "item.completed", "item": {"id": "e", "type": "file_change",
      "changes": [{"path": os.path.join(workspace, "app.py"), "kind": "update"}]}})
if mode == "no-usage":
    sys.exit(0)
if mode == "partial":
    sessions = os.path.join(os.environ["CODEX_HOME"], "sessions")
    os.makedirs(sessions)
    with open(os.path.join(sessions, "s.jsonl"), "w") as log:
        for event in ({"type": "session_meta", "payload": {"id": "fake"}},
                      {"type": "turn_context", "payload": {"model": "fake"}},
                      {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
                          "input_tokens": 90, "cached_input_tokens": 10, "output_tokens": 10, "total_tokens": 100}}}}):
            log.write(json.dumps(event) + "\n")
    sys.exit(0)
total = {"noindex": 3000, "index": 2000, "fugue": 2500}["fugue" if fugue else ("index" if indexed else "noindex")]
emit({"type": "turn.completed", "usage": {"input_tokens": total - 100, "cached_input_tokens": total // 2,
      "output_tokens": 100}})
'''


class SummaryTests(unittest.TestCase):
    def test_failed_trials_are_not_dropped_from_cost(self):
        report = pilot.summarize_trials([trial("baseline", 100), trial("fugue", 80, False)], TWO_ARM)
        self.assertEqual(180, report["experiment_total_tokens"])
        self.assertEqual(0, report["comparisons"]["workflow_effect"]["qualified_pairs"])
        self.assertEqual(1, report["accepted"])
        self.assertEqual(0.0, report["by_condition"]["fugue"]["acceptance_rate"])

    def test_negative_saving_is_preserved(self):
        report = pilot.summarize_trials([trial("baseline", 100), trial("fugue", 150)], TWO_ARM)
        stats = report["comparisons"]["workflow_effect"]["metrics"]["total_tokens"]
        self.assertEqual(-50, stats["sum_difference"])
        self.assertEqual(-0.5, stats["median_saving_rate"])
        self.assertEqual(1, stats["treatment_higher"])

    def test_missing_side_or_usage_does_not_make_a_pair(self):
        report = pilot.summarize_trials([trial("baseline")], TWO_ARM)
        self.assertEqual(0, report["comparisons"]["workflow_effect"]["qualified_pairs"])
        self.assertNotIn("fugue", report["by_condition"])
        missing = trial("fugue")
        missing["usage"] = None
        report = pilot.summarize_trials([trial("baseline"), missing], TWO_ARM)
        self.assertEqual(1, report["unknown_usage_trials"])
        self.assertEqual(0, report["comparisons"]["workflow_effect"]["qualified_pairs"])
        self.assertIsNone(report["experiment_total_tokens"])
        self.assertEqual(100, report["known_total_tokens"])

    def test_pairs_stay_inside_their_block(self):
        report = pilot.summarize_trials([trial("baseline", 100, repeat=1), trial("fugue", 90, repeat=2)], TWO_ARM)
        self.assertEqual(0, report["comparisons"]["workflow_effect"]["qualified_pairs"])

    def test_zero_baseline_has_no_percentage(self):
        report = pilot.summarize_trials([trial("baseline", 0), trial("fugue", 50)], TWO_ARM)
        stats = report["comparisons"]["workflow_effect"]["metrics"]["total_tokens"]
        self.assertEqual(-50, stats["sum_difference"])
        self.assertIsNone(stats["median_saving_rate"])

    def test_primary_metric_ignores_cheap_cache_volume(self):
        # 赋格总量更大但几乎全是缓存;主指标看未缓存输入 + 输出
        base = trial("baseline", 1000, cached=200, output=100)
        fugue = trial("fugue", 1200, cached=900, output=100)
        metrics = pilot.summarize_trials([base, fugue], TWO_ARM)["comparisons"]["workflow_effect"]["metrics"]
        self.assertEqual(-200, metrics["total_tokens"]["sum_difference"])
        self.assertEqual(800 - 300, metrics["uncached_plus_output"]["sum_difference"])
        self.assertEqual("uncached_plus_output", pilot.PRIMARY_METRIC)

    def test_weighted_cost_uses_declared_weights_only(self):
        weights = pilot.parse_weights("uncached_input_tokens=1,cached_input_tokens=0.1,output_tokens=4")
        report = pilot.summarize_trials([trial("baseline", 1000, cached=500, output=100),
                                         trial("fugue", 1000, cached=800, output=100)], TWO_ARM, weights)
        stats = report["comparisons"]["workflow_effect"]["metrics"]["weighted_cost"]
        self.assertAlmostEqual((400 + 50 + 400) - (100 + 80 + 400), stats["sum_difference"])
        self.assertNotIn("weighted_cost", pilot.summarize_trials([trial("baseline"), trial("fugue")],
                                                                  TWO_ARM)["comparisons"]["workflow_effect"]["metrics"])
        with self.assertRaises(ValueError):
            pilot.parse_weights("total_tokens=1")

    def test_failure_aware_outcome_counts_one_sided_failures(self):
        trials = [trial("baseline", 100, repeat=1), trial("fugue", 80, repeat=1),
                  trial("baseline", 100, False, repeat=2), trial("fugue", 300, repeat=2),
                  trial("baseline", 100, repeat=3), trial("fugue", 50, False, repeat=3),
                  trial("baseline", 100, False, repeat=4), trial("fugue", 50, False, repeat=4)]
        outcome = pilot.summarize_trials(trials, TWO_ARM)["comparisons"]["workflow_effect"]["failure_aware"]
        self.assertEqual((2, 1, 1), (outcome["treatment_better"], outcome["treatment_worse"], outcome["ties"]))
        self.assertEqual(1.0, outcome["sign_test_p"])

    def test_aa_design_reports_noise_and_power(self):
        chosen = pilot.design("aa", "index")
        self.assertEqual([("index-a", "index"), ("index-b", "index")], chosen["arms"])
        trials = []
        for repeat, (a, b) in enumerate([(1000, 900), (1000, 1100), (1000, 950), (1000, 1080)], 1):
            trials += [trial("index-a", a, repeat=repeat), trial("index-b", b, repeat=repeat)]
        noise = pilot.summarize_trials(trials, chosen["comparisons"])["comparisons"]["noise"]
        self.assertEqual(4, noise["qualified_pairs"])
        hint = noise["power_hint"]["total_tokens"]
        self.assertGreater(hint["pairs_needed"]["10%"], hint["pairs_needed"]["30%"])

    def test_cache_is_not_added_twice(self):
        result = pilot.cli_usage([{"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20}}])
        self.assertEqual(120, result["total_tokens"])
        self.assertEqual(20, result["uncached_input_tokens"])
        self.assertEqual(40, result["uncached_plus_output"])

    def test_legacy_usage_derives_primary_metric(self):
        legacy = {"input_tokens": 121379, "cached_input_tokens": 104064, "output_tokens": 1979, "total_tokens": 123358}
        self.assertEqual(17315 + 1979, pilot.usage_value(legacy, "uncached_plus_output"))
        self.assertIsNone(pilot.usage_value({"total_tokens": 5}, "uncached_plus_output"))

    def test_usage_requires_completion_and_valid_counters(self):
        self.assertIsNone(pilot.cli_usage([{"type": "turn.failed"}]))
        self.assertIsNone(pilot.cli_usage([{"type": "turn.completed", "usage": {
            "input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1}}]))


class DesignTests(unittest.TestCase):
    def test_schedule_keeps_every_block_complete_and_reproducible(self):
        arms = pilot.DESIGNS["three-arm"]["arms"]
        first = pilot.build_schedule(["a", "b"], 3, arms, 7)
        self.assertEqual(first, pilot.build_schedule(["a", "b"], 3, arms, 7))
        self.assertEqual(6, len(first))
        for block in first:
            self.assertEqual({"noindex", "index", "fugue"}, set(block["order"]))
        self.assertGreater(len({tuple(b["order"]) for b in first}), 1)

    def test_strip_removes_indexes_headers_and_rule_blocks_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg").mkdir()
            (root / "evals" / "fixtures" / "x").mkdir(parents=True)
            (root / "PROJECT_INDEX.md").write_text("# index\n")
            (root / "pkg" / "FOLDER_INDEX.md").write_text("# folder\n")
            (root / "evals" / "fixtures" / "x" / "FOLDER_INDEX.md").write_text("# sample data\n")
            body = "\n".join("x%d = %d" % (i, i) for i in range(60)) + "\nTAG = '[INPUT]: not a header'\n"
            (root / "pkg" / "mod.py").write_text('"""\n[INPUT]: 依赖 os\n[OUTPUT]: 提供 x\n[POS]: demo\n'
                                                 '[PROTOCOL]: update\nkeep this line\n"""\n' + body)
            (root / "pkg" / "only_header.py").write_text('"""\n[INPUT]: 依赖 os\n[POS]: demo\n"""\nVALUE = 1\n')
            (root / "pkg" / "prose.py").write_text('"""\n[POS]、模块定位留给人补全。\n"""\n')
            (root / "pkg" / "markers.py").write_text(
                'BEGIN = "%s"\nEND = "%s"\n' % (pilot.MANAGED_BEGIN, pilot.MANAGED_END))
            (root / "AGENTS.md").write_text("Project rules\n%s\nGEB protocol text\n%s\nmore rules\n"
                                            % (pilot.MANAGED_BEGIN, pilot.MANAGED_END))
            (root / "README.md").write_text("[INPUT]: documentation mention stays\n")
            stats = pilot.strip_indexes(root, ["evals/fixtures/"])
            self.assertEqual({"index_files_removed": 2, "header_files_stripped": 2, "header_lines_removed": 6,
                              "empty_header_blocks_removed": 1, "managed_blocks_removed": 1}, stats)
            self.assertEqual("VALUE = 1\n", (root / "pkg" / "only_header.py").read_text())
            self.assertIn("[POS]、模块定位", (root / "pkg" / "prose.py").read_text())
            mod = (root / "pkg" / "mod.py").read_text()
            self.assertNotIn("[POS]", mod)
            self.assertIn("keep this line", mod)
            self.assertIn("TAG = '[INPUT]: not a header'", mod)
            self.assertIn(pilot.MANAGED_BEGIN, (root / "pkg" / "markers.py").read_text())
            self.assertEqual("Project rules\nmore rules\n", (root / "AGENTS.md").read_text())
            self.assertTrue((root / "evals" / "fixtures" / "x" / "FOLDER_INDEX.md").exists())
            self.assertIn("[INPUT]", (root / "README.md").read_text())
            compile(mod, "mod.py", "exec")

    def test_tasks_file_rejects_escaping_acceptance_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text(json.dumps({"schema": "geb.token-pilot.tasks.v1", "tasks": {"t": {
                "prompt": "x", "acceptance": [["true"]], "acceptance_files": {"../escape.py": "a.py"}}}}))
            with self.assertRaises(ValueError):
                pilot.load_tasks(path)

    def test_builtin_tasks_have_both_prompt_styles_without_file_names_in_symptoms(self):
        tasks = pilot.load_tasks(pilot.DEFAULT_TASKS_FILE)["tasks"]
        self.assertEqual(4, len(tasks))
        for task in tasks.values():
            symptom = task["prompts"]["symptom"]
            self.assertNotIn("geb_metrics", symptom)
            self.assertNotIn(".py", symptom)
            self.assertTrue(task["reference_patch"].is_file())


class RunnerTests(unittest.TestCase):
    def make_source(self, root):
        repo = root / "source"
        (repo / "tests").mkdir(parents=True)
        (repo / "PROJECT_INDEX.md").write_text("# demo index\n")
        (repo / "app.py").write_text('"""\n[INPUT]: none\n[OUTPUT]: VALUE\n[POS]: demo\n[PROTOCOL]: update\n"""\n'
                                     'import os\nVALUE = 1\nNAME = "demo"\n')
        (repo / "tests" / "test_app.py").write_text("import unittest\nclass T(unittest.TestCase):\n"
                                                    "    def test_ok(self):\n        self.assertTrue(True)\n")
        git = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
            subprocess.run(command, cwd=repo, env=git, check=True, capture_output=True)
        spec = root / "spec"
        spec.mkdir()
        (spec / "check.py").write_text("import os, sys\nsys.path.insert(0, os.getcwd())\nimport app\n"
                                       "assert app.VALUE == 2, app.VALUE\n")
        (spec / "fix.patch").write_text('--- a/app.py\n+++ b/app.py\n@@ -7,3 +7,3 @@\n import os\n'
                                        '-VALUE = 1\n+VALUE = 2\n NAME = "demo"\n')
        (spec / "tasks.json").write_text(json.dumps({
            "schema": "geb.token-pilot.tasks.v1", "source_ref": "HEAD", "protected": ["tests/test_*.py"],
            "regression": [["{python}", "-B", "-m", "unittest", "discover", "-s", "tests"]],
            "tasks": {"bump": {"prompts": {"symptom": "The value is too small.", "named": "Set app.VALUE to 2."},
                               "acceptance_files": {".pilot_acceptance/check.py": "check.py"},
                               "acceptance": [["{python}", "-B", ".pilot_acceptance/check.py"]],
                               "reference_patch": "fix.patch"}}}))
        fake = root / "fake-codex"
        fake.write_text(FAKE_CODEX.replace("{python}", sys.executable))
        fake.chmod(0o700)
        return repo, spec / "tasks.json", fake

    def run_pilot(self, root, *extra, mode="ok"):
        repo, tasks, fake = self.make_source(root)
        env = dict(os.environ, FAKE_PATCH=str(tasks.parent / "fix.patch"), FAKE_MODE=mode)
        command = [sys.executable, "-B", str(Path(pilot.__file__)), "--source-repo", str(repo), "--tasks-file",
                   str(tasks), "--codex", str(fake), "--model", "fake", "--auth-home", str(root / "no-auth"),
                   "--output", str(root / "out"), "--execute", "--timeout", "30"] + list(extra)
        run = subprocess.run(command, capture_output=True, text=True, env=env)
        self.assertEqual(0, run.returncode, run.stderr)
        return json.loads((root / "out" / "report.json").read_text())

    def test_three_arm_end_to_end_separates_index_and_workflow_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "2")
            self.assertEqual("schedule_complete", report["stop_reason"])
            self.assertEqual(6, len(report["trials"]))
            self.assertTrue(all(t["accepted"] for t in report["trials"]), report["trials"])
            comparisons = report["summary"]["comparisons"]
            index = comparisons["index_effect"]["metrics"]
            self.assertEqual(2, comparisons["index_effect"]["qualified_pairs"])
            self.assertGreater(index["total_tokens"]["median_saving_rate"], 0)
            self.assertGreater(index["nav_output_chars_before_edit"]["median_difference"], 0)
            self.assertLess(comparisons["workflow_effect"]["metrics"]["total_tokens"]["median_saving_rate"], 0)
            noindex = next(t for t in report["trials"] if t["arm"] == "noindex")
            self.assertEqual(1, noindex["strip"]["index_files_removed"])
            fugue = next(t for t in report["trials"] if t["arm"] == "fugue")
            self.assertEqual(1, fugue["navigation"]["total"]["workflow_runs"])
            patch = (Path(directory) / "out" / noindex["trial_dir"] / "changes.patch").read_text()
            self.assertNotIn(".pilot_acceptance", patch)
            self.assertIn("new_helper.py", patch)
            self.assertEqual([0, 1, 2], sorted(t["block_position"] for t in report["trials"][:3]))
            self.assertEqual([], report["summary"]["trials_with_outside_workspace_reads"])

    def test_budget_stops_only_at_block_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "3", "--max-total-tokens", "100")
            self.assertEqual("token_budget", report["stop_reason"])
            self.assertEqual(3, len(report["trials"]))
            self.assertEqual({"noindex", "index", "fugue"}, {t["condition"] for t in report["trials"]})

    def test_unknown_usage_stops_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "2", mode="no-usage")
            self.assertEqual("unknown_usage", report["stop_reason"])
            self.assertEqual(1, len(report["trials"]))
            self.assertIsNone(report["summary"]["experiment_total_tokens"])

    def test_partial_usage_stops_before_spending_an_unknown_remainder(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "1", mode="partial")
            self.assertEqual("incomplete_usage", report["stop_reason"])
            self.assertEqual(1, len(report["trials"]))
            self.assertEqual(1, report["summary"]["partial_usage_trials"])
            self.assertEqual(100, report["summary"]["budget_tokens_lower_bound"])
            self.assertIsNone(report["summary"]["experiment_total_tokens"])
            self.assertEqual(0, report["summary"]["comparisons"]["index_effect"]["qualified_pairs"])

    def test_hidden_answers_inside_the_source_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, tasks, _ = self.make_source(root)
            (repo / "leak.py").write_bytes((tasks.parent / "check.py").read_bytes())
            git = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
            subprocess.run(["git", "add", "-A"], cwd=repo, env=git, check=True)
            subprocess.run(["git", "commit", "-qm", "leak"], cwd=repo, env=git, check=True)
            run = subprocess.run([sys.executable, "-B", str(Path(pilot.__file__)), "--source-repo", str(repo),
                                  "--tasks-file", str(tasks), "--model", "m", "--output", "unused"],
                                 capture_output=True, text=True)
            self.assertEqual(2, run.returncode)
            self.assertIn("leak.py", run.stderr)

    @needs_source_ref
    def test_self_hosted_skill_cannot_be_newer_than_source(self):
        run = subprocess.run([sys.executable, "-B", str(Path(pilot.__file__)), "--model", "m", "--output", "unused",
                              "--skill-ref", "HEAD"], capture_output=True, text=True)
        self.assertEqual(2, run.returncode)
        self.assertIn("ancestor", run.stderr)

    @needs_source_ref
    def test_builtin_tasks_verify_in_every_workspace(self):
        run = subprocess.run([sys.executable, "-B", str(Path(pilot.__file__)), "--verify-tasks"],
                             capture_output=True, text=True)
        self.assertEqual(0, run.returncode, run.stdout[-2000:] + run.stderr)
        self.assertTrue(json.loads(run.stdout)["ok"])

    def test_verify_tasks_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, tasks, _ = self.make_source(Path(directory))
            run = subprocess.run([sys.executable, "-B", str(Path(pilot.__file__)), "--source-repo", str(repo),
                                  "--tasks-file", str(tasks), "--verify-tasks"], capture_output=True, text=True)
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(["index", "noindex"], result["workspace_arms"])
            self.assertTrue(all(row["ok"] for row in result["results"]))

    @needs_source_ref
    def test_plan_mode_lists_paired_blocks_without_model_calls(self):
        run = subprocess.run([sys.executable, "-B", str(Path(pilot.__file__)), "--model", "m", "--output", "unused",
                              "--design", "aa", "--repeats", "2", "--tasks", "session-fallback"],
                             capture_output=True, text=True)
        self.assertEqual(0, run.returncode, run.stderr)
        plan = json.loads(run.stdout)
        self.assertEqual([["index-a", "index-b", "noise"]], plan["comparisons"])
        self.assertEqual(2, plan["planned_blocks"])
        self.assertEqual(4, plan["planned_trials"])

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
                for name, content in (("README.md", b"pilot fixture\n"), ("PROJECT_INDEX.md", b"# index\n")):
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("Synthetic skill for runner unit test")
            task = pilot.load_tasks(pilot.DEFAULT_TASKS_FILE)["tasks"]["session-fallback"]
            args = SimpleNamespace(auth_home=str(root / "no-auth"), codex=str(executable), model="fake-model",
                                   effort="xhigh", timeout=10, prompt_style="symptom", validation_timeout=10)
            with patch.object(pilot, "validate", return_value=[{"passed": True, "check": "synthetic", "kind": "acceptance"}]):
                results = {arm: pilot.trial(args, data.getvalue(), skill, task, 1, arm, arm, root)
                           for arm in ("noindex", "index", "fugue")}
            self.assertEqual(results["index"]["base_commit"], results["fugue"]["base_commit"])
            self.assertNotEqual(results["index"]["base_commit"], results["noindex"]["base_commit"])
            self.assertEqual(13, results["index"]["usage"]["total_tokens"])
            probe = json.loads((root / "session-fallback-1-index" / "events.jsonl").read_text().splitlines()[0])
            for arm, result in results.items():
                seen = json.loads((root / result["trial_dir"] / "events.jsonl").read_text().splitlines()[0])
                cwd = seen["args"][seen["args"].index("-C") + 1]
                for word in ("noindex", "index", "fugue", "session-fallback"):
                    self.assertNotIn(word, cwd)
            self.assertIsNone(probe["thread"])
            self.assertIn("model_reasoning_effort=\"xhigh\"", probe["args"])
            self.assertNotIn("--ephemeral", probe["args"])
            self.assertIn("without invoking the Fugue skill", probe["prompt"])
            self.assertNotIn("geb_metrics", probe["prompt"])
            self.assertFalse((Path(probe["home"]) / ".codex" / "AGENTS.md").exists())
            self.assertTrue((root / "session-fallback-1-fugue" / "home" / ".codex" / "AGENTS.md").exists())
            self.assertFalse((root / "session-fallback-1-noindex" / "workspace" / "PROJECT_INDEX.md").exists())

    def test_partial_usage_does_not_become_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / "sessions"
            sessions.mkdir()
            path = sessions / "one.jsonl"
            events = [{"type": "session_meta", "payload": {"id": "one"}},
                      {"type": "turn_context", "payload": {"model": "fake-model"}},
                      {"type": "event_msg", "payload": {"type": "token_count", "info": {
                          "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 80,
                                                "output_tokens": 20, "total_tokens": 120}}}}]
            path.write_text("\n".join(json.dumps(e) for e in events))
            cli = [{"type": "thread.started", "thread_id": "one"}]
            self.assertEqual(120, pilot.partial_usage(home, cli)["total_tokens"])
            self.assertIsNone(pilot.cli_usage(cli))
            self.assertIsNone(pilot.partial_usage(home, [{"type": "thread.started", "thread_id": "other"}]))


if __name__ == "__main__":
    unittest.main()
