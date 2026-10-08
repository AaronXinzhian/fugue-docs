#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, hashlib, io, json, os, pathlib, random, re, shutil, signal, statistics, subprocess, sys, tarfile, tempfile, time, geb_telemetry, geb_check, geb_adapt, analyze_navigation
[OUTPUT]: 提供三组/两组/A-A 设计的配对块 token 试点、无模型任务校验、成本口径与定位指标汇总
[POS]: fugue-docs 评测包-受限 token 试点执行器;分离索引收益与流程开销,不宣称普遍节省
[PROTOCOL]: 改设计、任务格式或汇总口径时更新 token-pilot.md、test_token_pilot.py 并保留失败运行
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from geb_telemetry import session_identity, session_snapshot
import geb_check
from geb_adapt import BEGIN as MANAGED_BEGIN, END as MANAGED_END
import analyze_navigation as nav

DEFAULT_TASKS_FILE = ROOT / "evals" / "fixtures" / "token-pilot" / "tasks.json"
ARMS = ("noindex", "index", "fugue")
DESIGNS = {
    # 无索引 → 仅索引 = 索引收益;仅索引 → 完整赋格 = 流程开销/收益;两端 = 总效应
    "three-arm": {"arms": [("noindex", "noindex"), ("index", "index"), ("fugue", "fugue")],
                  "comparisons": [("noindex", "index", "index_effect"),
                                  ("index", "fugue", "workflow_effect"),
                                  ("noindex", "fugue", "total_effect")]},
    "two-arm": {"arms": [("index", "index"), ("fugue", "fugue")],
                "comparisons": [("index", "fugue", "workflow_effect")]},
}
PRIMARY_METRIC = "uncached_plus_output"
COST_KEYS = ("uncached_input_tokens", "cached_input_tokens", "output_tokens")
HEADER_LINE = re.compile(r"^\s*(?:#+|//+|/\*+|\*|--|;+)?\s*\[(?:INPUT|OUTPUT|POS|PROTOCOL)\]\s*:")
EMPTY_BLOCK = (('"""', '"""'), ("\'\'\'", "\'\'\'"), ("/**", "*/"), ("/*", "*/"))
INDEX_FILE_NAMES = {"PROJECT_INDEX.md", "FOLDER_INDEX.md"}
BASE_PROMPT = ("\nKeep the change scoped, preserve existing behavior, and run relevant tests. "
               "Both source code and existing project documentation are available to you. "
               "Do not use the network, publish, commit, or read outside this workspace and your isolated home. "
               "Do not change pre-existing test files; add tests in a new file. "
               "Stop after the change and give a concise result.\n")
PLAIN_PROMPT = ("Use your normal coding workflow without invoking the Fugue skill or its automation. "
                "Existing documentation remains available.\n")


def design(name, aa_arm="index"):
    if name == "aa":
        if aa_arm not in ARMS:
            raise ValueError("unknown A/A arm: " + aa_arm)
        a, b = aa_arm + "-a", aa_arm + "-b"
        return {"arms": [(a, aa_arm), (b, aa_arm)], "comparisons": [(a, b, "noise")]}
    return DESIGNS[name]


def load_tasks(path):
    """读取任务文件;验收文件与参考补丁按任务文件目录解析,运行时才复制进工作区。"""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "geb.token-pilot.tasks.v1" or not isinstance(data.get("tasks"), dict):
        raise ValueError("tasks file must use schema geb.token-pilot.tasks.v1 with a tasks object")
    tasks = {}
    for name, raw in data["tasks"].items():
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", name):
            raise ValueError("task names must be simple identifiers: " + name)
        prompts = raw.get("prompts") or ({"named": raw["prompt"]} if raw.get("prompt") else {})
        if not prompts or not raw.get("acceptance"):
            raise ValueError("task %s needs prompts and acceptance commands" % name)
        files = {}
        for dest, source in (raw.get("acceptance_files") or {}).items():
            if Path(dest).is_absolute() or ".." in Path(dest).parts:
                raise ValueError("acceptance destination must stay inside the workspace: " + dest)
            files[dest] = (path.parent / source).resolve()
        patch = raw.get("reference_patch")
        tasks[name] = {
            "name": name, "prompts": prompts, "acceptance": raw["acceptance"], "acceptance_files": files,
            "regression": raw.get("regression", data.get("regression", [])),
            "protected": raw.get("protected", data.get("protected", [])),
            "strip_exclude": raw.get("strip_exclude", data.get("strip_exclude", [])),
            "reference_patch": (path.parent / patch).resolve() if patch else None}
    digest, secrets = hashlib.sha256(path.read_bytes()), set()
    for name in sorted(tasks):
        hidden = sorted(tasks[name]["acceptance_files"].values())
        if tasks[name]["reference_patch"]:
            hidden.append(tasks[name]["reference_patch"])
        for source in hidden:
            content = source.read_bytes()
            digest.update(content)
            secrets.add(hashlib.sha256(content).hexdigest())
    return {"path": str(path), "source_ref": data.get("source_ref"), "tasks": tasks,
            "sha256": digest.hexdigest(), "secret_hashes": secrets}


def archive_hashes(archive):
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        return {member.name: hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                for member in tar.getmembers() if member.isfile()}


def leaked(archive, secrets):
    """源码或技能归档里若含隐藏验收文件或参考补丁,模型就能直接看到答案。"""
    return sorted(name for name, digest in archive_hashes(archive).items() if digest in secrets)


SKILL_PATHS = ("SKILL.md", "scripts", "references", "adapters", "agents")


def skill_archive(revision):
    present = set(subprocess.check_output(["git", "ls-tree", "--name-only", revision], cwd=ROOT, text=True).split())
    paths = [p for p in SKILL_PATHS if p in present]
    return subprocess.check_output(["git", "archive", revision, "--"] + paths, cwd=ROOT)


def build_schedule(task_names, repeats, arms, seed):
    """配对块:同一任务同一重复的各组相邻执行,块内顺序随机,块之间也随机。"""
    rng = random.Random(seed)
    blocks = [(task, repeat) for task in task_names for repeat in range(1, repeats + 1)]
    rng.shuffle(blocks)
    schedule = []
    for task, repeat in blocks:
        order = [label for label, _ in arms]
        rng.shuffle(order)
        schedule.append({"task": task, "repeat": repeat, "order": order})
    return schedule


def is_excluded(rel, prefixes):
    return any(rel == p.rstrip("/") or rel.startswith(p.rstrip("/") + "/") for p in prefixes)


def strip_indexes(workspace, exclude_prefixes=()):
    """无索引组:删 L1/L2 索引文件、代码文件头部的 L3 标签行和 GEB 托管规则块。"""
    stats = {"index_files_removed": 0, "header_files_stripped": 0, "header_lines_removed": 0,
             "empty_header_blocks_removed": 0, "managed_blocks_removed": 0}
    workspace = Path(workspace)
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink() or ".git" in path.relative_to(workspace).parts:
            continue
        rel = path.relative_to(workspace).as_posix()
        if is_excluded(rel, exclude_prefixes):
            continue
        if path.name in INDEX_FILE_NAMES:
            path.unlink()
            stats["index_files_removed"] += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not geb_check.is_code_file(rel):
            # 托管规则块只出现在 AGENTS.md 等规则文件里;标记必须独占一行,避免误删源码常量
            block = re.compile(r"^" + re.escape(MANAGED_BEGIN) + r"$.*?^" + re.escape(MANAGED_END) + r"$\n?",
                               re.S | re.M)
            text, removed = block.subn("", text)
            if removed:
                path.write_text(text, encoding="utf-8")
                stats["managed_blocks_removed"] += removed
            continue
        lines = text.splitlines(True)
        head = lines[:geb_check.L3_SCAN_LINES]
        kept = [line for line in head if not HEADER_LINE.match(line)]
        if len(kept) != len(head):
            stats["header_files_stripped"] += 1
            stats["header_lines_removed"] += len(head) - len(kept)
            kept, emptied = drop_empty_blocks(kept)
            stats["empty_header_blocks_removed"] += emptied
            path.write_text("".join(kept + lines[geb_check.L3_SCAN_LINES:]), encoding="utf-8")
    return stats


def drop_empty_blocks(lines):
    """头部标签删光后留下的空文档字符串/空注释块也删掉,不给"这里删过东西"的暗示。"""
    result, removed, i = [], 0, 0
    while i < len(lines):
        if i + 1 < len(lines) and (lines[i].strip(), lines[i + 1].strip()) in EMPTY_BLOCK:
            removed += 1
            i += 2
            continue
        result.append(lines[i])
        i += 1
    return result, removed


def count_index_files(archive):
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        return sum(Path(m.name).name in INDEX_FILE_NAMES for m in tar.getmembers() if m.isfile())


def git_environment(env):
    return dict(env, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                GIT_AUTHOR_NAME="Fugue Pilot", GIT_COMMITTER_NAME="Fugue Pilot",
                GIT_AUTHOR_EMAIL="pilot@example.invalid", GIT_COMMITTER_EMAIL="pilot@example.invalid",
                GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")


def prepare_workspace(archive, workspace, arm, task, git_env):
    workspace.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # The source is a git archive; refuse traversal and links regardless.
        for member in tar.getmembers():
            if member.issym() or member.islnk() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                raise ValueError("unsafe archive member")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(workspace, filter="data")
        else:
            tar.extractall(workspace)
    strip = strip_indexes(workspace, task["strip_exclude"]) if arm == "noindex" else None
    for command in (["git", "-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q"],
                    ["git", "add", "-A"], ["git", "-c", "core.hooksPath=" + os.devnull,
                                           "-c", "commit.gpgsign=false", "commit", "-qm", "Fixed pilot snapshot"]):
        subprocess.run(command, cwd=workspace, env=git_env, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace, env=git_env, text=True).strip()
    return base, strip


def protected_snapshot(workspace, patterns):
    files = {}
    for pattern in patterns:
        for path in workspace.glob(pattern):
            if path.is_file():
                files[path.relative_to(workspace).as_posix()] = path.read_bytes()
    return files


def expand(command):
    return [sys.executable if part == "{python}" else str(part) for part in command]


def validate(workspace, task, evidence_dir, timeout=120):
    """在模型结束后复制隐藏验收文件,依次运行验收与回归命令;两类分开记录。"""
    for dest, source in task["acceptance_files"].items():
        target = workspace / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results = []
    commands = [("acceptance", c) for c in task["acceptance"]] + [("regression", c) for c in task["regression"]]
    for index, (kind, command) in enumerate(commands):
        try:
            run = subprocess.run(expand(command), cwd=workspace, capture_output=True, text=True,
                                 timeout=timeout, env=env)
            log, passed = run.stdout + run.stderr, run.returncode == 0
        except subprocess.TimeoutExpired:
            log, passed = "validation timed out", False
        except OSError as error:
            log, passed = "validation could not start: %s" % error, False
        (evidence_dir / ("validation-%d.txt" % index)).write_text(log, encoding="utf-8")
        results.append({"check": "%s:%d" % (kind, index), "kind": kind, "passed": passed,
                        "sha256": hashlib.sha256(log.encode()).hexdigest()})
    return results


def cli_usage(events):
    usages = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
    if not usages:
        return None
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(not isinstance(u, dict) or any(not isinstance(u.get(k), int) or isinstance(u[k], bool)
                                        or u[k] < 0 for k in keys) for u in usages):
        return None
    result = {k: sum(u[k] for u in usages) for k in keys}
    if result["cached_input_tokens"] > result["input_tokens"]:
        return None
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    result["uncached_input_tokens"] = result["input_tokens"] - result["cached_input_tokens"]
    result["uncached_plus_output"] = result["uncached_input_tokens"] + result["output_tokens"]
    result["turns"] = len(usages)
    return result


def partial_usage(home, events):
    ids = {e.get("thread_id") for e in events if e.get("type") == "thread.started" and e.get("thread_id")}
    if len(ids) != 1:
        return None
    session_id = ids.pop()
    candidates = [p for p in (home / "sessions").glob("**/*.jsonl")
                  if (session_identity(p) or {}).get("session_id") == session_id]
    if len(candidates) != 1:
        return None
    try:
        snapshot = session_snapshot(candidates[0])
    except (OSError, ValueError):
        return None
    return snapshot["usage"] if snapshot else None


def usage_value(usage, key, weights=None):
    """从用量取指标;旧记录缺派生字段时按定义现算,缺原始字段则为未知。"""
    if not isinstance(usage, dict):
        return None
    if key == "weighted_cost":
        if not weights:
            return None
        values = [usage_value(usage, k) for k in weights]
        return None if None in values else sum(weights[k] * v for k, v in zip(weights, values))
    if key in usage and isinstance(usage[key], (int, float)):
        return usage[key]
    if key == "uncached_input_tokens" and isinstance(usage.get("input_tokens"), int) \
            and isinstance(usage.get("cached_input_tokens"), int):
        return usage["input_tokens"] - usage["cached_input_tokens"]
    if key == "uncached_plus_output":
        uncached, output = usage_value(usage, "uncached_input_tokens"), usage.get("output_tokens")
        return uncached + output if uncached is not None and isinstance(output, int) else None
    return None


def token_metrics(weights=None):
    keys = ["uncached_plus_output", "total_tokens", "uncached_input_tokens", "cached_input_tokens", "output_tokens"]
    if weights:
        keys.append("weighted_cost")
    return {key: (lambda trial, key=key: usage_value(trial.get("usage"), key, weights)) for key in keys}


def sum_known(values):
    return None if not values or None in values else sum(values)


def budget_tokens(trial):
    """预算口径:完整用量,否则用已上报的部分用量作下限;两者都没有记 0 并由调用方停止。"""
    for usage in (trial.get("usage"), trial.get("partial_usage")):
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            return usage["total_tokens"]
    return 0


def failure_aware(blocks, reference, treatment):
    """把失败也算进来的块级胜负:只通过一侧时通过的一侧更好,都失败算持平;用量未知的块跳过。"""
    better = worse = ties = skipped = 0
    for block in blocks.values():
        a, b = block.get(reference), block.get(treatment)
        if not a or not b:
            continue
        outcomes = []
        for trial in (a, b):
            if not trial.get("accepted"):
                outcomes.append("fail")
            else:
                outcomes.append(usage_value(trial.get("usage"), PRIMARY_METRIC))
        if None in outcomes:
            skipped += 1
        elif outcomes == ["fail", "fail"]:
            ties += 1
        elif outcomes[0] == "fail":
            better += 1
        elif outcomes[1] == "fail":
            worse += 1
        elif outcomes[1] < outcomes[0]:
            better += 1
        elif outcomes[1] > outcomes[0]:
            worse += 1
        else:
            ties += 1
    return {"metric": PRIMARY_METRIC, "treatment_better": better, "treatment_worse": worse, "ties": ties,
            "skipped_unknown_usage": skipped, "sign_test_p": nav.sign_test(better, worse)}


def summarize_trials(trials, comparisons, weights=None):
    """失败与未知用量计入成本;只有双方都通过验收且用量完整的同块配对进入差值。"""
    known = [t for t in trials if t.get("usage")]
    labels = []
    for trial in trials:
        if trial["condition"] not in labels:
            labels.append(trial["condition"])
    blocks = {}
    for trial in trials:
        blocks.setdefault((trial["task"], trial["repeat"]), {})[trial["condition"]] = trial
    metrics = token_metrics(weights)
    metrics.update({name: (lambda trial, fn=fn: fn(trial.get("navigation"))) for name, fn in nav.NAV_METRICS.items()})
    result = {}
    for reference, treatment, name in comparisons:
        pairs = []
        for (task, repeat), block in sorted(blocks.items()):
            a, b = block.get(reference), block.get(treatment)
            if not a or not b or not all(t.get("accepted") and t.get("usage") for t in (a, b)):
                continue
            pairs.append({"task": task, "repeat": repeat,
                          "values": {m: [fn(a), fn(b)] for m, fn in metrics.items()},
                          "elapsed_difference_seconds": round(a["elapsed_seconds"] - b["elapsed_seconds"], 3)})
        stats = {m: nav.paired_stats([tuple(p["values"][m]) for p in pairs]) for m in metrics}
        for stat in stats.values():
            # 例如没检测到首次修改的试验:配对成立但该指标缺失,单独计数而不是静默丢掉
            stat["missing_in_qualified_pairs"] = len(pairs) - stat["n"]
        entry = {"reference": reference, "treatment": treatment, "qualified_pairs": len(pairs),
                 "difference_sign": "reference minus treatment; positive means treatment used less",
                 "metrics": stats, "failure_aware": failure_aware(blocks, reference, treatment), "pairs": pairs}
        if name == "noise":
            entry["power_hint"] = {m: nav.power_hint(entry["metrics"][m].get("sd_log_ratio"))
                                   for m in (PRIMARY_METRIC, "total_tokens")}
        result[name] = entry
    by_condition = {}
    for label in labels:
        group = [t for t in trials if t["condition"] == label]
        measured = [t for t in group if t.get("usage")]
        by_condition[label] = {
            "attempted": len(group), "accepted": sum(bool(t.get("accepted")) for t in group),
            "acceptance_rate": sum(bool(t.get("accepted")) for t in group) / len(group),
            "measured_tokens": sum(t["usage"]["total_tokens"] for t in measured) if measured else None,
            "measured_uncached_plus_output": sum_known([usage_value(t["usage"], PRIMARY_METRIC) for t in measured])}
    positions = {}
    for trial in trials:
        value = usage_value(trial.get("usage"), PRIMARY_METRIC) if trial.get("accepted") else None
        if trial.get("block_position") is not None and value is not None:
            positions.setdefault(str(trial["block_position"]), []).append(value)
    return {"attempted": len(trials), "accepted": sum(bool(t.get("accepted")) for t in trials),
            "unknown_usage_trials": len(trials) - len(known),
            "partial_usage_trials": sum(1 for t in trials if not t.get("usage") and t.get("partial_usage")),
            "budget_tokens_lower_bound": sum(budget_tokens(t) for t in trials),
            "trials_with_outside_workspace_reads": [
                t.get("trial_id") for t in trials
                if nav.nav_value(t.get("navigation"), "total", "outside_workspace_reads")],
            "primary_metric_median_by_block_position": {
                k: statistics.median(v) for k, v in sorted(positions.items())},
            "experiment_total_tokens": (sum(t["usage"]["total_tokens"] for t in known)
                                        if known and len(known) == len(trials) else None),
            "known_total_tokens": sum(t["usage"]["total_tokens"] for t in known) if known else None,
            "primary_metric": PRIMARY_METRIC, "by_condition": by_condition, "comparisons": result}


def run_codex(args, workspace, directory, env, prompt):
    command = [args.codex, "exec", "--json", "--ignore-user-config",
               "--skip-git-repo-check", "--sandbox", "workspace-write", "--color", "never",
               "-m", args.model, "-c", 'model_reasoning_effort="' + args.effort + '"',
               "-c", 'approval_policy="never"', "-C", str(workspace), "-"]
    with (directory / "events.jsonl").open("w") as out, (directory / "stderr.log").open("w") as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err, env=env,
                                   cwd=directory, text=True, start_new_session=True)
        try:
            process.communicate(prompt, timeout=args.timeout)
            return "completed" if process.returncode == 0 else "runner_failed"
        except subprocess.TimeoutExpired:
            kill_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_group(process.pid, signal.SIGKILL)
                process.wait()
            return "timeout"


def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def build_prompt(task, style, arm):
    prompt = task["prompts"].get(style)
    if prompt is None:
        raise ValueError("task %s has no %s prompt" % (task["name"], style))
    return prompt + BASE_PROMPT + ("" if arm == "fugue" else PLAIN_PROMPT)


def trial(args, archive, skill_dir, task, repeat, label, arm, parent, trial_dir=None, position=None):
    """在中性临时目录里跑一次:模型看到的路径不含任务名或组名,结束后整体移入输出目录。"""
    trial_id = "%s-%d-%s" % (task["name"], repeat, label)
    trial_dir = trial_dir or trial_id
    directory = Path(tempfile.mkdtemp(prefix="run-"))
    try:
        result = run_trial(args, archive, skill_dir, task, directory, arm)
    finally:
        shutil.move(str(directory), str(parent / trial_dir))
    result.update(trial_id=trial_id, trial_dir=trial_dir, task=task["name"], repeat=repeat,
                  condition=label, arm=arm, block_position=position)
    return result


def run_trial(args, archive, skill_dir, task, directory, arm):
    workspace = directory / "workspace"
    home = directory / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    env.update(HOME=str(home), CODEX_HOME=str(codex_home), PYTHONDONTWRITEBYTECODE="1")
    git_env = git_environment(env)
    base_commit, strip = prepare_workspace(archive, workspace, arm, task, git_env)
    auth = Path(args.auth_home).expanduser() / "auth.json"
    if auth.is_file():
        (codex_home / "auth.json").symlink_to(auth.resolve())
    if arm == "fugue":
        target = home / ".agents" / "skills" / "fugue-docs"
        target.parent.mkdir(parents=True)
        shutil.copytree(skill_dir, target)
        (codex_home / "AGENTS.md").write_text(
            "For this development task, use the fugue-docs skill at " + str(target / "SKILL.md") + ".\n", encoding="utf-8")
    protected = protected_snapshot(workspace, task["protected"])
    started = time.monotonic()
    try:
        status = run_codex(args, workspace, directory, env, build_prompt(task, args.prompt_style, arm))
    finally:
        auth_link = codex_home / "auth.json"
        if auth_link.is_symlink() or auth_link.is_file():
            auth_link.unlink()
    elapsed = round(time.monotonic() - started, 3)
    events = nav.load_events(directory / "events.jsonl")
    usage = cli_usage(events)
    partial = partial_usage(codex_home, events) if usage is None else None
    # 先保存模型改动(含新建文件),再复制隐藏验收文件,补丁里不会混入验收脚本
    subprocess.run(["git", "add", "-A", "-N"], cwd=workspace, env=git_env, check=True, capture_output=True)
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=workspace, env=git_env)
    (directory / "changes.patch").write_bytes(diff)
    checks = validate(workspace, task, directory, args.validation_timeout) if status == "completed" else []
    if checks:
        checks.append({"check": "protected_files_unchanged", "kind": "protection", "passed": all(
            (workspace / name).is_file() and (workspace / name).read_bytes() == content
            for name, content in protected.items())})
    return {"status": status, "base_commit": base_commit, "strip": strip,
            "changes_sha256": hashlib.sha256(diff).hexdigest(),
            "usage": usage, "elapsed_seconds": elapsed, "validation": checks,
            "partial_usage": partial,
            "usage_coverage": "complete_cli_turn" if usage else (
                "partial_lower_bound" if partial else "unknown"),
            "accepted": status == "completed" and bool(checks) and all(c["passed"] for c in checks),
            "navigation": nav.analyze_events(events),
            "events_sha256": hashlib.sha256((directory / "events.jsonl").read_bytes()).hexdigest()}


def verify_tasks(archive, task_set, names, arms, parent, timeout):
    """不调用模型:每组工作区上验收应先失败、回归应先通过,打上参考补丁后全部通过。"""
    results, ok = [], True
    env = dict(os.environ, HOME=str(parent / "home"))
    git_env = git_environment(env)
    workspace_arms = sorted({"index" if arm == "fugue" else arm for _, arm in arms})
    for name in names:
        task = task_set["tasks"][name]
        for arm in workspace_arms:
            directory = parent / ("%s-%s" % (name, arm))
            directory.mkdir()
            row = {"task": name, "arm": arm}
            for phase in ("before", "after"):
                workspace = directory / phase
                _, strip = prepare_workspace(archive, workspace, arm, task, git_env)
                row["strip"] = strip
                evidence = directory / ("evidence-" + phase)
                evidence.mkdir()
                if phase == "after":
                    if not task["reference_patch"]:
                        row["after"] = None
                        row["patch"] = "missing"
                        continue
                    applied = subprocess.run(["git", "apply", "--whitespace=nowarn", str(task["reference_patch"])],
                                             cwd=workspace, env=git_env, capture_output=True, text=True)
                    row["patch"] = "applied" if applied.returncode == 0 else "failed: " + applied.stderr.strip()[:200]
                    if applied.returncode:
                        row["after"] = None
                        continue
                row[phase] = validate(workspace, task, evidence, timeout)
            before = row["before"]
            expectations = {
                "task_not_already_solved": not all(c["passed"] for c in before if c["kind"] == "acceptance"),
                "regression_passes_before": all(c["passed"] for c in before if c["kind"] == "regression"),
                "reference_patch_applies": row["patch"] == "applied",
                "reference_solution_accepted": bool(row.get("after")) and all(c["passed"] for c in row["after"])}
            row["expectations"] = expectations
            row["ok"] = all(expectations.values())
            ok = ok and row["ok"]
            results.append(row)
    return {"schema": "geb.token-pilot-verify.v1", "ok": ok, "workspace_arms": workspace_arms,
            "note": "fugue arm shares the index workspace; only the installed skill differs",
            "results": results}


def parse_weights(text):
    if not text:
        return None
    weights = {}
    for part in text.split(","):
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in COST_KEYS:
            raise ValueError("cost weight keys: " + ", ".join(COST_KEYS))
        weights[key] = float(value)
        if weights[key] < 0:
            raise ValueError("cost weights must be non-negative")
    return weights


def main():
    parser = argparse.ArgumentParser(description="Bounded paired-block token pilot, not a universal benchmark")
    parser.add_argument("--ref", help="Fixed source revision (default: the tasks file source_ref)")
    parser.add_argument("--source-repo", default=str(ROOT), help="Git repository the tasks run against")
    parser.add_argument("--tasks-file", default=str(DEFAULT_TASKS_FILE))
    parser.add_argument("--skill-ref", help="fugue-docs revision installed in the fugue arm "
                        "(default: the source revision when testing this repository, else HEAD)")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--design", choices=("three-arm", "two-arm", "aa"), default="three-arm")
    parser.add_argument("--aa-arm", choices=ARMS, default="index", help="Arm repeated in an A/A noise run")
    parser.add_argument("--prompt-style", choices=("symptom", "named"), default="symptom",
                        help="symptom prompts describe behavior without naming files or functions")
    parser.add_argument("--tasks", nargs="+")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--validation-timeout", type=int, default=120)
    parser.add_argument("--max-total-tokens", type=int, default=300000)
    parser.add_argument("--cost-weights", help="Relative weights, e.g. uncached_input_tokens=1,cached_input_tokens=0.1,output_tokens=4")
    parser.add_argument("--seed", type=int, default=730)
    parser.add_argument("--auth-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--output", help="Local private output directory; raw logs are not public results")
    parser.add_argument("--verify-tasks", action="store_true",
                        help="Without model calls, check every arm's acceptance against the reference patches")
    parser.add_argument("--execute", action="store_true", help="Without this, print the plan without model calls")
    args = parser.parse_args()
    try:
        task_set = load_tasks(args.tasks_file)
        weights = parse_weights(args.cost_weights)
        chosen = design(args.design, args.aa_arm)
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))
    names = args.tasks or sorted(task_set["tasks"])
    unknown = [n for n in names if n not in task_set["tasks"]]
    if unknown:
        parser.error("unknown tasks: " + ", ".join(unknown))
    if not 1 <= args.repeats <= 20 or args.timeout <= 0 or args.max_total_tokens <= 0:
        parser.error("require 1..20 repeats and positive budgets")
    ref = args.ref or task_set["source_ref"]
    if not ref:
        parser.error("--ref is required when the tasks file has no source_ref")
    source = Path(args.source_repo).resolve()
    try:
        revision = subprocess.check_output(["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=source,
                                           text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        parser.error("cannot resolve %s in %s; a shallow clone needs the full history (git fetch --unshallow)"
                     % (ref, source))
    archive = subprocess.check_output(["git", "archive", revision], cwd=source)
    index_files = count_index_files(archive)
    if index_files == 0:
        parser.error("source revision has no PROJECT_INDEX.md/FOLDER_INDEX.md; this pilot compares warm indexes")
    self_hosted = source == ROOT.resolve()
    skill_rev = subprocess.check_output(["git", "rev-parse", args.skill_ref or (revision if self_hosted else "HEAD")],
                                        cwd=ROOT, text=True).strip()
    if self_hosted and skill_rev != revision and subprocess.run(
            ["git", "merge-base", "--is-ancestor", skill_rev, revision], cwd=ROOT).returncode != 0:
        # 自托管时更新的 skill 可能已经包含任务答案(脚本或文档)
        parser.error("in self-hosted runs the skill revision must be the source revision or an ancestor of it")
    skill = skill_archive(skill_rev)
    exposed = leaked(archive, task_set["secret_hashes"]) + leaked(skill, task_set["secret_hashes"])
    if exposed:
        parser.error("hidden acceptance files or reference patches are visible to the model: " + ", ".join(exposed))
    if args.verify_tasks:
        with tempfile.TemporaryDirectory(prefix="fugue-verify-") as directory:
            parent = Path(directory)
            (parent / "home").mkdir()
            result = verify_tasks(archive, task_set, names, chosen["arms"], parent, args.validation_timeout)
        result.update(source_commit=revision, tasks_sha256=task_set["sha256"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if not args.model or not args.output:
        parser.error("--model and --output are required unless --verify-tasks is used")
    schedule = build_schedule(names, args.repeats, chosen["arms"], args.seed)
    arm_of = dict(chosen["arms"])
    report = {"schema": "geb.token-pilot.v2", "source_commit": revision, "source_repo": str(source),
              "source_index_files": index_files, "model": args.model, "effort": args.effort,
              "design": args.design, "arms": [list(a) for a in chosen["arms"]],
              "comparisons": [list(c) for c in chosen["comparisons"]],
              "prompt_style": args.prompt_style, "tasks_file": task_set["path"], "tasks_sha256": task_set["sha256"],
              "skill_commit": skill_rev, "self_hosted": self_hosted,
              "self_hosted_note": ("source is fugue-docs itself: SKILL.md and docs describing indexes stay in every "
                                   "workspace, and its own index self-check fails in the noindex arm; treat "
                                   "index_effect here as confounded") if self_hosted else None,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "session_persistence": "isolated_codex_home", "seed": args.seed,
              "planned_blocks": len(schedule), "planned_trials": sum(len(b["order"]) for b in schedule),
              "schedule": schedule, "primary_metric": PRIMARY_METRIC, "cost_weights": weights,
              "scope": "warm existing indexes; tasks from the tasks file only",
              "initialization_cost": None, "initialization_note": "not measured; indexed arms start with existing indexes",
              "token_budget": args.max_total_tokens, "timeout_per_trial": args.timeout,
              "budget_note": ("budget checked after each complete block against complete or partial usage, may "
                              "overshoot by one block; a trial with neither complete nor partial usage stops at once"),
              "quality_note": "same hidden acceptance and regression commands; not independent semantic review",
              "evidence_level": "test_passed_pairs_only_pending_independent_semantic_review",
              "trials": []}
    if not args.execute:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    skill_dir = output / "skill"
    skill_dir.mkdir()
    with tarfile.open(fileobj=io.BytesIO(skill)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(skill_dir, filter="data")
        else:
            tar.extractall(skill_dir)
    report["source_archive_sha256"] = hashlib.sha256(archive).hexdigest()
    report["skill_sha256"] = hashlib.sha256(b"".join(p.relative_to(skill_dir).as_posix().encode() + b"\0" +
        p.read_bytes() for p in sorted(skill_dir.rglob("*")) if p.is_file())).hexdigest()
    stop = None
    for block in schedule:
        for position, label in enumerate(block["order"]):
            result = trial(args, archive, skill_dir, task_set["tasks"][block["task"]], block["repeat"],
                           label, arm_of[label], output, "trial-%03d" % len(report["trials"]), position)
            report["trials"].append(result)
            report["summary"] = summarize_trials(report["trials"], chosen["comparisons"], weights)
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({k: result[k] for k in ("trial_id", "status", "accepted", "usage")}), flush=True)
            if result["usage"] is None and not result["partial_usage"]:
                stop = "unknown_usage"
                break
        if stop:
            break
        if report["summary"]["budget_tokens_lower_bound"] >= args.max_total_tokens:
            stop = "token_budget"
            break
    report["stop_reason"] = stop or "schedule_complete"
    report["summary"] = summarize_trials(report["trials"], chosen["comparisons"], weights)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"stop_reason": report["stop_reason"], "summary": {
        k: v for k, v in report["summary"].items() if k != "comparisons"}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
