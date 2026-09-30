#!/usr/bin/env python3
"""
[INPUT]: 依赖 json, os, pathlib, shutil, subprocess, sys, tempfile, unittest, geb_arch, geb_check, geb_scaffold, geb_sync, geb_staged, grade_comprehension
[OUTPUT]: 提供同步、依赖、暂存提交和评分负对照回归
[POS]: fugue-docs 评测包-真实写入与边界行为测试
[PROTOCOL]: 变更时同步 evals/FOLDER_INDEX.md 并运行 unittest discovery
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "evals")]
import geb_arch as arch
import geb_check as check
import geb_scaffold as scaffold
import geb_sync as sync
import geb_staged as staged
import grade_comprehension as grader


def header(deps="(未检出外部依赖)"):
    return ('"""\n[INPUT]: 依赖 ' + deps + '\n[OUTPUT]: 提供 main()\n'
            '[POS]: audit example\n[PROTOCOL]: update indexes\n"""\n')


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="geb-boundary-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_tool(self, name, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / name),
                               str(self.root), *args], capture_output=True, text=True)

    def git(self, *args, expect=0):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        result = subprocess.run(["git", "-c", "commit.gpgsign=false",
                                 "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                 *args], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(expect, result.returncode, result.stdout + result.stderr)
        return result.stdout

    def fixture(self):
        shutil.copytree(ROOT / "evals/fixtures/fixture-b", self.root, dirs_exist_ok=True)

    def init_git(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")

    def test_preserve_non_code_rows_and_semantics(self):
        self.write("app.py", header() + "def main():\n    pass\n")
        self.write("README.md", "# Guide\n")
        self.write("config.json", "{}\n")
        index = self.write("PROJECT_INDEX.md", "# Project\n## 根目录文件\n| 文件 | 职责 |\n|---|---|\n| app.py | Entry description |\n| README.md | Manual description |\n| config.json | Configuration description |\n")
        sync.sync(str(self.root))
        result = index.read_text()
        for text in ("Entry description", "Manual description", "Configuration description"):
            self.assertIn(text, result)
        self.assertEqual([], sync.sync(str(self.root)))

    def test_js_relative_shared_edges(self):
        self.write("services/run.js", "import {save} from '../storage/store.js';\n")
        self.write("storage/store.js", "export function save() {}\n")
        report = arch.build_report(str(self.root))
        dirs, _, analyses, _ = arch.load_project(str(self.root))
        self.assertEqual([{"from": "services", "to": "storage"}], report["edges"])
        self.assertEqual([("services", "storage")], scaffold.mermaid_edges(str(self.root), dirs, analyses))
        self.assertEqual("storage/store.js", report["dependency_evidence"][0]["target"])

    def test_no_fabricated_relative_target(self):
        self.write("services/run.js", "import x from '../missing.js';\n")
        report = arch.build_report(str(self.root))
        self.assertEqual([], report["edges"])
        self.assertEqual("../missing.js", report["unresolved_imports"][0]["import"])

    def test_all_imports_and_exports_retained(self):
        deps = ["m%d" % i for i in range(15)]
        path = self.write("app.py", "\n".join("import " + dep for dep in deps)
                          + "\n" + "\n".join("def f%d(): pass" % i for i in range(15)))
        inputs, outputs = scaffold.analyze_file(str(path))
        self.assertEqual(deps, inputs)
        self.assertEqual(15, len(outputs))

    def test_strict_detects_eleventh_dependency(self):
        deps = ["os", "sys", "json", "re", "math", "time", "pathlib", "typing", "argparse", "collections"]
        self.write("app.py", header(", ".join(deps)) + "\n".join("import " + x for x in deps)
                   + "\nfrom storage.store import save\n")
        self.write("storage/store.py", header() + "def save(): pass\n")
        self.write("PROJECT_INDEX.md", "# Project\napp.py storage/store.py\n")
        violations, _ = check.run_checks(str(self.root), strict=True)
        self.assertTrue(any(v["level"] == "L3" and "storage" in v["problem"] for v in violations))

    def test_delete_last_file_detect_and_sync(self):
        self.fixture()
        sync.sync(str(self.root))
        self.init_git()
        (self.root / "storage/store.py").unlink()
        violations, _ = check.run_checks(str(self.root), strict=True, complete=True)
        self.assertTrue(any(v["path"] == "storage/store.py" for v in violations))
        changes = sync.sync(str(self.root), scope=sync.git_changed(str(self.root)))
        self.assertIn("[L2] storage/FOLDER_INDEX.md", changes)
        self.assertNotIn("| store.py |", (self.root / "storage/FOLDER_INDEX.md").read_text())
        self.assertEqual([], sync.sync(str(self.root)))

    def test_root_ghost_detected(self):
        self.write("PROJECT_INDEX.md", "# Project\n| 文件 | 职责 |\n|---|---|\n| old.py | deleted |\n")
        violations, _ = check.run_checks(str(self.root))
        self.assertTrue(any(v["path"] == "old.py" for v in violations))

    def test_last_root_file_sync(self):
        index = self.write("PROJECT_INDEX.md", "# Project\n## 根目录文件\n| 文件 | 职责 |\n|---|---|\n| old.py | deleted |\n")
        self.assertTrue(sync.sync(str(self.root)))
        self.assertNotIn("old.py", index.read_text())
        self.assertEqual([], sync.sync(str(self.root)))

    def test_linked_code_row_preserves_duty(self):
        self.write("app.py", header())
        index = self.write("PROJECT_INDEX.md", "# Project\n## 文件清单\n| 文件 | 职责 |\n|---|---|\n| [app.py](app.py) | Keep this duty |\n")
        sync.sync(str(self.root))
        self.assertEqual(1, index.read_text().count("Keep this duty"))
        self.assertNotIn("TODO", index.read_text())
        self.assertEqual([], sync.sync(str(self.root)))

    def test_same_basename_not_coverage(self):
        for path in ("a/util.py", "b/util.py"):
            self.write(path, header())
        self.write("PROJECT_INDEX.md", "# Project\nModules a and b\n| 文件 | 职责 |\n|---|---|\n| a/util.py | only first |\n")
        violations, _ = check.run_checks(str(self.root), strict=True, complete=True)
        self.assertTrue(any(v["path"] == "b/util.py" for v in violations))

    def test_unicode_and_control_paths_in_git_scope(self):
        self.write("base.py", header())
        paths = ["services/用户.py", "space name.py", "tab\tname.py", "line\nname.py"]
        for path in paths:
            self.write(path, header())
        self.init_git()
        for path in paths:
            self.write(path, header() + "import json\n")
        scope = sync.git_changed(str(self.root))
        self.assertEqual(set(paths), scope["files"])
        changes = sync.sync(str(self.root), scope=scope)
        self.assertEqual(len(paths), len(changes))
        self.assertEqual([], sync.sync(str(self.root)))

    def test_git_rename_tracks_both_directories(self):
        self.write("old/a.py", header())
        self.init_git()
        (self.root / "new").mkdir()
        self.git("mv", "old/a.py", "new/a.py")
        scope = sync.git_changed(str(self.root))
        self.assertEqual({"old", "new"}, scope["dirs"])
        self.assertEqual({"new/a.py"}, scope["files"])

    def test_staged_bad_worktree_good_is_blocked(self):
        self.fixture()
        self.init_git()
        self.assertEqual(0, self.run_tool("geb_adapt.py", "--pre-commit").returncode)
        path = self.root / "utils.py"
        good = path.read_text()
        self.write("utils.py", "def main(): pass\n")
        self.git("add", "utils.py")
        path.write_text(good, encoding="utf-8")
        self.git("commit", "-qm", "bad snapshot", expect=1)
        self.assertIn("[INPUT]", self.git("show", "HEAD:utils.py"))

    def test_staged_good_worktree_bad_is_allowed(self):
        self.fixture()
        self.init_git()
        self.write("utils.py", (self.root / "utils.py").read_text() + "\n# staged change\n")
        self.git("add", "utils.py")
        self.write("utils.py", "def main(): pass\n")
        self.assertEqual([], staged.check_staged(str(self.root), True, True)["violations"])
        self.assertNotIn("[INPUT]", (self.root / "utils.py").read_text())

    def test_cycles_preserve_direction(self):
        edges = [("a", "c"), ("c", "b"), ("b", "a")]
        cycles = arch.find_cycles(edges)
        self.assertTrue(cycles)
        self.assertTrue(all(edge in edges for cycle in cycles for edge in zip(cycle, cycle[1:])))

    def test_dense_acyclic_graph(self):
        edges = [(str(a), str(b)) for a in range(60) for b in range(a + 1, 60)]
        self.assertEqual([], arch.find_cycles(edges))

    def test_adapter_without_copy_has_no_invalid_command(self):
        result = self.run_tool("geb_adapt.py", "--tool", "codex")
        self.assertEqual(0, result.returncode)
        self.assertFalse((self.root / "scripts/geb/geb_scaffold.py").exists())
        self.assertNotIn("python3 scripts/geb/geb_scaffold.py .", result.stdout)
        self.assertIn("--copy-tools", result.stdout)

    def test_copied_tools_can_run(self):
        result = self.run_tool("geb_adapt.py", "--copy-tools")
        self.assertEqual(0, result.returncode, result.stderr)
        arch_cmd = subprocess.run([sys.executable, "-B", str(self.root / "scripts/geb/geb_arch.py"),
                                   str(self.root), "--json"], capture_output=True, text=True)
        self.assertEqual(0, arch_cmd.returncode, arch_cmd.stderr)
        self.assertTrue(json.loads(arch_cmd.stdout)["files"])

    def test_missing_token_counts_stay_unknown(self):
        spec = grader.read_json(str(ROOT / "evals/comprehension_fixture_b.json"))
        keywords = " ".join(word for q in spec["questions"] for point in q["rubric"] for word in point.get("any", []))
        answers = {str(q["id"]): keywords for q in spec["questions"]}
        runs = [grader.grade_run({"condition": c, "answers": answers}, spec) for c in ("docs_only", "code_only")]
        result = grader.compare_runs(runs, spec)
        self.assertIsNone(result["healthy"])
        self.assertIsNone(result["docs_vs_code_token_ratio"])

    def test_invalid_token_counts_rejected(self):
        for value in (-1, 0, True, "100", 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                grader.grade_run({"token_count": value, "answers": {}}, {"questions": []})

    def test_unreviewed_keywords_cannot_prove_quality(self):
        spec = {"questions": [{"id": 1, "rubric": [{"any": ["right"], "weight": 1}]}]}
        runs = [grader.grade_run({"condition": c, "token_count": t, "answers": {"1": "not right"}}, spec)
                for c, t in (("docs_only", 10), ("code_only", 100))]
        result = grader.compare_runs(runs, spec)
        self.assertTrue(result["proxy_healthy"])
        self.assertIsNone(result["healthy"])
        self.assertTrue(result["review_required"])


if __name__ == "__main__":
    unittest.main()
