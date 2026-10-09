#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, hashlib, io, json, os, pathlib, posixpath, random, re, shlex, shutil, signal, statistics, subprocess, sys, tarfile, tempfile, time, geb_telemetry, geb_check, geb_adapt, analyze_navigation
[OUTPUT]: 提供 Codex/Claude Code 三组/两组/A-A 设计的配对块 token 试点、外部源码准备、无模型任务校验、成本口径与定位指标汇总
[POS]: fugue-docs 评测包-受限 token 试点执行器;分离索引收益与流程开销(含钩子化流程),不宣称普遍节省
[PROTOCOL]: 改设计、任务格式或汇总口径时更新 token-pilot.md、test_token_pilot.py 并保留失败运行
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import random
import re
import shlex
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
DEFAULT_TEST_RULE = "Do not change pre-existing test files; add tests in a new file."
BASE_PROMPT = ("\nKeep the change scoped, preserve existing behavior, and run relevant tests. "
               "Both source code and existing project documentation are available to you. "
               "Do not use the network, publish, commit, or read outside this workspace and your isolated home. "
               "{test_rule} "
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
        acceptance_patch = raw.get("acceptance_patch")
        cwd = raw.get("cwd", data.get("cwd"))
        if cwd is not None and (not isinstance(cwd, str) or Path(cwd).is_absolute() or ".." in Path(cwd).parts):
            raise ValueError("cwd must be a relative directory inside the workspace: %r" % (cwd,))
        tasks[name] = {
            "name": name, "prompts": prompts, "acceptance": raw["acceptance"], "acceptance_files": files,
            "acceptance_patch": (path.parent / acceptance_patch).resolve() if acceptance_patch else None,
            "regression": raw.get("regression", data.get("regression", [])),
            "protected": raw.get("protected", data.get("protected", [])),
            "strip_exclude": raw.get("strip_exclude", data.get("strip_exclude", [])),
            # 验收前恢复到快照的已跟踪文件(例如既有测试代码):模型对它们的改动保留在 changes.patch,不参与判定
            "reset": raw.get("reset_before_validation", data.get("reset_before_validation", [])),
            "workspace_ignore": raw.get("workspace_ignore", data.get("workspace_ignore", [])),
            "test_rule": raw.get("test_rule", data.get("test_rule", DEFAULT_TEST_RULE)),
            # 验收与回归命令的运行目录(相对工作区);上游测试要求在 test/ 下运行时必须设置,否则可能空跑
            "cwd": cwd,
            "reference_patch": (path.parent / patch).resolve() if patch else None}
        tasks[name]["acceptance_targets"] = (patch_targets(tasks[name]["acceptance_patch"])
                                             if tasks[name]["acceptance_patch"] else [])
    source = data.get("source")
    if source is not None:
        if not all(isinstance(source.get(k), str) and source[k] for k in ("git", "commit")):
            raise ValueError("source needs git and commit")
        source = dict(source)
        if source.get("overlay"):
            source["overlay"] = str((path.parent / source["overlay"]).resolve())
    digest, secrets = hashlib.sha256(path.read_bytes()), set()
    for name in sorted(tasks):
        hidden = sorted(tasks[name]["acceptance_files"].values())
        for key in ("reference_patch", "acceptance_patch"):
            if tasks[name][key]:
                hidden.append(tasks[name][key])
        for secret in hidden:
            content = secret.read_bytes()
            digest.update(content)
            secrets.add(hashlib.sha256(content).hexdigest())
    if source and source.get("overlay"):
        digest.update(Path(source["overlay"]).read_bytes())
    return {"path": str(path), "source_ref": data.get("source_ref"), "source": source, "tasks": tasks,
            "sha256": digest.hexdigest(), "secret_hashes": secrets}


def patch_targets(patch):
    """补丁涉及的文件(相对仓库根目录);用于验收前恢复这些文件,以及排除保护检查。"""
    targets = []
    for line in Path(patch).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("diff --git a/"):
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                target = parts[1].strip()
                if target.startswith("/") or ".." in PurePosixPath(target).parts:
                    raise ValueError("acceptance patch target escapes the workspace: " + target)
                targets.append(target)
    return targets


def glob_regex(pattern):
    """仓库相对路径的 glob:"**/" 匹配任意层目录,"*" 与 "?" 不跨越 "/"。"""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matching(paths, patterns):
    regexes = [glob_regex(p) for p in patterns]
    return sorted(p for p in paths if any(r.match(p) for r in regexes))


def checked_members(tar, strip_prefix=None):
    """归档成员安全检查:拒绝绝对路径、上跳、硬链接、设备文件和指向树外的符号链接。

    树内相对符号链接保留(上游测试数据常用链接复用样式表);strip_prefix 只取该子目录并去掉前缀。
    """
    members = []
    prefix = strip_prefix.strip("/") + "/" if strip_prefix else ""
    for member in tar.getmembers():
        name = member.name
        if prefix:
            if not name.startswith(prefix) or name == prefix:
                continue
            member.name = name = name[len(prefix):]
        parts = PurePosixPath(name).parts
        if not name or name.startswith("/") or ".." in parts or not (
                member.isfile() or member.isdir() or member.issym()):
            raise ValueError("unsafe archive member: " + name)
        if member.issym():
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
            if member.linkname.startswith("/") or target == ".." or target.startswith("../"):
                raise ValueError("archive symlink leaves the tree: %s -> %s" % (name, member.linkname))
        members.append(member)
    return members


def extract(tar, destination, members):
    if hasattr(tarfile, "data_filter"):
        tar.extractall(destination, members=members, filter="data")
    else:
        tar.extractall(destination, members=members)


def materialize_source(source, cache):
    """按任务文件声明准备外部源码:固定提交的子目录 + 索引覆盖补丁,做成本地 git 仓库并复用。"""
    overlay = source.get("overlay")
    overlay_digest = hashlib.sha256(Path(overlay).read_bytes()).hexdigest()[:12] if overlay else "none"
    target = Path(cache).expanduser().resolve() / ("%s-%s-%s" % (
        re.sub(r"[^A-Za-z0-9]+", "-", source.get("name", "source")).strip("-"), source["commit"][:12], overlay_digest))
    marker = target / ".fugue-source.json"
    if marker.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # 暂存目录与缓存同盘,最后的 os.replace 不会跨设备
    staging = Path(tempfile.mkdtemp(prefix="source-", dir=str(target.parent)))
    try:
        env = git_environment(dict(os.environ))
        fetch = staging / "fetch"
        fetch.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=fetch, env=env, check=True)
        subprocess.run(["git", "fetch", "-q", "--depth", "1", source["git"], source["commit"]],
                       cwd=fetch, env=env, check=True)
        subdir = source.get("subdir")
        if source.get("tree"):
            tree = subprocess.check_output(["git", "rev-parse", "FETCH_HEAD:" + (subdir or "")], cwd=fetch,
                                           env=env, text=True).strip()
            if tree != source["tree"]:
                raise ValueError("fetched source tree %s does not match the pinned %s" % (tree, source["tree"]))
        archive = subprocess.check_output(["git", "archive", "FETCH_HEAD"] + ([subdir] if subdir else []),
                                          cwd=fetch, env=env)
        repo = staging / "repo"
        repo.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            extract(tar, repo, checked_members(tar, subdir))
        for command in (["git", "init", "-q"], ["git", "add", "-A"],
                        ["git", "-c", "commit.gpgsign=false", "commit", "-qm", "upstream " + source["commit"][:12]]):
            subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True)
        if overlay:
            subprocess.run(["git", "apply", "--whitespace=nowarn", overlay], cwd=repo, env=env, check=True)
            subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True)
            subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "fugue indexes"],
                           cwd=repo, env=env, check=True)
        (repo / ".fugue-source.json").write_text(json.dumps({k: source.get(k) for k in (
            "name", "git", "commit", "subdir", "tree")}, indent=1) + "\n", encoding="utf-8")
        # 标记文件不进入归档:写在提交之后,且加入本地排除
        with (repo / ".git" / "info" / "exclude").open("a") as f:
            f.write(".fugue-source.json\n")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(repo), str(target))
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
    return target


def archive_hashes(archive):
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        return {member.name: hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                for member in tar.getmembers() if member.isfile()}


def leaked(archive, secrets):
    """源码或技能归档里若含隐藏验收文件或参考补丁,模型就能直接看到答案。"""
    return sorted(name for name, digest in archive_hashes(archive).items() if digest in secrets)


SKILL_PATHS = ("SKILL.md", "scripts", "references", "adapters", "agents")
PLUGIN_PATHS = ("hooks", ".claude-plugin")


def skill_archive(revision, agent="codex"):
    """技能归档:Codex 只装技能;Claude 以插件形式加载,额外带上钩子与插件清单。"""
    present = set(subprocess.check_output(["git", "ls-tree", "--name-only", revision], cwd=ROOT, text=True).split())
    wanted = SKILL_PATHS + (PLUGIN_PATHS if agent == "claude" else ())
    if agent == "claude" and not all(p in present for p in PLUGIN_PATHS):
        raise ValueError("skill revision %s has no hooks/ and .claude-plugin/; Claude runs need v2.7 or later"
                         % revision[:12])
    paths = [p for p in wanted if p in present]
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
            # 按字节读写,保留原有换行符(CRLF 文件不能在无索引组里悄悄变成 LF)
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not geb_check.is_code_file(rel):
            # 托管规则块只出现在 AGENTS.md 等规则文件里;标记必须独占一行,避免误删源码常量
            block = re.compile(r"^" + re.escape(MANAGED_BEGIN) + r"$.*?^" + re.escape(MANAGED_END) + r"$\n?",
                               re.S | re.M)
            text, removed = block.subn("", text)
            if removed:
                path.write_bytes(text.encode("utf-8"))
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
            path.write_bytes("".join(kept + lines[geb_check.L3_SCAN_LINES:]).encode("utf-8"))
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
        extract(tar, workspace, checked_members(tar))
    strip = strip_indexes(workspace, task["strip_exclude"]) if arm == "noindex" else None
    subprocess.run(["git", "-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q"],
                   cwd=workspace, env=git_env, check=True, capture_output=True)
    ignore = task.get("workspace_ignore") or []
    if ignore:
        # 上游 .gitignore 不在子目录归档里时由任务文件补上(测试输出等),各组相同,只影响 git 状态
        info = workspace / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "exclude").write_text("".join(line + "\n" for line in ignore), encoding="utf-8")
    for command in (["git", "add", "-A"], ["git", "-c", "core.hooksPath=" + os.devnull,
                                           "-c", "commit.gpgsign=false", "commit", "-qm", "Fixed pilot snapshot"]):
        subprocess.run(command, cwd=workspace, env=git_env, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace, env=git_env, text=True).strip()
    return base, strip


def protected_snapshot(workspace, patterns, exclude=()):
    """受保护文件的初始内容;隐藏验收补丁会覆盖的文件不在此列(模型可以合理地修改它们)。"""
    files = {}
    for pattern in patterns:
        for path in workspace.glob(pattern):
            rel = path.relative_to(workspace).as_posix()
            if path.is_file() and rel not in exclude:
                files[rel] = path.read_bytes()
    return files


def expand(command):
    return [sys.executable if part == "{python}" else str(part) for part in command]


def prepare_acceptance(workspace, task, evidence_dir, git_env, base="HEAD"):
    """恢复需要重置的已跟踪文件与验收补丁目标,再打上隐藏验收补丁。返回一条 setup 检查记录。

    以快照提交 base 为准:运行后 "git add -N" 会让新文件出现在 ls-files 里,模型若自行提交,HEAD 也不再是快照。
    """
    tracked = set(subprocess.check_output(["git", "ls-tree", "-r", "-z", "--name-only", base], cwd=workspace,
                                          env=git_env, text=True).split("\0")) - {""}
    targets = task.get("acceptance_targets") or []
    restore = sorted(set(matching(tracked, task.get("reset") or [])) | (set(targets) & tracked))
    discarded = []
    for start in range(0, len(restore), 200):
        chunk = restore[start:start + 200]
        changed = subprocess.run(["git", "diff", "-z", "--name-only", base, "--"] + chunk, cwd=workspace,
                                 env=git_env, capture_output=True, text=True).stdout.split("\0")
        discarded += [name for name in changed if name and name not in targets]
        subprocess.run(["git", "checkout", base, "--"] + chunk, cwd=workspace, env=git_env,
                       check=True, capture_output=True)
    for target in targets:
        path = workspace / target
        if target not in tracked and (path.is_file() or path.is_symlink()):
            path.unlink()
    record = {"check": "acceptance_setup", "kind": "setup", "passed": True,
              "restored_files": len(restore), "discarded_edits": discarded}
    if task.get("acceptance_patch"):
        applied = subprocess.run(["git", "apply", "--whitespace=nowarn", str(task["acceptance_patch"])],
                                 cwd=workspace, env=git_env, capture_output=True, text=True)
        log = applied.stdout + applied.stderr
        (evidence_dir / "acceptance-setup.txt").write_text(log, encoding="utf-8")
        record["passed"] = applied.returncode == 0
        record["sha256"] = hashlib.sha256(log.encode()).hexdigest()
    return record


def validate(workspace, task, evidence_dir, timeout=120, kinds=("acceptance", "regression"), prepare=True,
             base="HEAD"):
    """模型结束后:恢复重置文件、打隐藏验收补丁、复制隐藏验收文件,再依次运行验收与回归命令;各类分开记录。

    prepare=False 只在原样工作区上运行命令(任务校验用它确认快照本身的回归是绿的)。
    """
    git_env = git_environment({k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "CLAUDE"))})
    setup = None
    if prepare and (task.get("acceptance_patch") or task.get("reset")):
        setup = prepare_acceptance(workspace, task, evidence_dir, git_env, base)
        if not setup["passed"]:
            return [setup]
    for dest, source in (task["acceptance_files"].items() if prepare else ()):
        target = workspace / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "CLAUDE"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results = [setup] if setup else []
    commands = [("acceptance", c) for c in task["acceptance"]] + [("regression", c) for c in task["regression"]]
    run_dir = workspace / task["cwd"] if task.get("cwd") else workspace
    for index, (kind, command) in enumerate(commands):
        if kind not in kinds:
            continue
        try:
            run = subprocess.run(expand(command), cwd=run_dir, capture_output=True, text=True,
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


CLAUDE_MODEL_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
CLAUDE_MESSAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def counter(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def claude_tokens(fresh, output, cache_read, cache_creation):
    """Anthropic 口径换算到试点口径:未缓存输入 = 新输入 + 写缓存(都按未命中处理),缓存命中单列。"""
    uncached = fresh + cache_creation
    result = {"input_tokens": uncached + cache_read, "cached_input_tokens": cache_read,
              "cache_creation_input_tokens": cache_creation, "fresh_input_tokens": fresh,
              "output_tokens": output, "uncached_input_tokens": uncached}
    result["total_tokens"] = result["input_tokens"] + output
    result["uncached_plus_output"] = uncached + output
    return result


def claude_result(events):
    results = [e for e in events if e.get("type") == "result"]
    return results[-1] if len(results) == 1 else None


def claude_usage(events):
    """完整用量取自唯一的 result 事件:modelUsage 含子代理与后台小模型;缺失时退回不含子代理的 usage 并标注。"""
    result = claude_result(events)
    if not result:
        return None
    by_model = result.get("modelUsage")
    if isinstance(by_model, dict) and by_model:
        rows = list(by_model.values())
        if any(not isinstance(row, dict) or not all(counter(row.get(k)) for k in CLAUDE_MODEL_KEYS) for row in rows):
            return None
        raw = [sum(row[k] for row in rows) for k in CLAUDE_MODEL_KEYS]
        usage = claude_tokens(raw[0], raw[1], raw[2], raw[3])
        usage["source"] = "result.modelUsage"
        usage["by_model"] = {name: {k: row[k] for k in CLAUDE_MODEL_KEYS + ("costUSD",) if k in row}
                             for name, row in by_model.items()}
    else:
        raw = result.get("usage")
        if not isinstance(raw, dict) or not all(counter(raw.get(k)) for k in CLAUDE_MESSAGE_KEYS):
            return None
        usage = claude_tokens(*(raw[k] for k in CLAUDE_MESSAGE_KEYS))
        usage["source"] = "result.usage_main_thread_only"
    cost = result.get("total_cost_usd")
    usage["cost_usd"] = cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    usage["turns"] = result.get("num_turns")
    return usage


def claude_partial_usage(events):
    """没有 result 事件时(超时、崩溃):按消息 ID 去重累加助手消息用量,只作下限。"""
    messages = {}
    for event in events:
        message = event.get("message") if event.get("type") == "assistant" else None
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict) or not message.get("id"):
            continue
        seen = messages.setdefault(message["id"], {k: 0 for k in CLAUDE_MESSAGE_KEYS})
        for key in CLAUDE_MESSAGE_KEYS:
            if counter(usage.get(key)):
                seen[key] = max(seen[key], usage[key])
    if not messages:
        return None
    usage = claude_tokens(*(sum(m[k] for m in messages.values()) for k in CLAUDE_MESSAGE_KEYS))
    usage.update(source="assistant_messages_lower_bound", messages=len(messages))
    return usage


# 与组别无关的故障:接口报错、额度用尽、执行中断、环境或执行器出错。它们不算任何一组的失败,出现即停止实验
INFRA_STATUSES = ("infrastructure_error", "setup_failed", "harness_failed")


INFRA_API_ERRORS = {"authentication_failed", "billing_error", "rate_limit", "overloaded", "server_error",
                    "cloud_credential_error"}
INFRA_TEXT = re.compile(r"(?i)\b(401|403|429|500|502|503|504|529)\b|overloaded|rate.?limit|usage limit|credit balance|"
                        r"billing|authenticat|unauthori[sz]ed|forbidden|econnrefused|enotfound|network error")


def claude_status(events, process_status):
    """result 为 success 且非错误才算完成。

    "success + is_error" 是接口报错:认证、额度、限流、过载、服务端错误是基础设施故障;其他(例如请求过长,
    可能由某组的行为引起)记为 agent_api_error,算该组失败。执行中断(error_during_execution)按基础设施故障处理。
    """
    result = claude_result(events)
    if process_status == "timeout":
        return "timeout"
    if result is None:
        return "runner_failed"
    subtype = str(result.get("subtype") or "error")
    if subtype == "success" and not result.get("is_error"):
        return "completed" if process_status == "completed" else "runner_failed"
    if subtype == "error_during_execution":
        return "infrastructure_error"
    if subtype == "success":
        retried = {str(e.get("error")) for e in events if e.get("type") == "system" and e.get("subtype") == "api_retry"}
        if retried & INFRA_API_ERRORS or INFRA_TEXT.search(str(result.get("result") or "")):
            return "infrastructure_error"
        return "agent_api_error"
    return "agent_" + re.sub(r"[^a-z0-9_]+", "_", subtype.lower())


def claude_session(events):
    """init 事件里的会话配置与结束统计,用于事后核对隔离、插件加载与权限拒绝。"""
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    result = claude_result(events) or {}
    info = {"init_seen": init is not None}
    if init:
        plugins = []
        for plugin in init.get("plugins") or []:
            plugins.append(plugin.get("name") if isinstance(plugin, dict) else str(plugin))
        info.update(model=init.get("model"), version=init.get("claude_code_version"),
                    permission_mode=init.get("permissionMode"), plugins=plugins,
                    tools=sorted(str(t) for t in init.get("tools") or []),
                    mcp_servers=[s.get("name") if isinstance(s, dict) else str(s)
                                 for s in init.get("mcp_servers") or []])
        info["api_key_source"] = init.get("apiKeySource")
    denials = result.get("permission_denials")
    reasons = [e for e in events if e.get("type") == "system" and e.get("subtype") == "permission_denied"]
    info.update(result_subtype=result.get("subtype"), result_is_error=bool(result.get("is_error")),
                num_turns=result.get("num_turns"), duration_api_ms=result.get("duration_api_ms"),
                permission_denials=len(denials) if isinstance(denials, list) else None,
                denied_tools=sorted({str(d.get("tool_name")) for d in denials or [] if isinstance(d, dict)}),
                denial_reasons=[{"tool": e.get("tool_name"), "type": e.get("decision_reason_type"),
                                 "message": str(e.get("message") or e.get("decision_reason") or "")[:160]}
                                for e in reasons[:10]],
                # 仅作记录:沙箱生效时,写工作区外绝对路径的命令也会被判为"需要批准",不能据此推断沙箱失效
                bash_approval_denials=sum(1 for e in reasons if e.get("tool_name") == "Bash"
                                          and "approval" in str(e.get("message") or "").lower()),
                api_errors=sorted({str(e.get("error")) for e in events
                                   if e.get("type") == "system" and e.get("subtype") == "api_retry"}))
    if result.get("is_error"):
        info["result_error"] = str(result.get("result") or "")[:300]
    return info


def hook_stats(data_dir):
    """赋格钩子在试验私有目录留下的会话状态与错误日志;没有会话状态说明钩子没有运行。"""
    data_dir = Path(data_dir)
    states = []
    for path in sorted((data_dir / "sessions").glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and str(state.get("schema", "")).startswith("geb.hook-session"):
            states.append(state)
    keys = ("stops", "blocks", "block_chars", "auto_writes", "skipped_turns")
    stats = {"sessions": len(states), **{k: sum(int(s.get(k) or 0) for s in states) for k in keys}}
    stats["prompted_gaps"] = sum(len(s.get("prompted") or {}) for s in states)
    try:
        errors = (data_dir / "hook-errors.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        errors = []
    stats["errors"] = len(errors)
    stats["error_samples"] = [line[:300] for line in errors[:5]]
    return stats


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


OPTIONAL_USAGE_KEYS = ("cache_creation_input_tokens", "cost_usd")


def token_metrics(weights=None, trials=()):
    keys = ["uncached_plus_output", "total_tokens", "uncached_input_tokens", "cached_input_tokens", "output_tokens"]
    # Claude 记录还有写缓存与美元成本;只有试验里出现过才列出,Codex 报告保持原样
    keys += [k for k in OPTIONAL_USAGE_KEYS
             if any(isinstance((t.get("usage") or {}).get(k), (int, float)) for t in trials)]
    if weights:
        keys.append("weighted_cost")
    return {key: (lambda trial, key=key: usage_value(trial.get("usage"), key, weights)) for key in keys}


def budget_cost(trial):
    """美元预算口径(只有 Claude 上报):完整记录的 cost_usd,否则 0。"""
    usage = trial.get("usage")
    value = usage.get("cost_usd") if isinstance(usage, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


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
    better = worse = ties = skipped = infrastructure = 0
    for block in blocks.values():
        a, b = block.get(reference), block.get(treatment)
        if not a or not b:
            continue
        if a.get("status") in INFRA_STATUSES or b.get("status") in INFRA_STATUSES:
            infrastructure += 1  # 接口或环境故障不是哪一组的表现
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
            "skipped_unknown_usage": skipped, "skipped_infrastructure": infrastructure,
            "sign_test_p": nav.sign_test(better, worse)}


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
    metrics = token_metrics(weights, trials)
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
            "measured_uncached_plus_output": sum_known([usage_value(t["usage"], PRIMARY_METRIC) for t in measured]),
            "measured_cost_usd": sum_known([usage_value(t["usage"], "cost_usd") for t in measured])}
    positions = {}
    for trial in trials:
        value = usage_value(trial.get("usage"), PRIMARY_METRIC) if trial.get("accepted") else None
        if trial.get("block_position") is not None and value is not None:
            positions.setdefault(str(trial["block_position"]), []).append(value)
    return {"attempted": len(trials), "accepted": sum(bool(t.get("accepted")) for t in trials),
            "unknown_usage_trials": len(trials) - len(known),
            "partial_usage_trials": sum(1 for t in trials if not t.get("usage") and t.get("partial_usage")),
            "budget_tokens_lower_bound": sum(budget_tokens(t) for t in trials),
            "budget_cost_usd": round(sum(budget_cost(t) for t in trials), 6),
            "trials_with_outside_workspace_reads": [
                t.get("trial_id") for t in trials
                if nav.nav_value(t.get("navigation"), "total", "outside_workspace_reads")],
            "primary_metric_median_by_block_position": {
                k: statistics.median(v) for k, v in sorted(positions.items())},
            "experiment_total_tokens": (sum(t["usage"]["total_tokens"] for t in known)
                                        if known and len(known) == len(trials) else None),
            "known_total_tokens": sum(t["usage"]["total_tokens"] for t in known) if known else None,
            "primary_metric": PRIMARY_METRIC, "by_condition": by_condition, "comparisons": result}


def run_process(command, directory, env, prompt, timeout, cwd):
    """运行代理进程:事件流写 events.jsonl,超时整组终止。"""
    with (directory / "events.jsonl").open("w") as out, (directory / "stderr.log").open("w") as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err, env=env,
                                   cwd=cwd, text=True, start_new_session=True)
        try:
            process.communicate(prompt, timeout=timeout)
            return "completed" if process.returncode == 0 else "runner_failed"
        except subprocess.TimeoutExpired:
            kill_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_group(process.pid, signal.SIGKILL)
                process.wait()
            return "timeout"


def run_codex(args, workspace, directory, env, prompt):
    command = [args.codex, "exec", "--json", "--ignore-user-config",
               "--skip-git-repo-check", "--sandbox", "workspace-write", "--color", "never",
               "-m", args.model, "-c", 'model_reasoning_effort="' + (args.effort or "medium") + '"',
               "-c", 'approval_policy="never"', "-C", str(workspace), "-"]
    return run_process(command, directory, env, prompt, args.timeout, directory)


# 白名单:试验进程只拿到这些环境变量(加 LC_*、--claude-env 指定的名字和凭据)。
# PWD、调用脚本的设置(TASKS、PILOT_OUTPUT 等)和其他工具的令牌一律不传,模型用 env 也看不到任务名或输出目录
CLAUDE_ENV_ALLOW = ("PATH", "SHELL", "USER", "LOGNAME", "LANG", "TERM", "COLORTERM", "TZ", "TMPDIR", "TEMP", "TMP",
                    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
                    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
                    "all_proxy", "PYENV_ROOT", "PYENV_VERSION", "ASDF_DATA_DIR", "SYSTEMROOT")
CREDENTIAL_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def claude_credentials(environ=None):
    """选一种凭据:订阅令牌优先;只有没有令牌时才用 API key(Claude Code 里 API key 优先级更高,一起传会按 API 计费)。"""
    environ = os.environ if environ is None else environ
    if environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return {"CLAUDE_CODE_OAUTH_TOKEN": environ["CLAUDE_CODE_OAUTH_TOKEN"]}
    if environ.get("ANTHROPIC_API_KEY"):
        chosen = {"ANTHROPIC_API_KEY": environ["ANTHROPIC_API_KEY"]}
        if environ.get("ANTHROPIC_BASE_URL"):
            chosen["ANTHROPIC_BASE_URL"] = environ["ANTHROPIC_BASE_URL"]
        return chosen
    return {}


def claude_environment(home, extra_names=(), credentials=None):
    """试验私有的 Claude Code 配置目录:不读用户 CLAUDE.md、记忆、插件和设置;凭据只经环境变量传入。"""
    env = {k: v for k, v in os.environ.items()
           if k in CLAUDE_ENV_ALLOW or k.startswith("LC_") or k in extra_names}
    real_home = Path.home()
    for name, default in (("PYENV_ROOT", ".pyenv"), ("ASDF_DATA_DIR", ".asdf")):
        # HOME 换成私有目录后,pyenv/asdf 的 shim 会去私有 HOME 找安装;指回真实位置
        if name not in env and str(real_home / default) in env.get("PATH", ""):
            env[name] = str(real_home / default)
    env.update(claude_credentials() if credentials is None else credentials)
    config = home / ".claude"
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(config), FUGUE_DATA_DIR=str(config / "fugue"),
               PYTHONDONTWRITEBYTECODE="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
               DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
    return env


def claude_settings(sandbox, deny_read=()):
    """只允许本地工作:网页工具拒绝;Bash 在沙箱里自动放行(写入限于工作区、无网络),不允许逃逸。

    on:沙箱不可用时拒绝启动,另外禁止 Bash 读取答案所在目录(本仓库、输出目录、源码缓存等)并清掉凭据变量;
    basic:只用早期版本也认识的沙箱开关(某个键不被认识时,Claude Code 会静默丢掉整段沙箱设置);
    off:不用沙箱,直接放行 Bash,只适合一次性虚拟机。
    """
    settings = {"permissions": {"deny": ["WebFetch", "WebSearch"]}}
    if sandbox == "off":
        settings["permissions"]["allow"] = ["Bash"]
        return settings
    settings["sandbox"] = {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}
    if sandbox == "on":
        settings["sandbox"].update(
            failIfUnavailable=True,
            credentials={"envVars": [{"name": name, "mode": "deny"} for name in CREDENTIAL_VARS]})
        if deny_read:
            settings["sandbox"]["filesystem"] = {"denyRead": [str(p) for p in deny_read]}
    return settings


def claude_command(args, plugin=None):
    settings = claude_settings(getattr(args, "claude_sandbox", "on"), getattr(args, "deny_read", ()))
    command = [args.claude, "-p", "--output-format", "stream-json", "--verbose", "--model", args.model,
               "--permission-mode", "acceptEdits", "--settings", json.dumps(settings, sort_keys=True)]
    if plugin:
        # 只加载插件,不用 --add-dir 把插件目录列为工作目录:那会让赋格组的环境说明多出一个目录,
        # 而钩子流程本来就不需要模型读技能文件(读取被拒会记入 permission_denials)
        command += ["--plugin-dir", str(plugin)]
    if args.effort:
        command += ["--effort", args.effort]
    if getattr(args, "max_turns", None):
        command += ["--max-turns", str(args.max_turns)]
    if getattr(args, "max_budget_usd", None):
        command += ["--max-budget-usd", str(args.max_budget_usd)]
    return command + list(getattr(args, "claude_arg", None) or [])


def run_claude(args, workspace, directory, env, prompt, plugin):
    return run_process(claude_command(args, plugin), directory, env, prompt, args.timeout, workspace)


def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def build_prompt(task, style, arm, agent="codex"):
    """Claude 各组提示词完全相同(无钩子的组本来就没有插件);Codex 非赋格组保留原有的“不调用技能”说明。"""
    prompt = task["prompts"].get(style)
    if prompt is None:
        raise ValueError("task %s has no %s prompt" % (task["name"], style))
    text = prompt + BASE_PROMPT.format(test_rule=task.get("test_rule") or DEFAULT_TEST_RULE)
    return text + ("" if agent == "claude" or arm == "fugue" else PLAIN_PROMPT)


def trial(args, archive, skill_dir, task, repeat, label, arm, parent, trial_dir=None, position=None):
    """在中性临时目录里跑一次:模型看到的路径不含任务名或组名,结束后整体移入输出目录。"""
    trial_id = "%s-%d-%s" % (task["name"], repeat, label)
    trial_dir = trial_dir or trial_id
    # 真实路径,与沙箱读取禁区、钩子的项目根判定一致
    directory = Path(tempfile.mkdtemp(prefix="run-")).resolve()
    try:
        result = run_trial(args, archive, skill_dir, task, directory, arm)
    finally:
        shutil.move(str(directory), str(parent / trial_dir))
    result.update(trial_id=trial_id, trial_dir=trial_dir, task=task["name"], repeat=repeat,
                  condition=label, arm=arm, block_position=position)
    return result


# CLI 在沙箱起不来时写到 stderr 的提示:降级运行("Sandbox disabled ... WITHOUT sandboxing")或拒绝启动(failIfUnavailable)
SANDBOX_DISABLED = re.compile(r"(?i)sandbox disabled|without sandboxing|without a working sandbox|"
                              r"sandbox[^\n]*required but unavailable")


def environment_signature(session):
    """各组应当相同的会话环境:赋格插件之外的插件与工具(Skill 工具可能只因赋格技能才出现,不比较)。"""
    if not session or not session.get("init_seen"):
        return None
    return {"version": session.get("version"), "model": session.get("model"),
            "api_key_source": session.get("api_key_source"),
            "plugins": sorted(p for p in session.get("plugins") or [] if "fugue" not in str(p)),
            "tools": sorted(t for t in session.get("tools") or [] if t != "Skill"),
            "mcp_servers": sorted(str(m) for m in session.get("mcp_servers") or [])}


def setup_problem(arm, session, hooks, stderr="", sandbox="off"):
    """隔离或插件加载出错会让整组数据失效:赋格组必须加载插件且钩子运行,其他组不得加载赋格插件;
    要求沙箱时沙箱必须可用(否则 -p 模式下 Bash 全被拒绝,模型无法跑测试)。"""
    if sandbox != "off" and SANDBOX_DISABLED.search(stderr or ""):
        return "sandbox_unavailable"
    if not session.get("init_seen"):
        return None  # 早期失败时没有 init 事件,交给状态判定
    loaded = any("fugue" in str(name) for name in session.get("plugins") or [])
    if arm == "fugue" and not loaded:
        return "fugue_plugin_not_loaded"
    if arm != "fugue" and loaded:
        return "unexpected_fugue_plugin"
    if arm == "fugue" and hooks is not None and hooks["sessions"] == 0:
        return "fugue_hooks_not_run"
    return None


def run_trial(args, archive, skill_dir, task, directory, arm):
    agent = getattr(args, "agent", "codex")
    workspace = directory / "workspace"
    home = directory / "home"
    if agent == "claude":
        env = claude_environment(home, getattr(args, "claude_env", None) or ())
        (home / ".claude").mkdir(parents=True)
    else:
        codex_home = home / ".codex"
        codex_home.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
        env.update(HOME=str(home), CODEX_HOME=str(codex_home), PYTHONDONTWRITEBYTECODE="1")
    git_env = git_environment(env)
    base_commit, strip = prepare_workspace(archive, workspace, arm, task, git_env)
    plugin = None
    if agent == "claude":
        if arm == "fugue":
            # 路径含 /skills/fugue-docs/,定位分析把对它的读取记为技能读取而不是越界读取
            plugin = home / "skills" / "fugue-docs"
            shutil.copytree(skill_dir, plugin)
    else:
        auth = Path(args.auth_home).expanduser() / "auth.json"
        if auth.is_file():
            (codex_home / "auth.json").symlink_to(auth.resolve())
        if arm == "fugue":
            target = home / ".agents" / "skills" / "fugue-docs"
            target.parent.mkdir(parents=True)
            shutil.copytree(skill_dir, target)
            (codex_home / "AGENTS.md").write_text(
                "For this development task, use the fugue-docs skill at " + str(target / "SKILL.md") + ".\n",
                encoding="utf-8")
    protected = protected_snapshot(workspace, task["protected"], set(task.get("acceptance_targets") or []))
    prompt = build_prompt(task, args.prompt_style, arm, agent)
    started = time.monotonic()
    try:
        if agent == "claude":
            status = run_claude(args, workspace, directory, env, prompt, plugin)
        else:
            status = run_codex(args, workspace, directory, env, prompt)
    finally:
        if agent != "claude":
            auth_link = codex_home / "auth.json"
            if auth_link.is_symlink() or auth_link.is_file():
                auth_link.unlink()
    elapsed = round(time.monotonic() - started, 3)
    events = nav.load_events(directory / "events.jsonl")
    extra = {}
    if agent == "claude":
        status = claude_status(events, status)
        usage = claude_usage(events)
        partial = claude_partial_usage(events) if usage is None else None
        session = claude_session(events)
        hooks = hook_stats(home / ".claude" / "fugue") if arm == "fugue" else None
        try:
            stderr = (directory / "stderr.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            stderr = ""
        problem = setup_problem(arm, session, hooks, stderr, getattr(args, "claude_sandbox", "on"))
        if problem:
            status = "setup_failed"
        extra = {"claude_session": session, "hooks": hooks, "setup_problem": problem}
        coverage = ("complete_result_model_usage" if usage and usage["source"] == "result.modelUsage"
                    else "main_thread_only" if usage else None)
    else:
        usage = cli_usage(events)
        partial = partial_usage(codex_home, events) if usage is None else None
        coverage = "complete_cli_turn" if usage else None
    diff, commits, checks = b"", "", []
    try:
        # 先保存模型改动(含新建文件、以及模型自行提交的内容),再复制隐藏验收文件,补丁里不会混入验收脚本
        subprocess.run(["git", "add", "-A", "-N"], cwd=workspace, env=git_env, check=True, capture_output=True)
        diff = subprocess.check_output(["git", "diff", "--binary", base_commit], cwd=workspace, env=git_env)
        (directory / "changes.patch").write_bytes(diff)
        commits = subprocess.run(["git", "rev-list", "--count", base_commit + "..HEAD"], cwd=workspace,
                                 env=git_env, capture_output=True, text=True).stdout.strip()
        checks = (validate(workspace, task, directory, args.validation_timeout, base=base_commit)
                  if status == "completed" else [])
        if checks:
            checks.append({"check": "protected_files_unchanged", "kind": "protection", "passed": all(
                (workspace / name).is_file() and (workspace / name).read_bytes() == content
                for name, content in protected.items())})
    except (subprocess.CalledProcessError, OSError) as error:
        # 例如模型留下 .git/index.lock、把文件换成目录:保住这次的用量,由主循环停止实验
        extra["harness_error"] = "%s: %s" % (type(error).__name__, str(error)[:300])
        status, checks = "harness_failed", []
    result = {"status": status, "agent": agent, "base_commit": base_commit, "strip": strip,
              "model_commits": int(commits) if commits.isdigit() else None,
              "changes_sha256": hashlib.sha256(diff).hexdigest(),
              "usage": usage, "elapsed_seconds": elapsed, "validation": checks,
              "partial_usage": partial,
              "usage_coverage": coverage or ("partial_lower_bound" if partial else "unknown"),
              "accepted": status == "completed" and bool(checks) and all(c["passed"] for c in checks),
              "navigation": nav.analyze_events(events),
              "events_sha256": hashlib.sha256((directory / "events.jsonl").read_bytes()).hexdigest()}
    result.update(extra)
    return result


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
                else:
                    # 快照本身的回归在打验收补丁之前运行:验收测试可能就在回归套件里
                    baseline = directory / "evidence-baseline"
                    baseline.mkdir()
                    row["baseline"] = validate(workspace, task, baseline, timeout, kinds=("regression",),
                                               prepare=False)
                row[phase] = validate(workspace, task, evidence, timeout,
                                      kinds=("acceptance", "regression") if phase == "after" else ("acceptance",))
            before = row["before"]
            expectations = {
                # 每条验收命令都要先失败:只要求"至少一条失败"时,空跑的命令会混进来
                "each_acceptance_check_fails_before": all(not c["passed"] for c in before
                                                          if c["kind"] == "acceptance"),
                "acceptance_setup_applies": all(c["passed"] for c in before if c["kind"] == "setup"),
                "regression_passes_before": all(c["passed"] for c in row["baseline"]),
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


SELFTEST_MARKER = "fugue-selftest-denied-marker"


def sandbox_test_command(task):
    """模型在沙箱里会怎样跑回归:{python} 换成 PATH 上的 python3,需要时先 cd 到任务的运行目录。"""
    if not task or not task.get("regression"):
        return None
    command = " ".join(shlex.quote("python3" if part == "{python}" else str(part)) for part in task["regression"][0])
    return ("cd %s && %s" % (shlex.quote(task["cwd"]), command)) if task.get("cwd") else command


def claude_selftest(args, skill_dir, keep_dir=None, archive=None, task=None):
    """零成本自检:本地假接口驱动真实 claude 跑一次赋格组式会话,不调用模型、不用你的凭据。

    确认这台机器、这个 CLI 版本上:设置被接受、沙箱生效(Bash 自动放行、工作区外写入被拦)、读取禁区与凭据清除生效、
    沙箱里 python3 与 git 能用、任务自己的测试能跑通、赋格插件加载、钩子运行并在新文件缺语义时拦截一次。
    给出 archive 与 task 时用真实的有索引工作区。任何一项不成立,付费试验就不该开始。
    """
    import claude_mock_api
    # 真实路径:读取禁区与探针命令里的路径必须一致(macOS 的 /var 是 /private/var 的符号链接)
    directory = Path(tempfile.mkdtemp(prefix="run-")).resolve()
    try:
        workspace, home, denied = directory / "workspace", directory / "home", directory / "denied"
        denied.mkdir()
        (home / ".claude").mkdir(parents=True)
        (denied / "marker.txt").write_text(SELFTEST_MARKER + "\n", encoding="utf-8")
        git_env = git_environment(dict(os.environ))
        if archive is not None and task is not None:
            prepare_workspace(archive, workspace, "index", task, git_env)
        else:
            workspace.mkdir()
            (workspace / "PROJECT_INDEX.md").write_text("# Self-test project (L1)\n\nFiles: core.py\n",
                                                        encoding="utf-8")
            (workspace / "core.py").write_text('"""\n[INPUT]: none\n[OUTPUT]: VALUE\n[POS]: self-test module\n'
                                               '[PROTOCOL]: update this header\n"""\nVALUE = 1\n', encoding="utf-8")
            for command in (["git", "-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q"],
                            ["git", "add", "-A"], ["git", "-c", "commit.gpgsign=false", "commit", "-qm", "self-test"]):
                subprocess.run(command, cwd=workspace, env=git_env, check=True, capture_output=True)
        plugin = home / "skills" / "fugue-docs"
        shutil.copytree(str(skill_dir), str(plugin))
        outside = directory / "outside.txt"
        # 每项单独一条命令:复合命令会被 CLI 的静态检查整体判为"需要批准",测不到沙箱本身。
        # 写工作区外用相对路径:写绝对路径会在执行前就被判为需要批准,只有相对路径能测到沙箱在执行时拦截
        credential_probe = "env | grep -c " + " ".join("-e %s=" % name for name in CREDENTIAL_VARS)
        probes = [("read", "cat %s" % (denied / "marker.txt")), ("write", "echo inside > selftest_inside.txt"),
                  ("env", credential_probe), ("outside", "echo outside > ../outside.txt"),
                  # macOS 的 /usr/bin/python3 和 git 是转调 xcrun 的壳;PATH 上的解释器也可能落在读取禁区里
                  ("python", 'python3 -B -c "import sys; print(sys.version)"'), ("git", "git status --short")]
        tests = sandbox_test_command(task)
        if tests:
            probes.append(("tests", tests))
        steps = [{"name": "Bash", "input": {"command": command, "description": "Self-test probe: " + name}}
                 for name, command in probes]
        steps.append({"name": "Write", "input": {"file_path": str(workspace / "added.py"),
                                                 "content": "def added():\n    return 1\n"}})
        sandbox = getattr(args, "claude_sandbox", "on")
        probe_args = argparse.Namespace(**vars(args))
        probe_args.deny_read = list(getattr(args, "deny_read", ())) + [str(denied.resolve())]
        probe_args.max_turns, probe_args.max_budget_usd = None, None
        with claude_mock_api.MockMessagesAPI(steps) as api:
            env = claude_environment(home, getattr(args, "claude_env", None) or (),
                                     credentials={"ANTHROPIC_API_KEY": "sk-ant-selftest-not-a-real-key",
                                                  "ANTHROPIC_BASE_URL": api.url})
            for name in ("NO_PROXY", "no_proxy"):
                env[name] = ",".join(filter(None, [env.get(name), "127.0.0.1", "localhost"]))
            status = run_process(claude_command(probe_args, plugin), directory, env,
                                 "Self-test run. Follow the scripted steps.", 600, workspace)
        events = nav.load_events(directory / "events.jsonl")
        session, hooks = claude_session(events), hook_stats(home / ".claude" / "fugue")
        results = {part.get("tool_use_id"): part for event in events if event.get("type") == "user"
                   for part in (event.get("message") or {}).get("content") or []
                   if isinstance(part, dict) and part.get("type") == "tool_result"}
        outputs = {}
        for index, (name, _command) in enumerate(probes):
            part = results.get("toolu_selftest_%d" % index)
            outputs[name] = None if part is None else {"error": bool(part.get("is_error")),
                                                       "text": nav.result_text(part.get("content"))[-300:]}
        lines = (outputs["env"] or {}).get("text", "").strip().splitlines()
        checks = {"run_completed": claude_status(events, status) == "completed",
                  "fugue_plugin_loaded": any("fugue" in str(p) for p in session.get("plugins") or []),
                  "hooks_ran": hooks["sessions"] >= 1 and hooks["errors"] == 0,
                  "stop_hook_prompted_semantics": hooks["blocks"] >= 1,
                  "bash_ran": outputs["write"] is not None and not outputs["write"]["error"]
                  and (workspace / "selftest_inside.txt").is_file(),
                  "python3_runs": outputs["python"] is not None and not outputs["python"]["error"],
                  "git_runs": outputs["git"] is not None and not outputs["git"]["error"]}
        if tests:
            checks["task_tests_pass_in_sandbox"] = outputs["tests"] is not None and not outputs["tests"]["error"]
        try:
            stderr = (directory / "stderr.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            stderr = ""
        if sandbox != "off":
            # 没有沙箱时,acceptEdits 也会放行工作区内写入、拒绝工作区外写入,探针结果相同;只能看 CLI 自己的提示
            checks["sandbox_active"] = not SANDBOX_DISABLED.search(stderr)
            checks["outside_write_blocked"] = outputs["outside"] is not None and not outside.exists()
        if sandbox == "on":
            checks["deny_read_effective"] = (outputs["read"] is not None
                                             and SELFTEST_MARKER not in outputs["read"]["text"])
            checks["credentials_hidden_from_bash"] = bool(lines) and lines[-1].strip() == "0"
        failed = sorted(name for name, passed in checks.items() if not passed)
        hint = None
        if failed and SANDBOX_DISABLED.search(stderr):
            hint = ("the sandbox cannot start on this machine (see stderr_tail); on Linux install bubblewrap and "
                    "socat. macOS has it built in")
        elif failed and sandbox == "on" and set(failed) & {"bash_ran", "outside_write_blocked", "deny_read_effective",
                                                            "credentials_hidden_from_bash"}:
            hint = ("this Claude Code version may not accept every sandbox key; it then drops the whole sandbox "
                    "section silently. Retry the self-test with --claude-sandbox basic, or update Claude Code")
        return {"ok": not failed, "failed": failed, "checks": checks, "hint": hint, "test_command": tests,
                "claude_version": session.get("version"), "mock_requests": len(api.requests),
                "probe_outputs": outputs, "hooks": hooks,
                "session": {k: session.get(k) for k in ("init_seen", "plugins", "permission_mode",
                                                         "denial_reasons", "result_subtype")},
                "stderr_tail": stderr[-400:]}
    finally:
        if keep_dir:
            shutil.move(str(directory), str(keep_dir))
        else:
            shutil.rmtree(str(directory), ignore_errors=True)


def deny_read_paths(args, task_set, source, output=None):
    """沙箱里 Bash 不得读取的目录:本仓库(含隐藏答案)、任务目录、源码与缓存(含带索引的副本)、输出目录、
    你的 Claude/Codex 配置与历史试点结果。不会屏蔽系统临时目录或整个家目录(试验本身在临时目录里)。"""
    home = Path.home().resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    # 一律比较真实路径:macOS 的临时目录 /var 是 /private/var 的符号链接
    output = Path(output).expanduser().resolve() if output else None
    candidates = [ROOT, Path(task_set["path"]).parent, Path(args.source_cache), source, home / ".claude",
                  home / ".codex", Path(args.auth_home), home / "fugue-pilot"]
    if output:
        candidates.append(output)
    paths = []
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if path == home or path == temp or path in temp.parents or path == Path(path.anchor):
            continue
        if (path.exists() or path == output) and str(path) not in paths:
            paths.append(str(path))
    return paths


def stale_trial_dirs():
    """系统临时目录里残留的试验目录(含 workspace 与 home):正常结束会移走,残留说明上次被强行中断。"""
    temp = Path(tempfile.gettempdir())
    try:
        return sorted(str(p) for p in temp.glob("run-*") if (p / "workspace").is_dir() and (p / "home").is_dir())
    except OSError:
        return []


def agent_version(args):
    binary = args.claude if args.agent == "claude" else args.codex
    try:
        run = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (run.stdout or run.stderr).strip()[:200] or None


def resolve_source(args, task_set, parser):
    """源码仓库:显式 --source-repo;否则任务文件声明的外部源码(固定提交 + 索引覆盖);否则本仓库。"""
    if args.source_repo:
        return Path(args.source_repo).resolve(), args.ref or task_set["source_ref"]
    if task_set.get("source"):
        try:
            return materialize_source(task_set["source"], args.source_cache), args.ref or "HEAD"
        except (subprocess.CalledProcessError, ValueError, OSError) as error:
            parser.error("cannot prepare the external source: %s" % error)
    return ROOT.resolve(), args.ref or task_set["source_ref"]


def main():
    parser = argparse.ArgumentParser(description="Bounded paired-block token pilot, not a universal benchmark")
    parser.add_argument("--ref", help="Fixed source revision (default: the tasks file source_ref)")
    parser.add_argument("--source-repo", help="Git repository the tasks run against (default: the tasks file "
                        "source, materialized under --source-cache, else this repository)")
    parser.add_argument("--source-cache", default=str(Path.home() / ".cache" / "fugue-pilot" / "sources"),
                        help="Where external sources declared by the tasks file are fetched and reused")
    parser.add_argument("--tasks-file", default=str(DEFAULT_TASKS_FILE))
    parser.add_argument("--skill-ref", help="fugue-docs revision installed in the fugue arm "
                        "(default: the source revision when testing this repository, else HEAD)")
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--claude-sandbox", choices=("on", "basic", "off"), default="on",
                        help="on: Bash runs in Claude Code's sandbox (writes limited to the workspace, no network), "
                        "refuses to start without it, cannot read answer directories and sees no credentials; "
                        "basic: only the sandbox keys older versions accept; "
                        "off: Bash is allowed unsandboxed, only for disposable machines")
    parser.add_argument("--claude-arg", action="append", default=[],
                        help="Extra argument passed to every claude call (repeatable)")
    parser.add_argument("--claude-env", action="append", default=[],
                        help="Extra environment variable name passed to claude (repeatable); "
                        "everything else outside a small allowlist is withheld")
    parser.add_argument("--claude-selftest", action="store_true",
                        help="Only run the zero-cost Claude Code self-test against a local mock API, then exit")
    parser.add_argument("--skip-claude-selftest", action="store_true",
                        help="Do not run the self-test before --execute (not recommended)")
    parser.add_argument("--max-turns", type=int, help="Claude only: per-trial turn limit")
    parser.add_argument("--max-budget-usd", type=float, help="Claude only: per-trial spending limit")
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"),
                        help="Codex default medium; Claude uses its own default unless set")
    parser.add_argument("--design", choices=("three-arm", "two-arm", "aa"), default="three-arm")
    parser.add_argument("--aa-arm", choices=ARMS, default="index", help="Arm repeated in an A/A noise run")
    parser.add_argument("--prompt-style", choices=("symptom", "named"), default="symptom",
                        help="symptom prompts describe behavior without naming files or functions")
    parser.add_argument("--tasks", nargs="+")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--validation-timeout", type=int, default=120)
    parser.add_argument("--max-total-tokens", type=int, default=300000,
                        help="Experiment budget on total tokens including cache reads, checked after each block")
    parser.add_argument("--max-total-cost-usd", type=float,
                        help="Claude only: experiment budget on reported cost, checked after each block")
    parser.add_argument("--cost-weights", help="Relative weights, e.g. uncached_input_tokens=1,cached_input_tokens=0.1,output_tokens=4")
    parser.add_argument("--seed", type=int, default=730)
    parser.add_argument("--auth-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--output", help="Local private output directory; raw logs are not public results")
    parser.add_argument("--verify-tasks", action="store_true",
                        help="Without model calls, check every arm's acceptance against the reference patches")
    parser.add_argument("--execute", action="store_true", help="Without this, print the plan without model calls")
    args = parser.parse_args()
    if args.agent == "codex" and args.effort == "max":
        parser.error("--effort max is a Claude level")
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
    if not 1 <= args.repeats <= 20 or args.timeout <= 0 or args.max_total_tokens <= 0 or (
            args.max_total_cost_usd is not None and args.max_total_cost_usd <= 0):
        parser.error("require 1..20 repeats and positive budgets")
    source, ref = resolve_source(args, task_set, parser)
    if not ref:
        parser.error("--ref is required when the tasks file has no source_ref")
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
    try:
        skill = skill_archive(skill_rev, args.agent)
    except ValueError as error:
        parser.error(str(error))
    exposed = leaked(archive, task_set["secret_hashes"]) + leaked(skill, task_set["secret_hashes"])
    if exposed:
        parser.error("hidden acceptance files or reference patches are visible to the model: " + ", ".join(exposed))
    args.deny_read = deny_read_paths(args, task_set, source,
                                     Path(args.output).expanduser().resolve() if args.output else None)
    if args.claude_selftest:
        if args.agent != "claude":
            parser.error("--claude-selftest needs --agent claude")
        with tempfile.TemporaryDirectory(prefix="fugue-selftest-") as directory:
            skill_dir = Path(directory) / "skill"
            skill_dir.mkdir()
            with tarfile.open(fileobj=io.BytesIO(skill)) as tar:
                extract(tar, skill_dir, checked_members(tar))
            result = claude_selftest(args, skill_dir, archive=archive, task=task_set["tasks"][names[0]])
        result.update(sandbox=args.claude_sandbox, deny_read=args.deny_read, agent_version=agent_version(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.verify_tasks:
        with tempfile.TemporaryDirectory(prefix="fugue-verify-") as directory:
            parent = Path(directory)
            (parent / "home").mkdir()
            result = verify_tasks(archive, task_set, names, chosen["arms"], parent, args.validation_timeout)
        result.update(source_commit=revision, source_repo=str(source), tasks_sha256=task_set["sha256"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if not args.model or not args.output:
        parser.error("--model and --output are required unless --verify-tasks is used")
    stale = stale_trial_dirs() if args.execute else []
    if stale:
        # 被强行中断的试验留下的工作区可能已经解完任务,新试验能用 ls .. 看到它们
        parser.error("leftover trial directories from an interrupted run are readable by new trials; inspect and "
                     "delete them first: " + " ".join(stale[:10]))
    if args.agent == "claude" and args.execute and not (
            os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        parser.error("Claude trials use a private config directory, so the keychain login is not visible: export "
                     "CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY")
    schedule = build_schedule(names, args.repeats, chosen["arms"], args.seed)
    arm_of = dict(chosen["arms"])
    claude = args.agent == "claude"
    report = {"schema": "geb.token-pilot.v2", "agent": args.agent,
              "agent_version": agent_version(args) if args.execute else None,
              "source_commit": revision, "source_repo": str(source),
              "external_source": ({k: task_set["source"].get(k) for k in ("name", "git", "commit", "subdir", "tree")}
                                  if task_set.get("source") and not args.source_repo else None),
              "source_index_files": index_files, "model": args.model, "effort": args.effort,
              "design": args.design, "arms": [list(a) for a in chosen["arms"]],
              "comparisons": [list(c) for c in chosen["comparisons"]],
              "prompt_style": args.prompt_style, "tasks_file": task_set["path"], "tasks_sha256": task_set["sha256"],
              "skill_commit": skill_rev, "self_hosted": self_hosted,
              "self_hosted_note": ("source is fugue-docs itself: SKILL.md and docs describing indexes stay in every "
                                   "workspace, and its own index self-check fails in the noindex arm; treat "
                                   "index_effect here as confounded") if self_hosted else None,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "session_persistence": "isolated_claude_config_dir" if claude else "isolated_codex_home",
              "claude_settings": claude_settings(args.claude_sandbox, args.deny_read) if claude else None,
              "claude_credential": sorted(claude_credentials()) if claude else None,
              "claude_isolation": ({"sandbox": args.claude_sandbox,
                                    "bash_cannot_read_answer_dirs": args.claude_sandbox == "on",
                                    "bash_sees_no_credentials": args.claude_sandbox == "on",
                                    "note": None if args.claude_sandbox == "on" else
                                    "weaker isolation: Bash can read earlier trials and hidden patches; "
                                    "treat results as weaker evidence"} if claude else None),
              "claude_env_names": (sorted(set(claude_environment(Path("/nonexistent"), args.claude_env))
                                          - set(CREDENTIAL_VARS)) if claude else None),
              "claude_limits": {"max_turns": args.max_turns, "max_budget_usd": args.max_budget_usd,
                                "extra_args": args.claude_arg} if claude else None,
              "fugue_arm_install": ("plugin via --plugin-dir: SKILL.md + hooks; identical prompt in every arm"
                                    if claude else "skill + AGENTS.md pointer; other arms told not to invoke it"),
              "seed": args.seed,
              "planned_blocks": len(schedule), "planned_trials": sum(len(b["order"]) for b in schedule),
              "schedule": schedule, "primary_metric": PRIMARY_METRIC, "cost_weights": weights,
              "usage_definition": ("uncached input = fresh input + cache writes; cached input = cache reads; "
                                   "totals from result.modelUsage (includes subagents)") if claude else
                                  "uncached input = input - cached input from turn.completed events",
              "scope": "warm existing indexes; tasks from the tasks file only",
              "initialization_cost": None, "initialization_note": "not measured; indexed arms start with existing indexes",
              "token_budget": args.max_total_tokens, "cost_budget_usd": args.max_total_cost_usd,
              "timeout_per_trial": args.timeout,
              "budget_note": ("budget checked after each complete block using complete usage; may overshoot "
                              "by one block. Incomplete usage stops immediately; partial usage is only a lower bound"),
              "quality_note": "same hidden acceptance and regression commands; not independent semantic review",
              "evidence_level": "test_passed_pairs_only_pending_independent_semantic_review" + (
                  "_weaker_isolation" if claude and args.claude_sandbox != "on" else ""),
              "trials": []}
    if not args.execute:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    skill_dir = output / "skill"
    skill_dir.mkdir()
    with tarfile.open(fileobj=io.BytesIO(skill)) as tar:
        extract(tar, skill_dir, checked_members(tar))
    if claude and not args.skip_claude_selftest:
        report["claude_selftest"] = claude_selftest(args, skill_dir, output / "selftest", archive,
                                                    task_set["tasks"][names[0]])
        print(json.dumps({"claude_selftest": report["claude_selftest"]["ok"],
                          "failed": report["claude_selftest"]["failed"],
                          "hint": report["claude_selftest"]["hint"]}), flush=True)
        if not report["claude_selftest"]["ok"]:
            report["stop_reason"] = "selftest_failed"
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            return 1
    report["source_archive_sha256"] = hashlib.sha256(archive).hexdigest()
    report["skill_sha256"] = hashlib.sha256(b"".join(p.relative_to(skill_dir).as_posix().encode() + b"\0" +
        p.read_bytes() for p in sorted(skill_dir.rglob("*")) if p.is_file())).hexdigest()
    stop, signature = None, None
    for block in schedule:
        for position, label in enumerate(block["order"]):
            result = trial(args, archive, skill_dir, task_set["tasks"][block["task"]], block["repeat"],
                           label, arm_of[label], output, "trial-%03d" % len(report["trials"]), position)
            current = environment_signature(result.get("claude_session"))
            if current is not None:
                if signature is None:
                    signature = report["claude_environment"] = current
                elif current != signature and result["status"] != "setup_failed":
                    # 托管设置、内置插件或 MCP 在实验中途变化:各组不再可比
                    result.update(status="setup_failed", setup_problem="environment_changed", accepted=False)
            report["trials"].append(result)
            report["summary"] = summarize_trials(report["trials"], chosen["comparisons"], weights)
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            usage = result.get("usage") or {}
            print(json.dumps({"trial_id": result["trial_id"], "status": result["status"],
                              "accepted": result["accepted"], PRIMARY_METRIC: usage.get(PRIMARY_METRIC),
                              "total_tokens": usage.get("total_tokens"), "cost_usd": usage.get("cost_usd")}),
                  flush=True)
            if result["status"] == "setup_failed":
                stop = "setup_failed:" + str(result.get("setup_problem"))
                break
            if result["status"] in ("infrastructure_error", "harness_failed"):
                # 接口拒绝、额度用尽、执行中断或执行器自身出错:后面的试验也会受影响,停下来人工检查
                stop = result["status"]
                break
            if result["usage"] is None:
                stop = "incomplete_usage" if result["partial_usage"] else "unknown_usage"
                break
            if args.max_total_cost_usd is not None and result["usage"].get("cost_usd") is None:
                stop = "cost_unknown"  # 设了美元预算却拿不到金额,预算就失效了
                break
        if stop:
            break
        if report["summary"]["budget_tokens_lower_bound"] >= args.max_total_tokens:
            stop = "token_budget"
            break
        if args.max_total_cost_usd is not None and report["summary"]["budget_cost_usd"] >= args.max_total_cost_usd:
            stop = "cost_budget"
            break
    report["stop_reason"] = stop or "schedule_complete"
    report["summary"] = summarize_trials(report["trials"], chosen["comparisons"], weights)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"stop_reason": report["stop_reason"], "summary": {
        k: v for k, v in report["summary"].items() if k != "comparisons"}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
