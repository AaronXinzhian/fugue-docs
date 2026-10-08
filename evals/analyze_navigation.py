#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, json, math, os, pathlib, re, shlex, statistics, sys
[OUTPUT]: 提供 Codex exec 事件流的离线定位成本分析、配对统计与 A/A 样本量提示;不调用模型
[POS]: fugue-docs 评测包-定位成本分析器(从已有试验日志衡量索引是否减少首次修改前的探索)
[PROTOCOL]: 改分类规则或指标时同步 token-pilot.md、evals/FOLDER_INDEX.md 与 test_navigation.py
"""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import statistics
import sys

INDEX_NAMES = {"PROJECT_INDEX.md", "FOLDER_INDEX.md"}
KIND_PRIORITY = ("edit", "test", "workflow", "search", "read", "list", "vcs", "other")
READ_COMMANDS = {"cat", "head", "tail", "nl", "less", "more", "bat", "sed", "awk", "view"}
SEARCH_COMMANDS = {"rg", "grep", "egrep", "fgrep", "ag", "ack"}
LIST_COMMANDS = {"ls", "tree", "find", "fd", "du", "wc", "stat", "file", "pwd"}
WRAPPERS = {"env", "time", "nice", "command", "stdbuf", "nohup"}
SHELLS = {"bash", "sh", "zsh", "dash"}
# 需要跳过取值的选项:否则 "head -n 50 a.py" 会把 50 当成文件
VALUE_OPTIONS = {
    "head": {"-n", "-c"}, "tail": {"-n", "-c"}, "sed": {"-e", "-f"}, "awk": {"-F", "-v", "-f"},
    "grep": {"-e", "-f", "-A", "-B", "-C", "-m", "--include", "--exclude"},
    "rg": {"-e", "-f", "-g", "--glob", "-t", "--type", "-T", "-A", "-B", "-C", "-m", "-M"},
    "nl": {"-b", "-w", "-s", "-v", "-i"},
}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)


def load_events(path):
    events = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def items_in_order(events):
    """按首次出现排序;完成事件覆盖开始事件,超时只开始的条目也保留。"""
    order, latest = [], {}
    for index, event in enumerate(events):
        if event.get("type") not in ("item.started", "item.updated", "item.completed"):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        key = item.get("id") or ("anonymous-%d" % index)
        if key not in latest:
            order.append(key)
        if event["type"] == "item.completed" or key not in latest:
            latest[key] = dict(item, _completed=event["type"] == "item.completed")
    return [latest[key] for key in order]


def item_type(item):
    return item.get("type") or item.get("item_type") or item.get("details", {}).get("type")


def strip_heredocs(text):
    lines, kept, delimiter = text.split("\n"), [], None
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        match = HEREDOC.search(line)
        if match:
            delimiter = match.group(2)
    return " ; ".join(part for part in kept if part.strip())


def tokenize(text):
    try:
        lexer = shlex.shlex(strip_heredocs(text.replace("\\\n", " ")), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer), False
    except ValueError:
        return text.split(), True


def shell_script(argv):
    argv = unwrap(argv)
    if argv and os.path.basename(argv[0]) in SHELLS:
        return next((argv[i + 1] for i, arg in enumerate(argv[:-1])
                     if arg.startswith("-") and "c" in arg.lstrip("-")), None)
    return None


def simple_commands(command):
    """把一条命令拆成简单命令,展开 bash -lc 包装;返回 (argv 列表, 是否解析降级)。"""
    argv = [str(part) for part in command] if isinstance(command, list) else None
    if argv is None:
        try:
            argv = shlex.split(str(command))
        except ValueError:
            argv = None
    # 外层包装先整体切分,脚本里的换行和 heredoc 交给内层处理
    script = shell_script(argv) if argv else None
    if script is not None:
        return simple_commands(script)
    if isinstance(command, list):
        command = " ".join(shlex.quote(str(part)) for part in command)
    tokens, degraded = tokenize(str(command))
    result, current = [], []
    for token in tokens + [";"]:
        if token in ("&&", "||", ";", "|", "&", "(", ")", ";;"):
            if current:
                result.append(current)
            current = []
            continue
        current.append(token)
    expanded = []
    for argv in result:
        argv = unwrap(argv)
        if not argv:
            continue
        name = os.path.basename(argv[0])
        if name in SHELLS:
            script = next((argv[i + 1] for i, arg in enumerate(argv[:-1])
                           if arg.startswith("-") and "c" in arg.lstrip("-")), None)
            if script is not None:
                inner, inner_degraded = simple_commands(script)
                expanded.extend(inner)
                degraded = degraded or inner_degraded
                continue
        expanded.append(argv)
    return expanded, degraded


def unwrap(argv):
    argv = list(argv)
    while argv:
        head = argv[0]
        if "=" in head and not head.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head):
            argv.pop(0)
        elif os.path.basename(head) in WRAPPERS:
            argv.pop(0)
            while argv and (argv[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0])):
                argv.pop(0)
        elif os.path.basename(head) == "timeout":
            argv = argv[2:] if len(argv) > 1 else []
        elif os.path.basename(head) == "xargs":
            argv.pop(0)
            while argv and argv[0].startswith("-"):
                argv.pop(0)
        else:
            break
    return argv


def split_redirects(argv):
    args, writes = [], []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in (">", ">>", "&>", ">|") and i + 1 < len(argv):
            if argv[i + 1] != "/dev/null" and not (args and args[-1].isdigit() and argv[i + 1].isdigit()):
                writes.append(argv[i + 1])
            if args and args[-1] in ("1", "2"):
                args.pop()
            i += 2
            continue
        if token in ("<", ">&", "<&", "<<", "<<-", "<<<") and i + 1 < len(argv):
            if args and args[-1] in ("1", "2"):
                args.pop()
            i += 2
            continue
        args.append(token)
        i += 1
    return args, writes


def positionals(name, args):
    values, skip, takes = [], False, VALUE_OPTIONS.get(name, set())
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == "--":
            continue
        if arg.startswith("-") and len(arg) > 1:
            if arg in takes:
                skip = True
            continue
        values.append(arg)
    return values


def looks_like_path(value):
    return bool(value) and not value.isdigit() and not re.match(r"^[0-9,]+[pd]?$", value) and (
        "/" in value or "." in value or value in ("Makefile", "Dockerfile"))


def classify(argv):
    """返回 (类别, 读取或搜索的路径, 写入的路径)。启发式,覆盖常见 shell 用法。"""
    args, writes = split_redirects(argv)
    if not args:
        return ("edit" if writes else "other"), [], writes
    name = os.path.basename(args[0])
    rest = args[1:]
    # 先判修改:读到或写到 test_*.py 不等于运行测试
    if name == "apply_patch" or name == "applypatch":
        return "edit", [], writes
    if name == "sed" and any(arg.startswith("-i") for arg in rest):
        return "edit", [], writes + [p for p in positionals(name, rest)[1:] if looks_like_path(p)]
    if name == "perl" and any(arg.startswith("-i") or arg.startswith("-pi") for arg in rest):
        return "edit", [], writes + [p for p in positionals(name, rest)[1:] if looks_like_path(p)]
    if name == "tee":
        return "edit", [], writes + positionals(name, rest)
    runs_python = re.match(r"^python[0-9.]*$", name) is not None
    if runs_python:
        module = rest[rest.index("-m") + 1] if "-m" in rest[:-1] else None
        script = None if module or "-c" in rest else next((a for a in rest if not a.startswith("-")), None)
    else:
        module, script = None, args[0]
    if script and re.search(r"(^|/)geb_[a-z_]+\.py$", script):
        return "workflow", [], writes
    if name in ("pytest", "py.test") or module in ("pytest", "unittest") or (
            script and re.search(r"(^|/)(test_[\w-]*|[\w-]*_test|run_regression_suite)\.py$", script)):
        return "test", [], writes
    if name in ("npm", "yarn", "pnpm", "go", "cargo", "make") and "test" in rest:
        return "test", [], writes
    if name == "git":
        if rest[:1] == ["grep"]:
            values = positionals("grep", rest[1:])
            return "search", [p for p in values[1:] if looks_like_path(p)], writes
        if rest[:1] == ["ls-files"]:
            return "list", [], writes
        if rest[:1] in (["show"], ["cat-file"]):
            return "read", [], writes
        return "vcs", [], writes
    if name in SEARCH_COMMANDS:
        values = positionals(name, rest)
        has_pattern_option = any(arg in ("-e", "-f") for arg in rest)
        paths = values if has_pattern_option else values[1:]
        return ("edit" if writes else "search"), [p for p in paths if looks_like_path(p)], writes
    if name in READ_COMMANDS:
        values = positionals(name, rest)
        if name in ("sed", "awk") and not any(arg in ("-e", "-f") for arg in rest):
            values = values[1:]
        kind = "edit" if writes and name != "sed" else "read"
        return kind, [p for p in values if looks_like_path(p)], writes
    if name in LIST_COMMANDS:
        return "list", [], writes
    if writes:
        return "edit", [], writes
    return "other", [], writes


def normalize_path(path, workspace_marker="/workspace/"):
    path = str(path)
    if workspace_marker in path:
        path = path.split(workspace_marker, 1)[1]
    path = os.path.normpath(path)
    return path[2:] if path.startswith("./") else path


SCRATCH_PREFIXES = ("/tmp", "/dev", "/private/tmp", "/var/folders", "/private/var/folders")
SYSTEM_PREFIXES = ("/usr", "/etc", "/opt", "/lib", "/bin", "/proc", "/sys", "/System", "/Library", "/nix")
PYTHON_WRITE = re.compile(r"""(?:Path\(\s*['"]([^'"]+)['"]\s*\)\s*\.write_(?:text|bytes)"""
                          r"""|open\(\s*['"]([^'"]+)['"]\s*,\s*['"][wax])""")
PYTHON_INLINE = re.compile(r"(^|[\s;&|(\"'])python[0-9.]*\s+(-\s*<<|-c\s)")


def is_scratch(path):
    # 先剥掉工作区前缀:试验目录本身常在 /tmp 下,工作区内的修改不能算临时写入。
    # 未展开的 shell 变量("$tmp"、$TMPDIR/x)无法确认落在工作区,不当作首次修改
    path = normalize_path(path)
    return path.startswith(SCRATCH_PREFIXES) or path.startswith("$")


def is_outside_workspace(path):
    """读取路径既不在工作区、也不是技能或系统目录时返回 True,用于发现跨试验读取。"""
    normalized = normalize_path(path)
    if is_skill_path(str(path)) or normalized.startswith("$"):
        return False
    if normalized == ".." or normalized.startswith("../"):
        return True
    if normalized.startswith("~"):
        return True
    return os.path.isabs(normalized) and not normalized.startswith(SCRATCH_PREFIXES + SYSTEM_PREFIXES)


def is_skill_path(path):
    return "/.agents/skills/" in path or "/skills/fugue-docs/" in path or os.path.basename(path) == "SKILL.md"


def empty_bucket():
    return {"commands": 0, "by_kind": {kind: 0 for kind in KIND_PRIORITY}, "output_chars": 0,
            "output_chars_by_kind": {kind: 0 for kind in KIND_PRIORITY}, "files_read": set(),
            "index_reads": 0, "skill_reads": 0, "workflow_runs": 0, "file_changes": 0,
            "outside_workspace_reads": set()}


def analyze_events(events):
    """定位成本:首次修改前的命令、读取和工具输出,以及全程同口径数值。"""
    items = items_in_order(events)
    schema = "type" if any(item.get("type") for item in items) else ("item_type" if items else None)
    total, before = empty_bucket(), empty_bucket()
    first_edit, degraded_commands, reasoning, messages, other_items, incomplete = None, 0, 0, 0, 0, 0
    for position, item in enumerate(items):
        kind_name = item_type(item)
        if not item.get("_completed"):
            incomplete += 1
        if kind_name == "reasoning":
            reasoning += 1
            continue
        if kind_name == "agent_message":
            messages += 1
            continue
        if kind_name == "file_change":
            changes = [str(c.get("path")) for c in item.get("changes") or []
                       if isinstance(c, dict) and c.get("path")]
            total["file_changes"] += 1
            if first_edit is None and not (changes and all(is_scratch(p) for p in changes)):
                first_edit = position
            continue
        if kind_name != "command_execution":
            other_items += 1
            continue
        raw = str(item.get("command") or "")
        commands, degraded = simple_commands(item.get("command") or "")
        degraded_commands += int(degraded)
        kinds, reads, writes = [], [], []
        for argv in commands:
            kind, targets, written = classify(argv)
            kinds.append(kind)
            if kind in ("read", "search"):
                reads.extend(targets)
            writes.extend(written)
        for path in PATCH_PATH.findall(raw):
            writes.append(path.strip())
            kinds.append("edit")
        if PYTHON_INLINE.search(raw) and (PYTHON_WRITE.search(raw) or ".write(" in raw):
            targets = [a or b for a, b in PYTHON_WRITE.findall(raw)]
            writes.extend(targets)
            # 写入目标写在变量里时无法确认位置,保守地不当作首次修改
            kinds.append("edit" if targets else "other")
        if "edit" in kinds and writes and all(is_scratch(w) for w in writes):
            kinds = [k for k in kinds if k != "edit"] or ["other"]
        primary = next((k for k in KIND_PRIORITY if k in kinds), "other")
        if primary == "edit" and first_edit is None:
            first_edit = position
        # 首次修改本身不计入"修改前"区间
        buckets = [total] if first_edit is not None else [total, before]
        output = item.get("aggregated_output") or ""
        normalized = [normalize_path(p) for p in reads]
        for bucket in buckets:
            bucket["commands"] += 1
            bucket["by_kind"][primary] += 1
            bucket["output_chars"] += len(output)
            bucket["output_chars_by_kind"][primary] += len(output)
            bucket["files_read"].update(normalized)
            bucket["index_reads"] += sum(os.path.basename(p) in INDEX_NAMES for p in normalized)
            bucket["skill_reads"] += sum(is_skill_path(p) for p in reads)
            bucket["workflow_runs"] += int("workflow" in kinds)
            bucket["outside_workspace_reads"].update(p for p in reads if is_outside_workspace(p))
    def export(bucket):
        result = dict(bucket)
        files = sorted(result.pop("files_read"))
        result["files_read"] = len(files)
        result["files_read_list"] = files[:80]
        outside = sorted(result.pop("outside_workspace_reads"))
        result["outside_workspace_reads"] = len(outside)
        result["outside_workspace_read_list"] = outside[:40]
        return result
    return {"schema": "geb.navigation.v1", "item_schema": schema, "items": len(items),
            "incomplete_items": incomplete, "reasoning_items": reasoning, "agent_messages": messages,
            "other_items": other_items, "degraded_command_parses": degraded_commands,
            "first_edit_item": first_edit, "first_edit_found": first_edit is not None,
            "before_first_edit": export(before) if first_edit is not None else None,
            "total": export(total),
            "note": "heuristic shell classification; output_chars is tool output before model-side truncation"}


def nav_value(navigation, scope, field):
    if not isinstance(navigation, dict):
        return None
    bucket = navigation.get(scope)
    if not isinstance(bucket, dict):
        return None
    value = bucket.get(field)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


NAV_METRICS = {
    "nav_commands_before_edit": lambda nav: nav_value(nav, "before_first_edit", "commands"),
    "nav_output_chars_before_edit": lambda nav: nav_value(nav, "before_first_edit", "output_chars"),
    "nav_files_read_before_edit": lambda nav: nav_value(nav, "before_first_edit", "files_read"),
    "nav_commands_total": lambda nav: nav_value(nav, "total", "commands"),
    "nav_output_chars_total": lambda nav: nav_value(nav, "total", "output_chars"),
}


def paired_stats(pairs):
    """pairs 为 (对照值, 处理值);差值 = 对照 - 处理,正数表示处理组更少。

    相对效应用对数比 log(对照/处理):(a-b)/a 的均值在两组同分布时也偏负(Jensen),
    不能用来估噪声或功效。几何节省率 = 1 - exp(-均值对数比)。
    """
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return {"n": 0}
    diffs = [a - b for a, b in pairs]
    rates = [(a - b) / a for a, b in pairs if a]
    logs = [math.log(a / b) for a, b in pairs if a > 0 and b > 0]
    mean_log = statistics.mean(logs) if logs else None
    return {"n": len(pairs),
            "reference_median": statistics.median(a for a, _ in pairs),
            "treatment_median": statistics.median(b for _, b in pairs),
            "sum_difference": sum(diffs),
            "mean_difference": statistics.mean(diffs),
            "median_difference": statistics.median(diffs),
            "sd_difference": statistics.stdev(diffs) if len(diffs) > 1 else None,
            "n_ratio": len(logs),
            "mean_log_ratio": mean_log,
            "sd_log_ratio": statistics.stdev(logs) if len(logs) > 1 else None,
            "geometric_saving_rate": 1 - math.exp(-mean_log) if mean_log is not None else None,
            "median_saving_rate": statistics.median(rates) if rates else None,
            "saving_rate_range": [min(rates), max(rates)] if rates else None,
            "treatment_lower": sum(d > 0 for d in diffs),
            "treatment_higher": sum(d < 0 for d in diffs),
            "ties": sum(d == 0 for d in diffs)}


def power_hint(sd_log_ratio, effects=(0.1, 0.2, 0.3)):
    """A/A 对数比标准差 → 检出给定相对节省所需配对数(双侧 5%,80% 功效,正态近似)。"""
    if not sd_log_ratio:
        return None
    z = 1.959964 + 0.841621
    return {"assumption": "normal approximation on log(reference/treatment); assumes A/A noise carries over",
            "aa_sd_log_ratio": sd_log_ratio,
            "pairs_needed": {"%.0f%%" % (e * 100): max(2, int(math.ceil((z * sd_log_ratio / -math.log(1 - e)) ** 2)))
                             for e in effects}}


def sign_test(better, worse):
    """双侧精确符号检验;持平不计入。"""
    n = better + worse
    if n == 0:
        return None
    k = min(better, worse)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def infer_comparisons(labels):
    labels = sorted(labels)
    if {"baseline", "fugue"} <= set(labels):
        return [("baseline", "fugue", "workflow_effect")]
    return [(a, b, "%s_vs_%s" % (a, b)) for i, a in enumerate(labels) for b in labels[i + 1:]]


def trial_directory(output, trial):
    name = trial.get("trial_dir") or trial.get("trial_id") or "%s-%d-%s" % (
        trial["task"], trial["repeat"], trial["condition"])
    return Path(output) / name


def analyze_output(output, include_failed=False):
    output = Path(output)
    report_path = output / "report.json"
    if not report_path.is_file():
        trials = []
        for events in sorted(output.glob("*/events.jsonl")):
            trials.append({"trial_id": events.parent.name, "navigation": analyze_events(load_events(events))})
        return {"schema": "geb.navigation-report.v1", "source": str(output), "trials": trials,
                "comparisons": None, "note": "no report.json; trials are unlabeled"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = []
    for trial in report.get("trials", []):
        events = trial_directory(output, trial) / "events.jsonl"
        navigation = analyze_events(load_events(events)) if events.is_file() else None
        rows.append({"task": trial.get("task"), "repeat": trial.get("repeat"),
                     "condition": trial.get("condition"), "accepted": trial.get("accepted"),
                     "navigation": navigation})
    comparisons = report.get("comparisons") or infer_comparisons({r["condition"] for r in rows})
    eligible = [r for r in rows if r["navigation"] and (include_failed or r.get("accepted") is not False)]
    blocks = {}
    for row in eligible:
        blocks.setdefault((row["task"], row["repeat"]), {})[row["condition"]] = row
    result = {}
    for reference, treatment, name in comparisons:
        result[name] = {"reference": reference, "treatment": treatment, "metrics": {
            metric: paired_stats([(fn(b[reference]["navigation"]), fn(b[treatment]["navigation"]))
                                  for b in blocks.values() if reference in b and treatment in b])
            for metric, fn in NAV_METRICS.items()}}
    by_condition = {}
    for row in eligible:
        by_condition.setdefault(row["condition"], []).append(row["navigation"])
    medians = {condition: {"trials": len(navs), **{
        metric: (statistics.median(values) if values else None)
        for metric, values in ((m, [v for v in (fn(n) for n in navs) if v is not None])
                               for m, fn in NAV_METRICS.items())}}
        for condition, navs in by_condition.items()}
    return {"schema": "geb.navigation-report.v1", "source": str(output),
            "eligible_trials": len(eligible), "missing_event_logs": sum(r["navigation"] is None for r in rows),
            "by_condition_median": medians, "comparisons": result,
            "trials": rows,
            "note": "navigation proxies are not token measurements; pairs are within the same task/repeat block"}


def main():
    parser = argparse.ArgumentParser(description="Offline navigation-cost analysis of Codex exec --json logs; no model calls")
    parser.add_argument("paths", nargs="+", help="Pilot output directories (with report.json) or events.jsonl files")
    parser.add_argument("--include-failed", action="store_true", help="Also pair trials that failed acceptance")
    parser.add_argument("--output", help="Write JSON here instead of stdout")
    args = parser.parse_args()
    results = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            results.append(analyze_output(path, args.include_failed))
        elif path.is_file():
            results.append({"source": str(path), "navigation": analyze_events(load_events(path))})
        else:
            parser.error("not found: " + raw)
    text = json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
