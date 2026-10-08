#!/usr/bin/env python3
"""
[INPUT]: 依赖 json, pathlib, subprocess, sys, tempfile, unittest, analyze_navigation
[OUTPUT]: 提供定位成本分析器的命令分类、首次修改边界、事件格式兼容与目录汇总回归
[POS]: fugue-docs 评测包-离线定位指标可信度检查;不调用模型
[PROTOCOL]: 修改分类规则或指标口径时同步本测试与 token-pilot.md
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import analyze_navigation as nav


def command(item_id, text, output="", key="type"):
    return {"type": "item.completed", "item": {"id": item_id, key: "command_execution", "command": text,
                                                "aggregated_output": output, "exit_code": 0}}


def kinds(text):
    return [nav.classify(argv)[0] for argv in nav.simple_commands(text)[0]]


class ClassifierTests(unittest.TestCase):
    def test_common_shell_forms(self):
        self.assertEqual(["read", "read"], kinds("/bin/bash -lc \"sed -n '1,200p' scripts/geb_metrics.py | nl -ba\""))
        self.assertEqual(["search"], kinds("bash -lc 'rg -n CODEX_THREAD_ID scripts'"))
        self.assertEqual(["vcs", "search"], kinds("bash -lc 'git diff --stat && grep -rn summarize scripts/'"))
        self.assertEqual(["list", "search"], kinds(["bash", "-lc", "find . -name '*.py' | xargs grep -l summarize"]))
        self.assertEqual(["test", "read"], kinds("bash -lc 'python3 -B -m unittest discover -s evals 2>&1 | tail -5'"))

    def test_test_files_are_read_or_edited_not_run(self):
        self.assertEqual(["read"], kinds("bash -lc 'cat evals/test_metrics.py'"))
        self.assertEqual(["read"], kinds("bash -lc \"sed -n '1,40p' evals/test_boundaries.py\""))
        self.assertEqual(["search"], kinds("bash -lc 'rg -n start_run evals/test_metrics.py'"))
        self.assertEqual(["edit"], kinds("bash -lc 'sed -i s/a/b/ evals/test_new.py'"))
        self.assertEqual(["test"], kinds("bash -lc 'python3 evals/test_new.py'"))
        self.assertEqual(["test"], kinds("bash -lc 'python3 -m pytest -q'"))
        cmds, _ = nav.simple_commands("bash -lc 'echo x | tee evals/test_new.py'")
        self.assertEqual(("edit", [], ["evals/test_new.py"]), nav.classify(cmds[1]))

    def test_python_inline_writes_and_variable_targets(self):
        write = "bash -lc \"python3 - <<'EOF'\nfrom pathlib import Path\nPath('evals/test_new.py').write_text('x')\nEOF\""
        result = nav.analyze_events([command("1", "bash -lc 'cat a.py'"), command("2", write)])
        self.assertEqual(1, result["first_edit_item"])
        scratch = nav.analyze_events([command("1", "bash -lc 'cat > \"$TMPDIR/x.txt\" <<EOF\nhi\nEOF'")])
        self.assertFalse(scratch["first_edit_found"])
        self.assertTrue(nav.is_scratch("/private/var/folders/ab/T/x"))

    def test_reads_outside_the_workspace_are_flagged(self):
        result = nav.analyze_events([command("1", "bash -lc 'cat ../../trial-000/changes.patch'"),
                                     command("2", "bash -lc 'cat /tmp/run-x/workspace/a.py'"),
                                     command("3", "bash -lc 'cat ~/.agents/skills/fugue-docs/SKILL.md'"),
                                     command("4", "bash -lc 'cat /Users/me/out/report.json'")])
        self.assertEqual(["../../trial-000/changes.patch", "/Users/me/out/report.json"],
                         result["total"]["outside_workspace_read_list"])

    def test_reading_a_workflow_script_is_not_running_it(self):
        self.assertEqual(["read"], kinds("bash -lc 'cat scripts/geb_metrics.py'"))
        self.assertEqual(["workflow"], kinds("bash -lc 'python3 ~/.agents/skills/fugue-docs/scripts/geb_sync.py . --changed'"))

    def test_heredoc_writes_are_edits_and_scratch_writes_are_not(self):
        cmds, degraded = nav.simple_commands("bash -lc \"cat > evals/test_new.py <<'EOF'\nimport os\nEOF\"")
        self.assertFalse(degraded)
        self.assertEqual(("edit", [], ["evals/test_new.py"]), nav.classify(cmds[0]))
        self.assertTrue(nav.is_scratch("/tmp/x.txt"))
        self.assertFalse(nav.is_scratch("/tmp/pilot/trial/workspace/app.py"))

    def test_read_targets_skip_option_values_and_patterns(self):
        self.assertEqual(["a.py"], nav.classify(["head", "-n", "50", "a.py"])[1])
        self.assertEqual(["src/x.py"], nav.classify(["grep", "-n", "def run", "src/x.py"])[1])
        self.assertEqual(["src/x.py"], nav.classify(["sed", "-n", "1,80p", "src/x.py"])[1])


class AnalyzerTests(unittest.TestCase):
    def events(self, key="type"):
        return [{"type": "thread.started", "thread_id": "t"},
                {"type": "item.completed", "item": {"id": "r", key: "reasoning", "text": "think"}},
                command("1", "bash -lc 'cat PROJECT_INDEX.md'", "a" * 100, key),
                command("2", "bash -lc 'rg -n summarize scripts && sed -n 1,80p scripts/m.py'", "b" * 300, key),
                {"type": "item.completed", "item": {"id": "e", key: "file_change",
                                                    "changes": [{"path": "/x/workspace/scripts/m.py", "kind": "update"}]}},
                command("3", "bash -lc 'cat scripts/other.py'", "c" * 50, key),
                {"type": "item.started", "item": {"id": "4", key: "command_execution",
                                                  "command": "bash -lc 'python3 -m unittest'", "aggregated_output": ""}}]

    def test_first_edit_boundary_and_totals(self):
        result = nav.analyze_events(self.events())
        before = result["before_first_edit"]
        self.assertEqual(2, before["commands"])
        self.assertEqual(400, before["output_chars"])
        self.assertEqual(["PROJECT_INDEX.md", "scripts/m.py"], before["files_read_list"])
        self.assertEqual(1, before["index_reads"])
        self.assertEqual(4, result["total"]["commands"])
        self.assertEqual(1, result["total"]["by_kind"]["test"])
        self.assertEqual(1, result["incomplete_items"])

    def test_legacy_item_type_schema(self):
        legacy = nav.analyze_events(self.events("item_type"))
        self.assertEqual("item_type", legacy["item_schema"])
        self.assertEqual(nav.analyze_events(self.events())["before_first_edit"]["commands"],
                         legacy["before_first_edit"]["commands"])

    def test_no_edit_means_unknown_pre_edit_cost(self):
        result = nav.analyze_events([command("1", "bash -lc 'ls'")])
        self.assertIsNone(result["before_first_edit"])
        self.assertIsNone(nav.NAV_METRICS["nav_commands_before_edit"](result))

    def test_command_patch_counts_as_first_edit(self):
        patch = "bash -lc \"apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch\nPATCH\""
        result = nav.analyze_events([command("1", "bash -lc 'cat a.py'", "x"), command("2", patch)])
        self.assertEqual(1, result["first_edit_item"])
        self.assertEqual(1, result["before_first_edit"]["commands"])

    def test_failed_file_change_does_not_establish_a_boundary(self):
        failed = {"type": "item.completed", "item": {"id": "edit", "type": "file_change",
                  "status": "failed", "changes": [{"path": "a.py", "kind": "update"}]}}
        result = nav.analyze_events([command("1", "cat a.py", "x"), failed])
        self.assertFalse(result["first_edit_found"])
        self.assertIsNone(result["before_first_edit"])
        self.assertTrue(result["first_edit_boundary_uncertain"])
        self.assertEqual(1, result["uncertain_edit_items"])
        self.assertEqual(1, result["total"]["commands"])

    def test_pending_or_failed_command_edits_leave_cost_unknown(self):
        for completed in (True, False):
            edit = command("edit", "sed -i s/a/b/ a.py")
            edit["item"]["exit_code"] = 1
            if not completed:
                edit["type"] = "item.started"
            result = nav.analyze_events([command("1", "cat a.py"), edit,
                                        command("later", "sed -i s/a/b/ a.py")])
            with self.subTest(completed=completed):
                self.assertFalse(result["first_edit_found"])
                self.assertIsNone(result["before_first_edit"])
                self.assertTrue(result["first_edit_boundary_uncertain"])
                self.assertEqual(1, result["uncertain_edit_items"])
                self.assertEqual(3, result["total"]["commands"])

    def test_failed_edit_after_known_boundary_keeps_locating_cost(self):
        failed = command("later", "sed -i s/a/b/ missing.py")
        failed["item"]["exit_code"] = 1
        result = nav.analyze_events([command("read", "cat a.py"),
                                    command("edit", "sed -i s/a/b/ a.py"), failed])
        self.assertTrue(result["first_edit_found"])
        self.assertEqual(1, result["before_first_edit"]["commands"])
        self.assertFalse(result["first_edit_boundary_uncertain"])
        self.assertEqual(1, result["uncertain_edit_items"])

    def test_paired_stats_and_power_hint(self):
        stats = nav.paired_stats([(100, 80), (100, 120), (None, 5)])
        self.assertEqual(2, stats["n"])
        self.assertEqual(0, stats["sum_difference"])
        self.assertEqual(1, stats["treatment_lower"])
        self.assertEqual({"n": 0}, nav.paired_stats([]))
        hint = nav.power_hint(0.2)
        self.assertEqual(29, hint["pairs_needed"]["10%"])
        self.assertIsNone(nav.power_hint(None))
        self.assertAlmostEqual(0.0390625, nav.sign_test(8, 1))
        self.assertIsNone(nav.sign_test(0, 0))

    def test_log_ratio_is_unbiased_for_identical_arms(self):
        # (a-b)/a 的均值在同分布时偏负;对数比的几何节省率应接近 0
        stats = nav.paired_stats([(100, 200), (200, 100)])
        self.assertAlmostEqual(0.0, stats["mean_log_ratio"])
        self.assertAlmostEqual(0.0, stats["geometric_saving_rate"])
        self.assertEqual(2, stats["n_ratio"])
        self.assertEqual(1, nav.paired_stats([(0, 3), (2, 1)])["n_ratio"])

    def test_output_directory_with_legacy_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trials = []
            for condition, extra in (("baseline", 3), ("fugue", 1)):
                folder = root / ("task-1-" + condition)
                folder.mkdir()
                events = [command(str(i), "bash -lc 'cat f%d.py'" % i, "z" * 10) for i in range(extra)]
                events.append({"type": "item.completed", "item": {"id": "e", "type": "file_change",
                                                                   "changes": [{"path": "f.py", "kind": "update"}]}})
                (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
                trials.append({"task": "task", "repeat": 1, "condition": condition, "accepted": True})
            (root / "report.json").write_text(json.dumps({"trials": trials}))
            run = subprocess.run([sys.executable, "-B", str(Path(nav.__file__)), str(root)],
                                 capture_output=True, text=True)
            self.assertEqual(0, run.returncode, run.stderr)
            result = json.loads(run.stdout)
            stats = result["comparisons"]["workflow_effect"]["metrics"]["nav_commands_before_edit"]
            self.assertEqual(1, stats["n"])
            self.assertEqual(2, stats["sum_difference"])
            self.assertEqual(0, result["missing_event_logs"])


if __name__ == "__main__":
    unittest.main()
