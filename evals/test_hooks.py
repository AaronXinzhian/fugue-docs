#!/usr/bin/env python3
"""
[INPUT]: 依赖 json, os, pathlib, subprocess, sys, tempfile, unittest, geb_hook, geb_metrics
[OUTPUT]: 提供 Claude Code 钩子回归:按工具调用归属、静默放行、新文件骨架、缺口只提示一次、各类不该写的文件、阈值迁移、改名、多会话、对话记录计量
[POS]: fugue-docs 评测包-程序化回环与计量可信度检查;不调用模型
[PROTOCOL]: 修改钩子行为、提示文本或计量口径时同步本测试与 README 钩子说明
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import geb_hook  # noqa: E402
import geb_metrics  # noqa: E402

HOOK = ROOT / "scripts" / "geb_hook.py"
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")

L1 = """# demo — 项目索引(L1)

## 定位
示例项目。

## 目录结构
```text
demo/
└── pkg/   # 业务包
```

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| app.py | 入口 | main() |
| pkg/core.py | 核心计算 | add() |
"""

APP = '''"""
[INPUT]: 依赖 pkg.core
[OUTPUT]: 提供 main()
[POS]: 入口
[PROTOCOL]: 变更时更新此头部,然后检查上级 PROJECT_INDEX.md
"""
from pkg.core import add


def main():
    return add(1, 2)
'''

CORE = '''"""
[INPUT]: 依赖 (未检出外部依赖)
[OUTPUT]: 提供 add()
[POS]: 核心计算
[PROTOCOL]: 变更时更新此头部,然后检查上级 PROJECT_INDEX.md
"""


def add(a, b):
    return a + b
'''

HEADER = '"""\n[INPUT]: 依赖 x\n[OUTPUT]: 提供 f()\n[POS]: p\n[PROTOCOL]: u\n"""\n'


def usage_line(message_id, output, cache_write=0, cache_read=0, uncached=10, sidechain=False, model="m"):
    return {"type": "assistant", "isSidechain": sidechain, "message": {
        "id": message_id, "role": "assistant", "model": model,
        "usage": {"input_tokens": uncached, "cache_creation_input_tokens": cache_write,
                  "cache_read_input_tokens": cache_read, "output_tokens": output}}}


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.root = base / "proj"
        self.data = base / "data"
        self.transcript = base / "transcript.jsonl"
        self.transcript.write_text("")
        self.env = dict(os.environ, FUGUE_DATA_DIR=str(self.data))
        self.env.pop("CLAUDE_PROJECT_DIR", None)
        self.env.pop("FUGUE_HOOK_QUIET", None)
        self.calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def make_project(self, git=True):
        (self.root / "pkg").mkdir(parents=True)
        (self.root / "PROJECT_INDEX.md").write_text(L1, encoding="utf-8")
        (self.root / "app.py").write_text(APP, encoding="utf-8")
        (self.root / "pkg" / "core.py").write_text(CORE, encoding="utf-8")
        if git:
            for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
                subprocess.run(command, cwd=self.root, env=GIT_ENV, check=True, capture_output=True)

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.root, env=GIT_ENV, check=True,
                              capture_output=True, text=True).stdout

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def hook(self, event, session="s1", **extra):
        payload = dict(session_id=session, transcript_path=str(self.transcript), cwd=str(self.root),
                       hook_event_name=event, **extra)
        run = subprocess.run([sys.executable, "-B", str(HOOK), event], input=json.dumps(payload),
                             capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        log = self.data / "hook-errors.log"
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")
        return json.loads(run.stdout) if run.stdout.strip() else None

    def write(self, rel, text):
        """不经过工具的写入:用户、编辑器或其他程序。"""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    def edit(self, rel, text, session="s1"):
        """模型用 Write/Edit 工具写文件:先触发 PreToolUse。"""
        self.hook("pre-tool", session=session, tool_name="Write", tool_input={"file_path": str(self.root / rel)})
        self.write(rel, text)

    def bash(self, action, command="python3 tool.py", session="s1"):
        """模型运行命令:命令前后各触发一次钩子。"""
        self.calls += 1
        extra = dict(tool_name="Bash", tool_input={"command": command}, tool_use_id="t%d" % self.calls)
        self.hook("pre-tool", session=session, **extra)
        action()
        self.hook("post-tool", session=session, **extra)

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def check_clean(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "geb_check.py"), str(self.root),
                              "--strict", "--complete"], capture_output=True, text=True)
        return run.returncode, run.stdout


class MaintenanceTests(HookCase):
    def test_session_start_only_adds_a_navigation_hint(self):
        self.make_project()
        output = self.hook("session-start", source="startup")
        self.assertEqual({"hookSpecificOutput"}, set(output))
        self.assertIn("PROJECT_INDEX.md", output["hookSpecificOutput"]["additionalContext"])
        self.env["FUGUE_HOOK_QUIET"] = "1"
        self.assertIsNone(self.hook("session-start", session="s2", source="startup"))

    def test_projects_without_indexes_are_untouched(self):
        self.root.mkdir(parents=True)
        self.edit("main.py", "print(1)\n")
        self.assertIsNone(self.hook("session-start", source="startup"))
        self.assertIsNone(self.hook("stop"))
        self.assertEqual("print(1)\n", self.read("main.py"))
        self.assertFalse(self.data.exists())

    def test_body_only_change_is_silent(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/core.py", CORE.replace("return a + b", "return b + a"))
        self.assertIsNone(self.hook("stop"))
        self.assertEqual(L1, self.read("PROJECT_INDEX.md"))

    def test_new_file_gets_skeleton_and_one_prompt(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/util.py", "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n")
        output = self.hook("stop")
        self.assertEqual("block", output["decision"])
        reason = output["reason"]
        self.assertEqual(1, reason.count("pkg/util.py:"))
        self.assertIn("职责", reason)
        util = self.read("pkg/util.py")
        self.assertIn("[OUTPUT]: 提供 clamp()", util)
        self.assertIn("[POS]: TODO", util)
        self.assertIn("| pkg/util.py | TODO(语义):职责 | clamp() |", self.read("PROJECT_INDEX.md"))
        self.assertIsNone(self.hook("stop", stop_hook_active=True))
        self.assertIsNone(self.hook("stop"))
        # 模型只改函数体、没补占位:不再重复提示
        self.edit("pkg/util.py", util.replace("max(lo, min(x, hi))", "min(max(x, lo), hi)"))
        self.assertNotIn("decision", self.hook("stop") or {})
        self.edit("pkg/util.py", self.read("pkg/util.py").replace("TODO(语义):本文件在系统中的定位与职责", "数值工具"))
        self.write("PROJECT_INDEX.md", self.read("PROJECT_INDEX.md").replace("TODO(语义):职责", "数值裁剪"))
        self.assertIsNone(self.hook("stop"))
        code, report = self.check_clean()
        self.assertEqual(0, code, report)

    def test_bash_written_files_are_attributed(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.bash(lambda: self.write("pkg/gen.py", "def made():\n    return 1\n"), "cat > pkg/gen.py")
        self.assertIn("pkg/gen.py", self.hook("stop")["reason"])

    def test_export_prompt_follows_the_last_reviewed_output_line(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/core.py", CORE + "\n\ndef sub(a, b):\n    return a - b\n")
        self.assertIn("pkg/core.py:导出变化(+sub())", self.hook("stop")["reason"])
        self.assertIn("| pkg/core.py | 核心计算 | add(), sub() |", self.read("PROJECT_INDEX.md"))
        self.assertIsNone(self.hook("stop"))
        self.edit("pkg/core.py", self.read("pkg/core.py").replace("[OUTPUT]: 提供 add()", "[OUTPUT]: 提供 add(), sub()"))
        self.assertIsNone(self.hook("stop"))
        self.edit("pkg/core.py", self.read("pkg/core.py").replace("def add(", "def plus("))
        reason = self.hook("stop")["reason"]
        self.assertIn("+plus()", reason)
        self.assertIn("-add()", reason)

    def test_deleted_file_is_removed_from_the_index_silently(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("app.py", APP.replace("from pkg.core import add\n", "").replace("return add(1, 2)", "return 3"))
        self.bash(lambda: (self.root / "pkg" / "core.py").unlink(), "rm pkg/core.py")
        self.assertNotIn("decision", self.hook("stop") or {})
        self.assertNotIn("pkg/core.py", self.read("PROJECT_INDEX.md"))
        self.assertIn("[INPUT]: 依赖 (未检出外部依赖)", self.read("app.py"))

    def test_new_top_level_directory_in_large_project(self):
        self.make_project()
        for i in range(21):  # 超过小项目阈值,目录需要自己的 L2
            self.write("pkg/m%02d.py" % i, HEADER + "def f():\n    return %d\n" % i)
        self.write("pkg/FOLDER_INDEX.md", "# pkg\n\n## 模块定位\n业务包\n\n## 文件清单\n| 文件 | 职责 | 关键导出 |\n"
                   "|------|------|----------|\n" + "".join("| m%02d.py | 模块 | f() |\n" % i for i in range(21))
                   + "| core.py | 核心计算 | add() |\n")
        self.commit("grow")
        self.hook("session-start", source="startup")
        self.edit("api/routes.py", "def handler():\n    return 1\n")
        reason = self.hook("stop")["reason"]
        self.assertTrue((self.root / "api" / "FOLDER_INDEX.md").is_file())
        self.assertIn("api/FOLDER_INDEX.md:补模块定位", reason)
        self.assertIn("PROJECT_INDEX.md:目录结构未提及新目录 api", reason)
        self.assertIn("api/routes.py", reason)

    def test_subproject_files_use_their_own_index(self):
        self.make_project()
        sub = "services/billing"
        self.write(sub + "/PROJECT_INDEX.md", "# billing\n\n## 文件清单\n| 文件 | 职责 | 关键导出 |\n|------|------|----------|\n"
                   "| charge.py | 扣费 | charge() |\n")
        self.write(sub + "/charge.py", HEADER.replace("f()", "charge()") + "\n\ndef charge():\n    return 1\n")
        self.write("PROJECT_INDEX.md", L1.replace("└── pkg/   # 业务包", "├── pkg/   # 业务包\n└── services/billing/  # 子项目"))
        self.commit("sub")
        self.hook("session-start", source="startup")
        self.edit(sub + "/refund.py", "def refund():\n    return 1\n")
        reason = self.hook("stop")["reason"]
        self.assertIn("services/billing/refund.py", reason)
        self.assertIn("services/billing/PROJECT_INDEX.md", reason)
        self.assertNotIn("模块定位", reason)
        self.assertIn("| refund.py | TODO(语义):职责 | refund() |", self.read(sub + "/PROJECT_INDEX.md"))
        self.assertNotIn("refund", self.read("PROJECT_INDEX.md"))

    def test_non_git_project(self):
        self.make_project(git=False)
        self.write("services/billing/PROJECT_INDEX.md", "# billing\n\n## 文件清单\n| 文件 | 职责 | 关键导出 |\n"
                   "|------|------|----------|\n")
        self.write("PROJECT_INDEX.md", L1.replace("└── pkg/   # 业务包", "├── pkg/   # 业务包\n└── services/billing/"))
        self.hook("session-start", source="startup")
        self.edit("pkg/util.py", "def clamp(x):\n    return x\n")
        self.bash(lambda: self.write("services/billing/charge.py", "def charge():\n    return 1\n"), "python3 gen.py")
        reason = self.hook("stop")["reason"]
        self.assertIn("pkg/util.py", reason)
        self.assertIn("services/billing/charge.py", reason)
        self.assertIn("[OUTPUT]: 提供 clamp()", self.read("pkg/util.py"))

    def test_bad_payload_never_blocks(self):
        for payload in ("not json", "[]", json.dumps({"session_id": "../../etc"})):
            run = subprocess.run([sys.executable, "-B", str(HOOK), "stop"], input=payload,
                                 capture_output=True, text=True, env=self.env)
            self.assertEqual(0, run.returncode)
            self.assertEqual("", run.stdout)
        run = subprocess.run([sys.executable, "-B", str(HOOK), "unknown"], input="{}",
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(0, run.returncode)

    def test_output_is_ascii_json(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/util.py", "def clamp(x):\n    return x\n")
        payload = dict(session_id="s1", transcript_path=str(self.transcript), cwd=str(self.root))
        run = subprocess.run([sys.executable, "-B", str(HOOK), "stop"], input=json.dumps(payload).encode(),
                             capture_output=True, env=self.env)
        run.stdout.decode("ascii")
        self.assertIn("pkg/util.py", json.loads(run.stdout)["reason"])


class AttributionTests(HookCase):
    """只处理模型通过工具写过的文件;每条对应独立复核里复现过的失败场景。"""

    def test_files_not_written_by_the_model_are_left_alone(self):
        self.make_project()
        self.write("pkg/draft.py", "def draft():\n    return 1\n")  # 会话前的草稿
        self.hook("session-start", source="startup")
        self.write("pkg/mine.py", "def mine():\n    return 1\n")  # 用户在会话中自己建的文件
        self.write("pkg/core.py", CORE + "\n\ndef sub(a, b):\n    return a - b\n")
        self.assertIsNone(self.hook("stop"))
        self.assertNotIn("[INPUT]", self.read("pkg/mine.py"))
        self.assertNotIn("[INPUT]", self.read("pkg/draft.py"))
        self.assertEqual(L1, self.read("PROJECT_INDEX.md"))

    def test_branch_switch_and_pull_are_not_attributed(self):
        self.make_project()
        self.git("checkout", "-qb", "feature")
        self.write("pkg/feat.py", "def feat():\n    return 1\n")
        self.commit("feature")
        self.git("checkout", "-q", "-")
        self.hook("session-start", source="startup")
        self.bash(lambda: self.git("checkout", "-q", "feature"), "git checkout feature")
        self.bash(lambda: self.write("pkg/feat.py", "def feat():\n    return 2\n"), "git stash pop")
        before = {rel: self.read(rel) for rel in ("pkg/feat.py", "app.py", "PROJECT_INDEX.md")}
        self.assertIsNone(self.hook("stop"))
        self.assertEqual(before, {rel: self.read(rel) for rel in before})

    def test_work_committed_in_the_same_turn_is_left_clean(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/util.py", "def clamp(x):\n    return x\n")
        self.bash(lambda: self.commit("model commit"), "git commit -am wip")
        self.assertIsNone(self.hook("stop"))
        self.assertEqual("", self.git("status", "--porcelain"))

    def test_uncommitted_edit_alongside_a_commit_is_still_maintained(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/core.py", CORE + "\n\ndef sub(a, b):\n    return a - b\n")
        self.edit("app.py", APP.replace("return add(1, 2)", "return add(2, 2)"))
        self.bash(lambda: (self.git("add", "app.py"), self.git("commit", "-qm", "app")), "git commit app.py")
        self.assertIn("pkg/core.py:导出变化(+sub())", self.hook("stop")["reason"])
        self.assertIn("| pkg/core.py | 核心计算 | add(), sub() |", self.read("PROJECT_INDEX.md"))

    def test_user_takes_over_a_file_between_turns(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/core.py", CORE.replace("return a + b", "return b + a"))
        self.assertIsNone(self.hook("stop"))
        self.commit("user commits the model's work")
        self.write("pkg/core.py", CORE.replace('"""\n\n\ndef add', '"""\nimport os\n\n\ndef add'))  # 用户自己的草稿
        self.hook("prompt", prompt="继续")
        self.edit("app.py", APP.replace("return add(1, 2)", "return add(2, 2)"))
        self.assertIsNone(self.hook("stop"))
        self.assertIn("[INPUT]: 依赖 (未检出外部依赖)", self.read("pkg/core.py"))

    def test_two_sessions_in_one_repository(self):
        self.make_project()
        self.hook("session-start", session="a", source="startup")
        self.hook("session-start", session="b", source="startup")
        self.edit("pkg/b_file.py", "def b_only():\n    return 1\n", session="b")
        self.edit("pkg/a_file.py", "def a_only():\n    return 1\n", session="a")
        reason = self.hook("stop", session="a")["reason"]
        self.assertIn("pkg/a_file.py", reason)
        self.assertNotIn("b_file", reason)
        self.assertNotIn("[INPUT]", self.read("pkg/b_file.py"))

    def test_symlink_to_outside_file_is_never_written(self):
        self.make_project()
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "shared.py").write_text("def shared():\n    return 1\n")
        os.symlink(str(outside / "shared.py"), str(self.root / "pkg" / "link.py"))
        self.hook("session-start", source="startup")
        self.edit("pkg/link.py", "def shared():\n    return 2\n")
        output = self.hook("stop") or {}
        self.assertEqual("def shared():\n    return 2\n", (outside / "shared.py").read_text())
        self.assertNotIn("link.py", output.get("reason", ""))

    def test_conflicts_and_syntax_errors_skip_only_that_file(self):
        self.make_project()
        self.hook("session-start", source="startup")
        conflicted = APP.replace("def main():", "<<<<<<< HEAD\ndef main():\n=======\ndef run():\n>>>>>>> other")
        self.edit("app.py", conflicted)
        self.edit("pkg/util.py", "def clamp(x):\n    return x\n")
        reason = self.hook("stop")["reason"]
        self.assertEqual(conflicted, self.read("app.py"))
        self.assertIn("pkg/util.py", reason)
        self.assertNotIn("app.py", reason)
        self.assertIn("| app.py | 入口 | main() |", self.read("PROJECT_INDEX.md"))  # 保留原导出列
        self.edit("app.py", APP.replace("return add(1, 2)", "return add(1, 2"))  # 语法错误
        self.hook("stop")
        self.assertIn("| app.py | 入口 | main() |", self.read("PROJECT_INDEX.md"))

    def test_merge_in_progress_writes_nothing(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/util.py", "def clamp(x):\n    return x\n")
        git_dir = Path(self.git("rev-parse", "--absolute-git-dir").strip())
        (git_dir / "MERGE_HEAD").write_text(self.git("rev-parse", "HEAD"))
        self.assertIsNone(self.hook("stop"))
        self.assertNotIn("[INPUT]", self.read("pkg/util.py"))
        (git_dir / "MERGE_HEAD").unlink()
        self.assertIn("pkg/util.py", self.hook("stop")["reason"])

    def test_non_utf8_bom_and_generated_files(self):
        self.make_project()
        self.hook("session-start", source="startup")
        gbk = "public class Legacy { // 旧代码\n}\n".encode("gbk")
        self.hook("pre-tool", tool_name="Write", tool_input={"file_path": str(self.root / "pkg" / "Legacy.java")})
        self.write("pkg/Legacy.java", gbk)
        self.edit("pkg/bom.py", "\ufeffdef bom():\n    return 1\n")
        self.edit("pkg/schema_pb2.py", "# Generated by the protocol buffer compiler.  DO NOT EDIT!\nX = 1\n")
        self.edit("pkg/report.py", '"""Handles auto-generated reports."""\n\n\ndef report():\n    return 1\n')
        reason = self.hook("stop")["reason"]
        self.assertEqual(gbk, (self.root / "pkg" / "Legacy.java").read_bytes())
        raw = (self.root / "pkg" / "bom.py").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf\"\"\"\n[INPUT]"))
        compile(raw.decode("utf-8-sig"), "bom.py", "exec")
        self.assertTrue(self.read("pkg/schema_pb2.py").startswith("# Generated"))
        self.assertNotIn("schema_pb2", reason)
        self.assertIn("pkg/report.py", reason)  # 文档里提到 auto-generated 不等于生成文件

    def test_crossing_the_small_project_limit_moves_duties(self):
        self.make_project()
        rows = "".join("| pkg/m%02d.py | 模块%02d | f() |\n" % (i, i) for i in range(18))
        self.write("PROJECT_INDEX.md", L1 + rows)
        for i in range(18):
            self.write("pkg/m%02d.py" % i, HEADER + "def f():\n    return %d\n" % i)
        self.commit("twenty files")
        self.hook("session-start", source="startup")
        self.edit("pkg/m18.py", "def g():\n    return 18\n")
        reason = self.hook("stop")["reason"]
        l2 = self.read("pkg/FOLDER_INDEX.md")
        self.assertIn("| m00.py | 模块00 |", l2)
        self.assertIn("| core.py | 核心计算 |", l2)
        self.assertNotIn("pkg/m00.py", self.read("PROJECT_INDEX.md"))  # 已迁入 L2,不留重复行
        self.assertIn("pkg/FOLDER_INDEX.md:补模块定位", reason)
        self.assertIn("pkg/m18.py", reason)

    def test_large_project_without_crossing_creates_no_l2(self):
        self.make_project()
        for i in range(21):
            self.write("legacy/m%02d.py" % i, HEADER + "def f():\n    return %d\n" % i)
        self.write("PROJECT_INDEX.md", L1.replace("└── pkg/   # 业务包", "├── pkg/\n└── legacy/")
                   + "".join("| legacy/m%02d.py | 旧模块 | f() |\n" % i for i in range(21)))
        self.write("pkg/FOLDER_INDEX.md", "# pkg\n\n## 模块定位\n业务包\n\n## 文件清单\n| 文件 | 职责 | 关键导出 |\n"
                   "|------|------|----------|\n| core.py | 核心计算 | add() |\n")
        self.commit("partially migrated")
        self.hook("session-start", source="startup")
        self.edit("pkg/core.py", CORE.replace("return a + b", "return b + a"))
        self.assertIsNone(self.hook("stop"))
        self.assertFalse((self.root / "legacy" / "FOLDER_INDEX.md").exists())

    def test_rename_keeps_the_duty_only_for_similar_content(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.bash(lambda: (self.root / "pkg" / "core.py").rename(self.root / "pkg" / "calc.py"), "mv pkg/core.py pkg/calc.py")
        self.edit("app.py", APP.replace("pkg.core", "pkg.calc"))
        self.hook("stop")
        index = self.read("PROJECT_INDEX.md")
        self.assertIn("| pkg/calc.py | 核心计算 |", index)
        self.assertNotIn("pkg/core.py", index)

    def test_unrelated_replacement_does_not_inherit_a_duty(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.bash(lambda: (self.root / "pkg" / "core.py").unlink(), "rm pkg/core.py")
        self.edit("pkg/report_export.py", "import csv\n\n\ndef export_rows(rows, path):\n"
                  "    with open(path, 'w') as f:\n        csv.writer(f).writerows(rows)\n")
        self.edit("app.py", APP.replace("from pkg.core import add\n", "").replace("return add(1, 2)", "return 3"))
        reason = self.hook("stop")["reason"]
        self.assertIn("| pkg/report_export.py | TODO(语义):职责 |", self.read("PROJECT_INDEX.md"))
        self.assertIn("pkg/report_export.py", reason)

    def test_failed_edit_does_not_claim_the_users_file(self):
        self.make_project()
        self.write("pkg/core.py", CORE.replace("return a + b", "return b + a"))  # 用户的草稿
        self.hook("session-start", source="startup")
        self.hook("pre-tool", tool_name="Edit", tool_input={"file_path": str(self.root / "pkg/core.py")})  # 编辑失败,文件未变
        self.assertIsNone(self.hook("stop"))
        self.write("pkg/core.py", CORE + "\n\ndef user_fn():\n    return 1\n")  # 用户继续改
        self.hook("prompt", prompt="继续")
        self.edit("app.py", APP.replace("return add(1, 2)", "return add(2, 2)"))
        self.assertIsNone(self.hook("stop"))
        self.assertEqual(L1, self.read("PROJECT_INDEX.md"))

    def test_bulk_copy_by_command_is_not_maintained(self):
        self.make_project()
        self.hook("session-start", source="startup")

        def copy_sdk():
            for i in range(30):
                self.write("third_party/sdk/m%02d.py" % i, "def f%d():\n    return %d\n" % (i, i))
            self.write("pkg/glue.py", "def glue():\n    return 1\n")
        self.bash(copy_sdk, "cp -r ~/sdk third_party/sdk")
        self.assertIsNone(self.hook("stop"))
        self.assertNotIn("[INPUT]", self.read("third_party/sdk/m00.py"))
        self.assertFalse((self.root / "third_party" / "sdk" / "FOLDER_INDEX.md").exists())

    def test_other_session_writes_during_a_long_command(self):
        self.make_project()
        self.hook("session-start", session="a", source="startup")
        self.hook("session-start", session="b", source="startup")
        self.bash(lambda: self.edit("pkg/b_file.py", "def b_only():\n    return 1\n", session="b"),
                  "pytest -q", session="a")
        self.assertIsNone(self.hook("stop", session="a"))
        self.assertNotIn("[INPUT]", self.read("pkg/b_file.py"))
        self.assertIn("pkg/b_file.py", self.hook("stop", session="b")["reason"])

    def test_failed_command_is_recorded_at_stop(self):
        self.make_project()
        self.hook("session-start", source="startup")
        extra = dict(tool_name="Bash", tool_input={"command": "eslint --fix || exit 1"}, tool_use_id="failed")
        self.hook("pre-tool", **extra)
        self.write("pkg/fixed.py", "def fixed():\n    return 1\n")  # 命令失败,没有 PostToolUse
        self.assertIn("pkg/fixed.py", self.hook("stop")["reason"])
        self.assertEqual([], [n for n in os.listdir(self.data / "sessions") if ".bash-" in n])

    def test_skipped_turn_keeps_the_models_edits(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/core.py", CORE.replace("return a + b", "return b + a"))
        self.assertIsNone(self.hook("stop"))
        git_dir = Path(self.git("rev-parse", "--absolute-git-dir").strip())
        self.hook("prompt", prompt="加一个减法")
        self.edit("pkg/core.py", CORE + "\n\ndef sub(a, b):\n    return a - b\n")
        (git_dir / "MERGE_HEAD").write_text(self.git("rev-parse", "HEAD"))
        self.assertIsNone(self.hook("stop"))  # 合并进行中,本轮不写
        (git_dir / "MERGE_HEAD").unlink()
        self.hook("prompt", prompt="继续")
        self.assertIn("+sub()", self.hook("stop")["reason"])

    def test_git_mv_keeps_the_duty(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.bash(lambda: self.git("mv", "pkg/core.py", "pkg/calc.py"), "git mv pkg/core.py pkg/calc.py")
        self.edit("app.py", APP.replace("pkg.core", "pkg.calc"))
        self.hook("stop")
        self.assertIn("| pkg/calc.py | 核心计算 |", self.read("PROJECT_INDEX.md"))

    def test_failing_git_skips_the_turn_without_losing_records(self):
        self.make_project()
        os.environ["FUGUE_DATA_DIR"] = str(self.data)
        try:
            self.hook("session-start", session="s9", source="startup")
            self.edit("pkg/wip.py", "def wip():\n    return 1\n", session="s9")
            payload = dict(session_id="s9", cwd=str(self.root), transcript_path=str(self.transcript))
            original = geb_hook.dirty_names
            geb_hook.dirty_names = lambda root, head: None  # 模拟 git 超时
            try:
                self.assertIsNone(geb_hook.stop(payload))
            finally:
                geb_hook.dirty_names = original
            self.assertNotIn("[INPUT]", self.read("pkg/wip.py"))
            self.assertIn("pkg/wip.py", geb_hook.read_json(geb_hook.session_file("s9", ".json"))["authored"])
            self.assertIn("pkg/wip.py", geb_hook.stop(payload)["reason"])
        finally:
            os.environ.pop("FUGUE_DATA_DIR", None)


class MeteringTests(HookCase):
    def append(self, *entries):
        with self.transcript.open("a") as f:
            for entry in entries:
                f.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")

    def record(self):
        files = list((self.data / "metrics").glob("*.json"))
        self.assertEqual(1, len(files))
        return json.loads(files[0].read_text())

    def test_usage_is_deduplicated_by_message_and_interval_based(self):
        self.make_project()
        self.append(usage_line("old", 999))  # 恢复会话:开始前已有的用量不计入
        self.hook("session-start", source="resume")
        self.append(usage_line("m1", 1, cache_write=2000), usage_line("m1", 120, cache_write=2000),
                    usage_line("m2", 30, cache_read=1000, uncached=5, sidechain=True, model="s"),
                    "not json", {"type": "assistant", "message": {"id": "x", "role": "assistant",
                                                                  "usage": {"input_tokens": -1}}})
        self.hook("stop")
        record = self.record()
        self.assertEqual("measured_interval", record["status"])
        usage = record["usage"]
        self.assertEqual(150, usage["output_tokens"])
        self.assertEqual(1000, usage["cached_input_tokens"])
        self.assertEqual(2015, usage["uncached_input_tokens"])
        self.assertEqual(3015, usage["input_tokens"])
        self.assertEqual(3165, usage["total_tokens"])
        self.assertEqual(2165, usage["uncached_plus_output"])
        self.assertEqual(2, usage["messages"])
        self.assertEqual(1, record["sidechain_messages"])
        self.assertEqual(["m", "s"], record["models"])
        summary = geb_metrics.summarize(self.data / "metrics")
        self.assertEqual(3165, summary["actual_total_tokens"])
        self.assertIsNone(summary["paired_token_difference"])

    def test_no_new_messages_is_unknown_not_zero(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.hook("stop")
        record = self.record()
        self.assertIsNone(record["usage"])
        self.assertEqual("no_usage_entries", record["status"])
        self.append(usage_line("m1", 5))
        self.hook("session-end")
        self.assertEqual(5, self.record()["usage"]["output_tokens"])

    def test_truncated_transcript_is_a_reset(self):
        self.make_project()
        self.append(usage_line("a", 5), usage_line("b", 5))
        self.hook("session-start", source="resume")
        self.transcript.write_text(json.dumps(usage_line("c", 1)) + "\n")
        self.hook("stop")
        self.assertEqual("counter_reset", self.record()["status"])
        self.assertIsNone(self.record()["usage"])

    def test_hook_installed_mid_session_measures_from_then_on(self):
        self.make_project()
        self.append(usage_line("a", 5))
        self.hook("stop")
        self.append(usage_line("b", 7))
        self.hook("stop")
        record = self.record()
        self.assertEqual("hook_installed_mid_session", record["coverage_start"])
        self.assertEqual(7, record["usage"]["output_tokens"])

    def test_block_cost_is_recorded(self):
        self.make_project()
        self.hook("session-start", source="startup")
        self.edit("pkg/util.py", "def clamp(x):\n    return x\n")
        reason = self.hook("stop")["reason"]
        maintenance = self.record()["maintenance"]
        self.assertEqual(1, maintenance["blocks"])
        self.assertEqual(len(reason), maintenance["block_chars"])
        self.assertGreaterEqual(maintenance["auto_writes"], 2)


class PluginTests(unittest.TestCase):
    def test_plugin_hooks_point_at_the_hook_script(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        events = config["hooks"]
        expected = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "PreToolUse": "pre-tool",
                    "PostToolUse": "post-tool", "Stop": "stop", "SessionEnd": "session-end"}
        self.assertEqual(set(expected), set(events))
        for event, argument in expected.items():
            command = events[event][0]["hooks"][0]["command"]
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/geb_hook.py", command)
            self.assertTrue(command.endswith(" " + argument))
            self.assertIn(argument, geb_hook.HANDLERS)
        self.assertIn("Bash", events["PreToolUse"][0]["matcher"])
        self.assertIn("Write", events["PreToolUse"][0]["matcher"])
        self.assertEqual("Bash", events["PostToolUse"][0]["matcher"])

    def test_skill_body_stays_small(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        body = text.split("---", 2)[2]
        self.assertLess(len(body), 2600, "SKILL.md 正文会在每次调用时进入上下文,保持精简")
        self.assertIn("references/manual-workflow.md", body)


if __name__ == "__main__":
    unittest.main()
