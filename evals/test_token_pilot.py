#!/usr/bin/env python3
"""
[INPUT]: 依赖 io, json, os, pathlib, shutil, subprocess, sys, tarfile, tempfile, types, unittest, unittest.mock, run_token_pilot
[OUTPUT]: 提供试点配对块、三组/A-A 汇总、成本口径、索引剥离、失败保留、外部源码、Claude 用量与隔离、假 Codex/Claude 端到端回归
[POS]: fugue-docs 评测包-实验设计与汇总可信度检查;不调用模型
[PROTOCOL]: 修改设计、汇总、剥离或 token 口径时同步本测试
"""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_token_pilot as pilot

TWO_ARM = [("baseline", "fugue", "workflow_effect")]
ROOT_DOCUTILS_TASKS = pilot.ROOT / "evals" / "fixtures" / "docutils-pilot" / "tasks.json"
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


FAKE_CLAUDE = r"""#!{python}
import json, os, subprocess, sys
prompt = sys.stdin.read()
argv = sys.argv
workspace = os.getcwd()
plugin = argv[argv.index("--plugin-dir") + 1] if "--plugin-dir" in argv else None
mode = os.environ.get("FAKE_MODE", "ok")
indexed = os.path.isfile(os.path.join(workspace, "PROJECT_INDEX.md"))
def emit(event):
    print(json.dumps(event), flush=True)
def hook(name):
    payload = {"session_id": "fake-session", "cwd": workspace, "hook_event_name": name}
    subprocess.run([sys.executable, os.path.join(plugin, "scripts", "geb_hook.py"), name],
                   input=json.dumps(payload), text=True, capture_output=True, cwd=workspace)
plugins = [{"name": "fugue-docs", "path": plugin}] if plugin and mode != "no-plugin" else []
emit({"type": "system", "subtype": "init", "model": "fake", "claude_code_version": "0.0-fake",
      "permissionMode": "acceptEdits", "plugins": plugins, "tools": ["Bash", "Read", "Edit"], "mcp_servers": []})
emit({"type": "probe", "argv": argv, "prompt": prompt, "home": os.environ["HOME"],
      "config": os.environ.get("CLAUDE_CONFIG_DIR"), "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"),
      "leak": os.environ.get("CLAUDE_FAKE_LEAK"), "fugue_data": os.environ.get("FUGUE_DATA_DIR")})
if plugin:
    hook("session-start")
def tool(index, name, data, output, error=False):
    emit({"type": "assistant", "message": {"id": "m%d" % index, "content": [
        {"type": "tool_use", "id": "t%d" % index, "name": name, "input": data}],
        "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 5}}})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t%d" % index, "content": output, "is_error": error}]}})
if indexed:
    tool(1, "Read", {"file_path": os.path.join(workspace, "PROJECT_INDEX.md")}, "x" * 50)
else:
    tool(1, "Grep", {"pattern": "VALUE", "path": workspace}, "y" * 400)
    tool(2, "Read", {"file_path": os.path.join(workspace, "app.py")}, "y" * 400)
tool(3, "Edit", {"file_path": os.path.join(workspace, "app.py")}, "String not found", error=True)
subprocess.run(["git", "apply", os.environ["FAKE_PATCH"]], cwd=workspace, check=True)
with open(os.path.join(workspace, "tests", "test_other.py"), "a") as f:
    f.write("\nclass Broken(unittest.TestCase):\n    def test_broken(self):\n        self.fail('model edit')\n")
with open(os.path.join(workspace, "tests", "test_new.py"), "w") as f:
    f.write("import unittest\nclass New(unittest.TestCase):\n    def test_new(self):\n        pass\n")
tool(4, "Edit", {"file_path": os.path.join(workspace, "app.py")}, "ok")
if mode == "commit":
    for command in (["git", "add", "-A"], ["git", "-c", "user.name=m", "-c", "user.email=m@example.invalid",
                                          "-c", "commit.gpgsign=false", "commit", "-qm", "model commit"]):
        subprocess.run(command, cwd=workspace, check=True, capture_output=True)
if plugin:
    hook("stop")
if mode == "no-result":
    sys.exit(0)
if mode == "api-error":
    emit({"type": "system", "subtype": "api_retry", "attempt": 1, "error_status": 429, "error": "rate_limit"})
    emit({"type": "result", "subtype": "success", "is_error": True, "result": "API Error: usage limit",
          "num_turns": 1, "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0,
          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}, "modelUsage": {}})
    sys.exit(1)
fresh = {"noindex": 3000, "index": 2000, "fugue": 2500}["fugue" if plugin else ("index" if indexed else "noindex")]
subtype = "error_max_turns" if mode == "max-turns" else "success"
emit({"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": 4,
      "total_cost_usd": None if mode == "no-cost" else fresh / 100000,
      "permission_denials": [{"tool_name": "WebFetch"}],
      "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1},
      "modelUsage": {"main": {"inputTokens": fresh, "outputTokens": 100, "cacheReadInputTokens": 5000,
                              "cacheCreationInputTokens": 200, "costUSD": fresh / 100000},
                     "helper": {"inputTokens": 50, "outputTokens": 5, "cacheReadInputTokens": 0,
                                "cacheCreationInputTokens": 0}}})
"""


FAKE_SELFTEST_CLAUDE = r"""#!{python}
# 会走 HTTP 的假 claude:向 ANTHROPIC_BASE_URL 要工具调用、执行、回传结果;FAKE_SANDBOX=broken 时不模拟沙箱
import json, os, subprocess, sys, urllib.request
argv = sys.argv
prompt = sys.stdin.read()
settings = json.loads(argv[argv.index("--settings") + 1])
plugin = argv[argv.index("--plugin-dir") + 1] if "--plugin-dir" in argv else None
workspace = os.getcwd()
broken = os.environ.get("FAKE_SANDBOX") == "broken"
sandbox = settings.get("sandbox") or {}
deny_read = (sandbox.get("filesystem") or {}).get("denyRead") or []
scrub = [v["name"] for v in (sandbox.get("credentials") or {}).get("envVars") or []]
def emit(event):
    print(json.dumps(event), flush=True)
def hook(name, extra=None):
    if not plugin:
        return {}
    payload = dict({"session_id": "selftest-session", "cwd": workspace, "hook_event_name": name}, **(extra or {}))
    run = subprocess.run([sys.executable, os.path.join(plugin, "scripts", "geb_hook.py"), name],
                         input=json.dumps(payload), text=True, capture_output=True, cwd=workspace)
    return json.loads(run.stdout) if run.stdout.strip() else {}
def ask(messages):
    body = json.dumps({"model": "fake", "stream": True, "messages": messages,
                       "tools": [{"name": "Bash"}, {"name": "Write"}]}).encode()
    request = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages?beta=true", data=body,
                                     headers={"content-type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    block, partial = None, ""
    for line in opener.open(request, timeout=30).read().decode().splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if data["type"] == "content_block_start":
                block = data["content_block"]
            elif data["type"] == "content_block_delta" and data["delta"]["type"] == "input_json_delta":
                partial += data["delta"]["partial_json"]
    if block["type"] == "tool_use":
        block["input"] = json.loads(partial)
    return block
def bash(command):
    if not broken and command.startswith("echo outside > ../"):
        return True, "Read-only file system"
    if not broken and any(p in command for p in deny_read):
        return True, "No such file or directory"
    env = {k: v for k, v in os.environ.items() if broken or k not in scrub}
    run = subprocess.run(command, shell=True, cwd=workspace, env=env, capture_output=True, text=True)
    return run.returncode != 0, run.stdout + run.stderr
emit({"type": "system", "subtype": "init", "model": "fake", "claude_code_version": "0.0-fake",
      "permissionMode": "acceptEdits", "apiKeySource": "ANTHROPIC_API_KEY",
      "plugins": [{"name": "fugue-docs", "path": plugin}] if plugin else [], "tools": ["Bash", "Write"]})
hook("session-start")
messages, turns = [{"role": "user", "content": prompt}], 0
while True:
    turns += 1
    block = ask(messages)
    messages.append({"role": "assistant", "content": [block]})
    if block["type"] == "tool_use":
        emit({"type": "assistant", "message": {"id": "m%d" % turns, "content": [block]}})
        if block["name"] == "Bash":
            error, text = bash(block["input"]["command"])
        else:
            hook("pre-tool", {"tool_name": "Write", "tool_input": block["input"]})
            open(block["input"]["file_path"], "w").write(block["input"]["content"])
            error, text = False, "File created"
        result = {"type": "tool_result", "tool_use_id": block["id"], "content": text, "is_error": error}
        emit({"type": "user", "message": {"content": [result]}})
        messages.append({"role": "user", "content": [result]})
        continue
    decision = hook("stop", {"stop_hook_active": turns > 99})
    if decision.get("decision") == "block" and not any(m.get("stop") for m in messages):
        messages.append({"role": "user", "content": decision["reason"], "stop": True})
        continue
    break
emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": turns, "total_cost_usd": 0,
      "modelUsage": {"fake": {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0,
                              "cacheCreationInputTokens": 0}}})
"""


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



def stream(*events):
    return [dict(e) for e in events]


class ClaudeTests(unittest.TestCase):
    def test_usage_comes_from_model_usage_and_counts_cache_writes_as_uncached(self):
        events = stream({"type": "result", "subtype": "success", "num_turns": 3, "total_cost_usd": 0.5,
                         "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 1,
                                   "cache_creation_input_tokens": 1},
                         "modelUsage": {"big": {"inputTokens": 100, "outputTokens": 20, "cacheReadInputTokens": 1000,
                                                "cacheCreationInputTokens": 50},
                                        "small": {"inputTokens": 10, "outputTokens": 2, "cacheReadInputTokens": 0,
                                                  "cacheCreationInputTokens": 0}}})
        usage = pilot.claude_usage(events)
        self.assertEqual("result.modelUsage", usage["source"])
        self.assertEqual(160, usage["uncached_input_tokens"])
        self.assertEqual(1000, usage["cached_input_tokens"])
        self.assertEqual(182, usage["uncached_plus_output"])
        self.assertEqual(1182, usage["total_tokens"])
        self.assertEqual(0.5, usage["cost_usd"])
        self.assertEqual(182, pilot.usage_value(usage, pilot.PRIMARY_METRIC))

    def test_usage_without_model_usage_is_marked_main_thread_only(self):
        usage = pilot.claude_usage([{"type": "result", "subtype": "success", "usage": {
            "input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 1}}])
        self.assertEqual("result.usage_main_thread_only", usage["source"])
        self.assertEqual(16, usage["uncached_plus_output"])
        self.assertIsNone(pilot.claude_usage([{"type": "result", "modelUsage": {"m": {"inputTokens": -1}}}]))
        self.assertIsNone(pilot.claude_usage([{"type": "assistant"}]))
        self.assertIsNone(pilot.claude_usage([{"type": "result", "usage": {}}, {"type": "result", "usage": {}}]))

    def test_partial_usage_deduplicates_repeated_message_blocks(self):
        message = {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 50,
                                         "cache_creation_input_tokens": 0}}
        later = {"id": "m1", "usage": dict(message["usage"], output_tokens=30)}
        other = {"id": "m2", "usage": {"input_tokens": 5, "output_tokens": 2, "cache_read_input_tokens": 0,
                                       "cache_creation_input_tokens": 3}}
        partial = pilot.claude_partial_usage([{"type": "assistant", "message": m} for m in (message, later, other)])
        self.assertEqual(2, partial["messages"])
        self.assertEqual(10 + 5 + 3 + 30 + 2, partial["uncached_plus_output"])
        self.assertEqual("assistant_messages_lower_bound", partial["source"])
        self.assertIsNone(pilot.claude_partial_usage([]))

    def test_status_requires_a_successful_result(self):
        ok = [{"type": "result", "subtype": "success", "is_error": False}]
        self.assertEqual("completed", pilot.claude_status(ok, "completed"))
        self.assertEqual("timeout", pilot.claude_status(ok, "timeout"))
        self.assertEqual("runner_failed", pilot.claude_status([], "completed"))
        self.assertEqual("agent_error_max_turns", pilot.claude_status(
            [{"type": "result", "subtype": "error_max_turns", "is_error": True}], "runner_failed"))

    def test_environment_is_private_and_keeps_only_the_token(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "CLAUDE_CONFIG_DIR": "/real",
                                     "CLAUDECODE": "1", "FUGUE_DATA_DIR": "/real/fugue", "CODEX_HOME": "/x"}):
            env = pilot.claude_environment(Path("/tmp/trial/home"))
        self.assertEqual("tok", env["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertEqual("/tmp/trial/home/.claude", env["CLAUDE_CONFIG_DIR"])
        self.assertEqual("/tmp/trial/home/.claude/fugue", env["FUGUE_DATA_DIR"])
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CODEX_HOME", env)

    def test_subscription_token_wins_and_other_variables_are_withheld(self):
        environ = {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "ANTHROPIC_API_KEY": "key", "ANTHROPIC_AUTH_TOKEN": "bearer",
                   "ANTHROPIC_BASE_URL": "https://proxy.example"}
        self.assertEqual({"CLAUDE_CODE_OAUTH_TOKEN": "tok"}, pilot.claude_credentials(environ))
        self.assertEqual({"ANTHROPIC_API_KEY": "key", "ANTHROPIC_BASE_URL": "https://proxy.example"},
                         pilot.claude_credentials({"ANTHROPIC_API_KEY": "key",
                                                   "ANTHROPIC_BASE_URL": "https://proxy.example"}))
        self.assertEqual({}, pilot.claude_credentials({}))
        with patch.dict(os.environ, dict(environ, PWD="/Users/me/fugue-docs", TASKS="rfc-base-url",
                                         PILOT_OUTPUT="/Users/me/out", LC_ALL="C.UTF-8", MY_VAR="x")):
            env = pilot.claude_environment(Path("/tmp/trial/home"), ["MY_VAR"])
        for name in ("PWD", "TASKS", "PILOT_OUTPUT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                     "ANTHROPIC_BASE_URL"):
            self.assertNotIn(name, env)
        self.assertEqual("C.UTF-8", env["LC_ALL"])
        self.assertEqual("x", env["MY_VAR"])
        self.assertEqual("tok", env["CLAUDE_CODE_OAUTH_TOKEN"])

    def test_api_failures_are_infrastructure_not_arm_failures(self):
        rejected = [{"type": "result", "subtype": "success", "is_error": True, "result": "API Error: 429"}]
        self.assertEqual("infrastructure_error", pilot.claude_status(rejected, "runner_failed"))
        self.assertEqual("infrastructure_error", pilot.claude_status(
            [{"type": "result", "subtype": "error_during_execution", "is_error": True}], "runner_failed"))
        too_long = [{"type": "result", "subtype": "success", "is_error": True,
                     "result": "API Error: 400 prompt is too long: 210000 tokens > 200000 maximum"}]
        self.assertEqual("agent_api_error", pilot.claude_status(too_long, "runner_failed"))
        quiet_limit = [{"type": "system", "subtype": "api_retry", "error": "overloaded"},
                       {"type": "result", "subtype": "success", "is_error": True, "result": "API Error"}]
        self.assertEqual("infrastructure_error", pilot.claude_status(quiet_limit, "runner_failed"))
        session = pilot.claude_session([{"type": "system", "subtype": "api_retry", "error": "rate_limit"}] + rejected)
        self.assertEqual(["rate_limit"], session["api_errors"])
        self.assertEqual("API Error: 429", session["result_error"])
        blocks = [trial("baseline", 100, repeat=1), dict(trial("fugue", 0, False, repeat=1),
                                                          status="infrastructure_error")]
        outcome = pilot.summarize_trials(blocks, TWO_ARM)["comparisons"]["workflow_effect"]["failure_aware"]
        self.assertEqual((0, 0, 0, 1), (outcome["treatment_better"], outcome["treatment_worse"], outcome["ties"],
                                        outcome["skipped_infrastructure"]))

    def test_sandbox_test_command_mirrors_the_task(self):
        task = {"regression": [["{python}", "-B", "alltests.py"]], "cwd": "test"}
        self.assertEqual("cd test && python3 -B alltests.py", pilot.sandbox_test_command(task))
        self.assertEqual("python3 -m unittest", pilot.sandbox_test_command(
            {"regression": [["{python}", "-m", "unittest"]], "cwd": None}))
        self.assertIsNone(pilot.sandbox_test_command({"regression": []}))

    def test_leftover_trial_directories_are_found(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(tempfile, "tempdir", directory):
                self.assertEqual([], pilot.stale_trial_dirs())
                (Path(directory) / "run-abc" / "workspace").mkdir(parents=True)
                (Path(directory) / "run-abc" / "home").mkdir()
                (Path(directory) / "run-other").mkdir()
                self.assertEqual([str(Path(directory) / "run-abc")], pilot.stale_trial_dirs())

    def test_deny_read_never_hides_the_temp_dir_or_home(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(source_cache=str(Path(directory) / "cache"), auth_home=str(Path.home()))
            task_set = {"path": str(pilot.DEFAULT_TASKS_FILE)}
            paths = pilot.deny_read_paths(args, task_set, Path(tempfile.gettempdir()), Path(directory) / "out")
        temp = str(Path(tempfile.gettempdir()).resolve())
        self.assertIn(str(pilot.ROOT.resolve()), paths)
        self.assertIn(str((Path(directory) / "out").resolve()), paths)
        self.assertNotIn(temp, paths)
        self.assertNotIn(str(Path.home().resolve()), paths)
        self.assertNotIn(str((Path(directory) / "cache").resolve()), paths)  # 不存在的目录不列

    def test_hint_matches_the_plugin_navigation_sentence(self):
        import geb_hook
        self.assertTrue(geb_hook.NAVIGATION_HINT.startswith(pilot.HINT_TEXT))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CLAUDE.md").write_text("# Project rules\nKeep it simple.")
            pilot.write_hint(root, "CLAUDE.md")
            self.assertEqual("# Project rules\nKeep it simple.\n\n" + pilot.HINT_TEXT + "\n",
                             (root / "CLAUDE.md").read_text())

    def test_prompt_is_identical_across_claude_arms(self):
        task = {"name": "t", "prompts": {"symptom": "Fix it."}, "test_rule": "Add tests as needed."}
        prompts = {arm: pilot.build_prompt(task, "symptom", arm, "claude") for arm in pilot.ARMS}
        self.assertEqual(1, len(set(prompts.values())))
        self.assertIn("Add tests as needed.", prompts["index"])
        self.assertNotIn("{test_rule}", prompts["index"])
        self.assertIn("without invoking the Fugue skill", pilot.build_prompt(task, "symptom", "index"))
        self.assertNotIn("without invoking", pilot.build_prompt(task, "symptom", "fugue"))

    def test_settings_keep_bash_sandboxed_and_web_tools_denied(self):
        settings = pilot.claude_settings("on", ["/answers"])
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertTrue(settings["sandbox"]["failIfUnavailable"])
        self.assertFalse(settings["sandbox"]["allowUnsandboxedCommands"])
        self.assertEqual(["/answers"], settings["sandbox"]["filesystem"]["denyRead"])
        self.assertEqual(set(pilot.CREDENTIAL_VARS),
                         {v["name"] for v in settings["sandbox"]["credentials"]["envVars"] if v["mode"] == "deny"})
        self.assertIn("WebFetch", settings["permissions"]["deny"])
        basic = pilot.claude_settings("basic", ["/answers"])["sandbox"]
        self.assertEqual({"enabled", "autoAllowBashIfSandboxed", "allowUnsandboxedCommands"}, set(basic))
        self.assertEqual(["Bash"], pilot.claude_settings("off")["permissions"]["allow"])
        args = SimpleNamespace(claude="claude", model="m", effort=None, claude_sandbox="on", max_turns=None,
                               max_budget_usd=None, claude_arg=["--foo"])
        command = pilot.claude_command(args, Path("/p"))
        self.assertEqual("/p", command[command.index("--plugin-dir") + 1])
        self.assertNotIn("--add-dir", command)
        self.assertNotIn("--plugin-dir", pilot.claude_command(args))
        self.assertNotIn("--effort", command)
        self.assertEqual("--foo", command[-1])

    def test_skill_archive_for_claude_carries_hooks_and_manifest(self):
        with tarfile.open(fileobj=io.BytesIO(pilot.skill_archive("HEAD", "claude"))) as tar:
            names = set(tar.getnames())
        self.assertIn("hooks/hooks.json", names)
        self.assertIn(".claude-plugin/plugin.json", names)
        with tarfile.open(fileobj=io.BytesIO(pilot.skill_archive("HEAD"))) as tar:
            self.assertNotIn("hooks/hooks.json", set(tar.getnames()))

    def test_setup_problem_detects_missing_plugin_or_hooks(self):
        loaded = {"init_seen": True, "plugins": ["fugue-docs"]}
        bare = {"init_seen": True, "plugins": []}
        self.assertIsNone(pilot.setup_problem("fugue", loaded, {"sessions": 1}))
        self.assertEqual("fugue_plugin_not_loaded", pilot.setup_problem("fugue", bare, {"sessions": 0}))
        self.assertEqual("fugue_hooks_not_run", pilot.setup_problem("fugue", loaded, {"sessions": 0}))
        self.assertEqual("unexpected_fugue_plugin", pilot.setup_problem("index", loaded, None))
        self.assertIsNone(pilot.setup_problem("index", {"init_seen": False}, None))
        warning = "Sandbox disabled: sandbox is enabled but dependencies are missing"
        self.assertEqual("sandbox_unavailable", pilot.setup_problem("index", bare, None, warning, "on"))
        self.assertIsNone(pilot.setup_problem("index", bare, None, warning, "off"))
        refused = "sandbox.failIfUnavailable is set — refusing to start without a working sandbox."
        self.assertEqual("sandbox_unavailable", pilot.setup_problem("index", bare, None, refused, "on"))

    def test_environment_signature_ignores_only_the_treatment(self):
        index = {"init_seen": True, "plugins": ["corp-tools"], "tools": ["Bash", "Read"], "mcp_servers": []}
        fugue = {"init_seen": True, "plugins": ["fugue-docs", "corp-tools"], "tools": ["Bash", "Read", "Skill"],
                 "mcp_servers": []}
        self.assertEqual(pilot.environment_signature(index), pilot.environment_signature(fugue))
        drifted = dict(index, mcp_servers=["new-server"])
        self.assertNotEqual(pilot.environment_signature(index), pilot.environment_signature(drifted))
        self.assertIsNone(pilot.environment_signature({"init_seen": False}))


class SelfTestTests(unittest.TestCase):
    def run_selftest(self, root, sandbox="on", broken=False, archive=None, task=None):
        fake = root / "fake-claude"
        fake.write_text(FAKE_SELFTEST_CLAUDE.replace("{python}", sys.executable))
        fake.chmod(0o700)
        skill = root / "skill"
        skill.mkdir()
        with tarfile.open(fileobj=io.BytesIO(pilot.skill_archive("HEAD", "claude"))) as tar:
            pilot.extract(tar, skill, pilot.checked_members(tar))
        args = SimpleNamespace(claude=str(fake), model="fake", effort=None, claude_sandbox=sandbox, max_turns=None,
                               max_budget_usd=None, claude_arg=[], claude_env=["FAKE_SANDBOX"],
                               deny_read=[str(root / "secret")])
        env = {"FAKE_SANDBOX": "broken" if broken else "ok", "CLAUDE_CODE_OAUTH_TOKEN": "real-token"}
        with patch.dict(os.environ, env):
            return pilot.claude_selftest(args, skill, root / "kept", archive, task)

    def test_mock_api_scripts_tool_calls_then_text(self):
        import claude_mock_api
        import urllib.request
        steps = [{"name": "Bash", "input": {"command": "ls"}}]
        with claude_mock_api.MockMessagesAPI(steps) as api:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(messages):
                body = json.dumps({"stream": True, "messages": messages, "tools": [{"name": "Bash"}]}).encode()
                request = urllib.request.Request(api.url + "/v1/messages", data=body)
                return opener.open(request, timeout=10).read().decode()
            first = post([{"role": "user", "content": "go"}])
            done = post([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x"}]}])
        self.assertIn('"name": "Bash"', first)
        self.assertIn("input_json_delta", first)
        self.assertIn("message_stop", first)
        self.assertIn("text_delta", done)
        self.assertEqual(2, len(api.requests))

    def test_selftest_passes_when_sandbox_plugin_and_hooks_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_selftest(root)
            self.assertTrue(result["ok"], result)
            self.assertGreaterEqual(result["mock_requests"], 6)  # 5 个工具步骤 + 结束 + 拦截后的再一轮
            self.assertTrue(result["checks"]["credentials_hidden_from_bash"])
            self.assertGreaterEqual(result["hooks"]["blocks"], 1)
            self.assertTrue((root / "kept" / "events.jsonl").is_file())
            probe = json.loads((root / "kept" / "events.jsonl").read_text().splitlines()[0])
            self.assertEqual("init", probe["subtype"])

    def test_selftest_runs_python_git_and_the_task_tests_on_a_real_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "tests").mkdir(parents=True)
            (repo / "PROJECT_INDEX.md").write_text("# demo index\n")
            (repo / "tests" / "test_ok.py").write_text("import unittest\nclass T(unittest.TestCase):\n"
                                                       "    def test_ok(self):\n        pass\n")
            git = pilot.git_environment(dict(os.environ))
            for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
                subprocess.run(command, cwd=repo, env=git, check=True, capture_output=True)
            archive = subprocess.check_output(["git", "archive", "HEAD"], cwd=repo)
            task = {"regression": [["{python}", "-B", "-m", "unittest", "discover", "-s", "tests"]], "cwd": None,
                    "strip_exclude": [], "workspace_ignore": []}
            result = self.run_selftest(root, archive=archive, task=task)
            self.assertTrue(result["ok"], result)
            for check in ("python3_runs", "git_runs", "task_tests_pass_in_sandbox"):
                self.assertTrue(result["checks"][check])
            self.assertEqual("python3 -B -m unittest discover -s tests", result["test_command"])
            task["regression"] = [["{python}", "-B", "-c", "raise SystemExit(1)"]]
            again = root / "again"
            again.mkdir()
            failed = self.run_selftest(again, archive=archive, task=task)
            self.assertEqual(["task_tests_pass_in_sandbox"], failed["failed"])

    def test_selftest_fails_when_sandbox_is_not_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_selftest(Path(directory), broken=True)
            self.assertFalse(result["ok"])
            self.assertIn("outside_write_blocked", result["failed"])
            self.assertIn("deny_read_effective", result["failed"])
            self.assertIn("credentials_hidden_from_bash", result["failed"])
            self.assertIn("--claude-sandbox basic", result["hint"])


class SourceTests(unittest.TestCase):
    def test_glob_matching_is_path_aware(self):
        paths = ["test/test_a.py", "test/sub/test_b.py", "test/data/x.rst", "src/test/y.py"]
        self.assertEqual(["test/sub/test_b.py", "test/test_a.py"], pilot.matching(paths, ["test/**/*.py"]))
        self.assertEqual(["test/test_a.py"], pilot.matching(paths, ["test/*.py"]))

    def test_archive_keeps_in_tree_links_and_refuses_escapes(self):
        def build(link_target, hard=False):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as tar:
                content = b"body"
                member = tarfile.TarInfo("pkg/sub/real.css")
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
                link = tarfile.TarInfo("pkg/data/link.css")
                link.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
                link.linkname = link_target
                tar.addfile(link)
            return data.getvalue()
        with tarfile.open(fileobj=io.BytesIO(build("../sub/real.css"))) as tar:
            members = pilot.checked_members(tar, "pkg")
            self.assertEqual(["sub/real.css", "data/link.css"], [m.name for m in members])
        for target, hard in (("../../../etc/passwd", False), ("/etc/passwd", False), ("pkg/sub/real.css", True)):
            with tarfile.open(fileobj=io.BytesIO(build(target, hard))) as tar:
                with self.assertRaises(ValueError):
                    pilot.checked_members(tar, "pkg" if not hard else None)

    def test_patch_targets_must_stay_inside(self):
        with tempfile.TemporaryDirectory() as directory:
            patch_file = Path(directory) / "p.patch"
            patch_file.write_text("diff --git a/x.py b/x.py\n")
            self.assertEqual(["x.py"], pilot.patch_targets(patch_file))
            patch_file.write_text("diff --git a/../x.py b/../x.py\n")
            with self.assertRaises(ValueError):
                pilot.patch_targets(patch_file)

    def test_protected_snapshot_skips_acceptance_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            for name in ("test_a.py", "test_b.py"):
                (root / "tests" / name).write_text("x")
            self.assertEqual(["tests/test_b.py"], sorted(pilot.protected_snapshot(
                root, ["tests/test_*.py"], {"tests/test_a.py"})))

    def test_builtin_docutils_tasks_load_with_external_source(self):
        task_set = pilot.load_tasks(ROOT_DOCUTILS_TASKS)
        self.assertEqual("docutils", task_set["source"]["name"])
        self.assertTrue(Path(task_set["source"]["overlay"]).is_file())
        self.assertEqual(4, len(task_set["tasks"]))
        for task in task_set["tasks"].values():
            self.assertTrue(task["acceptance_targets"])
            self.assertTrue(all(t.startswith("test/") for t in task["acceptance_targets"]))
            self.assertNotIn("docutils/", task["prompts"]["symptom"])
            self.assertNotIn(".py", task["prompts"]["symptom"])
            self.assertTrue(task["reference_patch"].is_file())


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


class ClaudeRunnerTests(unittest.TestCase):
    def make_source(self, root):
        repo = root / "source"
        (repo / "tests").mkdir(parents=True)
        (repo / "PROJECT_INDEX.md").write_text("# demo index\n")
        (repo / "app.py").write_text('"""\n[INPUT]: none\n[OUTPUT]: VALUE\n[POS]: demo\n[PROTOCOL]: update\n"""\n'
                                     'import os\nVALUE = 1\nNAME = "demo"\n')
        for name in ("test_app.py", "test_other.py"):
            (repo / "tests" / name).write_text("import unittest\nclass T(unittest.TestCase):\n"
                                               "    def test_ok(self):\n        self.assertTrue(True)\n")
        git = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
            subprocess.run(command, cwd=repo, env=git, check=True, capture_output=True)
        spec = root / "spec"
        spec.mkdir()
        (spec / "fix.patch").write_text('--- a/app.py\n+++ b/app.py\n@@ -7,3 +7,3 @@\n import os\n'
                                        '-VALUE = 1\n+VALUE = 2\n NAME = "demo"\n')
        (spec / "acceptance.patch").write_text(
            "diff --git a/tests/test_app.py b/tests/test_app.py\n--- a/tests/test_app.py\n+++ b/tests/test_app.py\n"
            "@@ -2,3 +2,6 @@ import unittest\n class T(unittest.TestCase):\n     def test_ok(self):\n"
            "         self.assertTrue(True)\n+    def test_value(self):\n+        import app\n"
            "+        self.assertEqual(2, app.VALUE)\n")
        (spec / "tasks.json").write_text(json.dumps({
            "schema": "geb.token-pilot.tasks.v1", "source_ref": "HEAD", "protected": [],
            "reset_before_validation": ["tests/**/*.py"], "test_rule": "Add or update tests as needed.",
            "regression": [["{python}", "-B", "-m", "unittest", "discover", "-s", "tests"]],
            "tasks": {"bump": {"prompts": {"symptom": "The value is too small.", "named": "Set app.VALUE to 2."},
                               "acceptance_patch": "acceptance.patch",
                               "acceptance": [["{python}", "-B", "-m", "unittest", "tests.test_app"]],
                               "reference_patch": "fix.patch"}}}))
        fake = root / "fake-claude"
        fake.write_text(FAKE_CLAUDE.replace("{python}", sys.executable))
        fake.chmod(0o700)
        return repo, spec / "tasks.json", fake

    def run_pilot(self, root, *extra, mode="ok", token="fake-token", expect=0):
        repo, tasks, fake = self.make_source(root)
        env = dict(os.environ, FAKE_PATCH=str(tasks.parent / "fix.patch"), FAKE_MODE=mode, CLAUDE_FAKE_LEAK="1")
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        command = [sys.executable, "-B", str(Path(pilot.__file__)), "--agent", "claude", "--source-repo", str(repo),
                   "--tasks-file", str(tasks), "--claude", str(fake), "--model", "fake",
                   "--output", str(root / "out"), "--execute", "--timeout", "60", "--skip-claude-selftest",
                   "--claude-env", "FAKE_PATCH", "--claude-env", "FAKE_MODE",
                   "--max-total-tokens", "10000000"] + list(extra)
        run = subprocess.run(command, capture_output=True, text=True, env=env)
        self.assertEqual(expect, run.returncode, run.stderr)
        if expect:
            return run
        return json.loads((root / "out" / "report.json").read_text())

    def probe(self, root, trial):
        for line in (root / "out" / trial["trial_dir"] / "events.jsonl").read_text().splitlines():
            event = json.loads(line)
            if event.get("type") == "probe":
                return event
        return None

    def test_three_arm_with_plugin_hooks_and_hidden_acceptance_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.run_pilot(root, "--repeats", "2")
            self.assertEqual("claude", report["agent"])
            self.assertEqual("schedule_complete", report["stop_reason"], report["trials"][-1])
            self.assertEqual(6, len(report["trials"]))
            self.assertTrue(all(t["accepted"] for t in report["trials"]), report["trials"][0]["validation"])
            trials = {t["arm"]: t for t in report["trials"]}
            setup = trials["index"]["validation"][0]
            self.assertEqual("acceptance_setup", setup["check"])
            self.assertEqual(["tests/test_other.py"], setup["discarded_edits"])
            patch_text = (root / "out" / trials["index"]["trial_dir"] / "changes.patch").read_text()
            self.assertIn("test_broken", patch_text)
            self.assertIn("tests/test_new.py", patch_text)
            self.assertEqual("complete_result_model_usage", trials["index"]["usage_coverage"])
            self.assertEqual(2000 + 50 + 200 + 105, trials["index"]["usage"]["uncached_plus_output"])
            fugue = trials["fugue"]
            self.assertEqual(["fugue-docs"], fugue["claude_session"]["plugins"])
            self.assertEqual(1, fugue["hooks"]["sessions"])
            self.assertEqual(1, fugue["hooks"]["stops"])
            self.assertEqual(0, fugue["hooks"]["errors"])
            self.assertIsNone(trials["index"]["hooks"])
            self.assertEqual(1, fugue["claude_session"]["permission_denials"])
            probes = {arm: self.probe(root, t) for arm, t in trials.items()}
            self.assertEqual(1, len({p["prompt"] for p in probes.values()}))
            for arm, probe in probes.items():
                self.assertEqual("fake-token", probe["token"])
                self.assertIsNone(probe["leak"])
                self.assertEqual(str(Path(probe["home"]) / ".claude"), probe["config"])
                self.assertEqual(arm == "fugue", "--plugin-dir" in probe["argv"])
                for word in ("noindex", "fugue", "bump"):
                    self.assertNotIn(word, probe["home"])
            navigation = trials["noindex"]["navigation"]
            self.assertTrue(navigation["first_edit_found"])
            self.assertEqual(2, navigation["before_first_edit"]["commands"])
            self.assertEqual(1, trials["index"]["navigation"]["before_first_edit"]["index_reads"])
            comparisons = report["summary"]["comparisons"]
            self.assertGreater(comparisons["index_effect"]["metrics"][pilot.PRIMARY_METRIC]["median_saving_rate"], 0)
            self.assertLess(comparisons["workflow_effect"]["metrics"]["cost_usd"]["median_saving_rate"], 0)
            self.assertIn("cache_creation_input_tokens", comparisons["workflow_effect"]["metrics"])
            self.assertGreater(report["summary"]["budget_cost_usd"], 0)

    def test_model_commits_do_not_hide_changes_or_move_the_reset_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.run_pilot(root, "--repeats", "1", "--design", "two-arm", mode="commit")
            self.assertEqual("schedule_complete", report["stop_reason"])
            for trial in report["trials"]:
                self.assertEqual(1, trial["model_commits"])
                self.assertTrue(trial["accepted"], trial["validation"])
                self.assertEqual(["tests/test_other.py"], trial["validation"][0]["discarded_edits"])
                patch_text = (root / "out" / trial["trial_dir"] / "changes.patch").read_text()
                self.assertIn("VALUE = 2", patch_text)
                self.assertIn("test_broken", patch_text)

    def test_api_failure_stops_without_scoring_an_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "2", "--design", "two-arm", mode="api-error")
            self.assertEqual("infrastructure_error", report["stop_reason"])
            self.assertEqual(1, len(report["trials"]))
            self.assertEqual(["rate_limit"], report["trials"][0]["claude_session"]["api_errors"])

    def test_missing_cost_stops_when_a_dollar_budget_is_set(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "2", "--max-total-cost-usd", "5", mode="no-cost")
            self.assertEqual("cost_unknown", report["stop_reason"])
            self.assertEqual(1, len(report["trials"]))

    def test_hint_arm_gets_only_a_rule_file_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.run_pilot(root, "--repeats", "1", "--design", "three-arm-hint")
            self.assertEqual("schedule_complete", report["stop_reason"])
            self.assertEqual({"file": "CLAUDE.md", "text": pilot.HINT_TEXT}, report["hint_arm"])
            trials = {t["arm"]: t for t in report["trials"]}
            self.assertEqual({"index", "hint", "fugue"}, set(trials))
            self.assertTrue(all(t["accepted"] for t in trials.values()))
            hint_md = root / "out" / trials["hint"]["trial_dir"] / "workspace" / "CLAUDE.md"
            self.assertEqual(pilot.HINT_TEXT + "\n", hint_md.read_text())
            for arm in ("index", "fugue"):
                self.assertFalse((root / "out" / trials[arm]["trial_dir"] / "workspace" / "CLAUDE.md").exists())
            self.assertNotIn("--plugin-dir", self.probe(root, trials["hint"])["argv"])
            self.assertNotEqual(trials["hint"]["base_commit"], trials["index"]["base_commit"])
            self.assertEqual({"hint_effect", "hooks_effect", "plugin_effect"},
                             set(report["summary"]["comparisons"]))

    def test_missing_plugin_stops_the_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "2", "--design", "two-arm", mode="no-plugin")
            self.assertEqual("setup_failed:fugue_plugin_not_loaded", report["stop_reason"])
            self.assertEqual("fugue", report["trials"][-1]["arm"])
            self.assertFalse(report["trials"][-1]["accepted"])

    def test_missing_result_keeps_only_a_lower_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "2", mode="no-result")
            self.assertEqual("incomplete_usage", report["stop_reason"])
            self.assertEqual(1, len(report["trials"]))
            self.assertEqual("assistant_messages_lower_bound", report["trials"][0]["partial_usage"]["source"])
            self.assertEqual("runner_failed", report["trials"][0]["status"])

    def test_turn_limit_counts_as_failure_with_known_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.run_pilot(Path(directory), "--repeats", "1", "--max-turns", "3", mode="max-turns")
            self.assertEqual("schedule_complete", report["stop_reason"])
            self.assertTrue(all(t["status"] == "agent_error_max_turns" for t in report["trials"]))
            self.assertFalse(any(t["accepted"] for t in report["trials"]))
            self.assertIsNotNone(report["summary"]["known_total_tokens"])
            probe = self.probe(Path(directory), report["trials"][0])
            self.assertEqual("3", probe["argv"][probe["argv"].index("--max-turns") + 1])

    def test_missing_claude_cli_is_reported_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, tasks, _ = self.make_source(root)
            run = subprocess.run([sys.executable, "-B", str(Path(pilot.__file__)), "--agent", "claude",
                                  "--source-repo", str(repo), "--tasks-file", str(tasks), "--model", "m",
                                  "--claude", str(root / "missing-claude"), "--claude-selftest"],
                                 capture_output=True, text=True)
            self.assertEqual(2, run.returncode)
            self.assertIn("install.sh", run.stderr)
            self.assertNotIn("Traceback", run.stderr)

    def test_execute_needs_a_token_because_the_config_dir_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.run_pilot(Path(directory), token=None, expect=2)
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", run.stderr)

    def test_verify_tasks_with_external_source_and_acceptance_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, tasks, _ = self.make_source(root)
            outer = root / "outer"
            shutil.copytree(str(repo), str(outer / "pkg"))
            shutil.rmtree(str(outer / "pkg" / ".git"))
            (outer / "README").write_text("outer project\n")
            (outer / "pkg" / "PROJECT_INDEX.md").unlink()
            git = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                       GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@example.invalid")
            for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "outer"]):
                subprocess.run(command, cwd=outer, env=git, check=True, capture_output=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=outer, text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD:pkg"], cwd=outer, text=True).strip()
            (tasks.parent / "overlay.patch").write_text(
                "diff --git a/PROJECT_INDEX.md b/PROJECT_INDEX.md\nnew file mode 100644\n--- /dev/null\n"
                "+++ b/PROJECT_INDEX.md\n@@ -0,0 +1 @@\n+# demo index\n")
            data = json.loads(tasks.read_text())
            data.pop("source_ref")
            data["source"] = {"name": "demo", "git": str(outer), "commit": commit, "subdir": "pkg",
                              "tree": tree, "overlay": "overlay.patch"}
            tasks.write_text(json.dumps(data))
            command = [sys.executable, "-B", str(Path(pilot.__file__)), "--tasks-file", str(tasks),
                       "--source-cache", str(root / "cache"), "--verify-tasks"]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, run.returncode, run.stdout[-3000:] + run.stderr)
            result = json.loads(run.stdout)
            self.assertTrue(result["ok"])
            self.assertEqual(["index", "noindex"], result["workspace_arms"])
            cached = list((root / "cache").iterdir())
            self.assertEqual(1, len(cached))
            self.assertFalse((cached[0] / "pkg").exists())
            self.assertTrue((cached[0] / "PROJECT_INDEX.md").is_file())
            # 第二次直接复用缓存
            again = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, again.returncode)
            data["source"]["tree"] = "0" * 40
            tasks.write_text(json.dumps(data))
            shutil.rmtree(str(root / "cache"))
            bad = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(2, bad.returncode)
            self.assertIn("does not match", bad.stderr)


if __name__ == "__main__":
    unittest.main()
