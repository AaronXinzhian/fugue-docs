#!/usr/bin/env python3
"""
[INPUT]: 依赖 difflib, hashlib, json, os, re, subprocess, sys, time, uuid, geb_check, geb_scaffold
[OUTPUT]: 提供 Claude Code 钩子入口 session-start / prompt / pre-tool / post-tool / stop / session-end:按工具调用归属本会话改动、自动同步机器字段、只把语义缺口回灌模型、从对话记录程序化计量
[POS]: fugue-docs 工具层-程序化回环:机器能推导的由程序完成,模型只在语义环节参与
[PROTOCOL]: 变更时更新此头部,然后检查 scripts/FOLDER_INDEX.md、hooks/hooks.json、SKILL.md 与 README 的钩子说明

钩子绝不能卡住正常工作:任何异常都静默放行(exit 0),错误写入数据目录的 hook-errors.log。

改动归属按工具调用记录,而不是从工作区推断:
- 模型用 Edit/Write/MultiEdit/NotebookEdit 前(pre-tool),记下目标文件和改前的指纹;
- 模型运行 Bash 前后(pre-tool / post-tool)各做一次未提交改动的快照,命令改了哪些代码文件就记哪些;
  切分支、拉取、合并、暂存恢复、重置等改写历史或工作区的 git 命令带来的文件不记。
用户自己改的文件、另一个会话写的文件、拉取带来的文件都不会被当成本会话的改动。已提交、
改回原样或在两轮之间被用户再改过的文件会从记录中移除。只对“本会话写过、仍有净改动、
仍未提交”的代码文件做维护;合并或变基进行中不写任何文件;冲突、语法错误、非 UTF-8、
生成代码和指向项目外的链接逐个跳过。

- stop:新文件补 L3 头骨架,依赖与清单由 geb_sync 增量同步,只改函数体时完全静默;
  语义缺口(新文件 [POS]、清单职责、导出变化后的 [OUTPUT]、新目录定位)以简短理由阻止
  一次收工,同一缺口证据不变时只提示一次。
- session-start 只注入一行导航提示;prompt、pre-tool、post-tool 不输出任何内容。
- session-end 只补记计量,对话记录在会话结束时才完整落盘。
"""

import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geb_check import (EXCLUDED_DIRS, L1_NAMES, L2_NAMES, L3_SCAN_LINES, L3_TAGS,  # noqa: E402
                       TODO_MARKERS, check_l3, find_index_file, is_code_file,
                       is_small_project, walk_project)
from geb_scaffold import (analysis_ok, analyze_file_checked, analyze_text,  # noqa: E402
                          has_module_docstring, header_lines, insert_header, render_header, render_l2)

USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
MAX_GAPS = 8
STATE_TTL_SECONDS = 30 * 24 * 3600
FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
GIT_WORKTREE = re.compile(r"\bgit\b[^;&|\n]*?\b(stash|checkout|switch|pull|merge|rebase|reset|restore|"
                          r"cherry-pick|revert|am|apply|clone|worktree)\b")
GENERATED = re.compile(r"@generated|DO NOT EDIT")  # 只看前 5 行的惯用标记,大小写敏感
TODO_DUTY = "TODO(" + "语义):职责"
RENAME_SIMILARITY = 0.6
BULK_LIMIT = 20  # 一条命令新增超过这么多代码文件,视为导入第三方代码或脚手架生成,不归入本会话
NAVIGATION_HINT = ("本项目使用赋格索引:先读 PROJECT_INDEX.md,再读目标目录 FOLDER_INDEX.md 和文件头定位,"
                   "然后读代码。索引的依赖、清单、检查与计量由钩子自动完成,不要手动运行 geb_sync、"
                   "geb_check 或 geb_metrics;收到赋格补写提示时,只补所列语义字段。")


# ---------------- 通用 ----------------

def data_dir():
    # 固定位置便于用户查账,插件升级或卸载也不会带走账本
    return os.environ.get("FUGUE_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "fugue")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def safe_id(value):
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or ""))[:128]
    return value.strip(".") or None


def session_file(session_id, suffix):
    return os.path.join(data_dir(), "sessions", session_id + suffix)


def write_json(path, data):
    """原子写入;ASCII 编码,任何路径或内容都能保存。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.%s.tmp" % (path, uuid.uuid4().hex)
    try:
        with open(temporary, "w", encoding="ascii") as f:
            f.write(json.dumps(data, ensure_ascii=True, indent=1) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def log_error(where, error):
    try:
        os.makedirs(data_dir(), exist_ok=True)
        with open(os.path.join(data_dir(), "hook-errors.log"), "a", encoding="utf-8") as f:
            f.write("%s %s %s: %s\n" % (now(), where, type(error).__name__, error))
    except Exception:  # noqa: BLE001
        pass


def norm(path):
    return os.path.normpath(path).replace(os.sep, "/")


def rel_dir(rel):
    return os.path.dirname(rel) or "."


def git(root, *args):
    """文件内容按 UTF-8 替换解码,只用于分析,不写回。"""
    try:
        run = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return run.stdout.decode("utf-8", "replace") if run.returncode == 0 else None


def project_root(payload):
    """会话目录;若它没有 L1 而所在 git 仓库根目录有,就用仓库根目录。规范化真实路径。"""
    base = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    if find_index_file(base, L1_NAMES):
        return base
    top = (git(base, "rev-parse", "--show-toplevel") or "").strip()
    if top and find_index_file(os.path.realpath(top), L1_NAMES):
        return os.path.realpath(top)
    return base


def relevant_code(rel):
    """项目内、未被排除、属于代码的文件才参与维护。"""
    parts = rel.split("/")
    if ".." in parts or any(p in EXCLUDED_DIRS or p.startswith(".") for p in parts[:-1]):
        return False
    return is_code_file(rel)


def inside(root, rel):
    """不跟随符号链接:路径本身或任一上级是链接、或真实路径不在项目内,都不写。"""
    current = root
    for part in rel.split("/"):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return False
    real = os.path.realpath(os.path.join(root, rel))
    return real.startswith(root.rstrip(os.sep) + os.sep)


def read_strict(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def head_of(text):
    return "".join(text.splitlines(True)[:L3_SCAN_LINES]) if text else ""


def tag_line(head, tag):
    return next((line.strip() for line in head.splitlines() if tag in line), None)


def tag_lines(head):
    return [line.strip() for line in head.splitlines() if any(t in line for t in L3_TAGS + ("[PROTOCOL]",))]


def is_generated(text):
    return bool(text) and bool(GENERATED.search("".join(text.splitlines(True)[:5])))


def view_of(path):
    """文件当前的导出与 [OUTPUT] 行;解析不可靠时导出记为 None,不做导出比较。"""
    _inputs, outputs, ok = analyze_file_checked(path)
    return {"exports": outputs if ok else None, "output_line": tag_line(head_of(read_strict(path)), "[OUTPUT]")}


def is_git(root):
    return (git(root, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"


def current_head(root):
    return (git(root, "rev-parse", "--verify", "-q", "HEAD") or "").strip() or None


def git_busy(root):
    """合并、变基、拣选或回退进行中:工作区处于中间状态,本轮不写。"""
    git_dir = (git(root, "rev-parse", "--absolute-git-dir") or "").strip()
    return bool(git_dir) and any(os.path.exists(os.path.join(git_dir, name)) for name in
                                 ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"))


def split_z(text):
    # 含无法解码字符的路径无法安全写回,直接跳过
    return [norm(part) for part in (text or "").split("\0") if part and "�" not in part]


def dirty_names(root, head):
    """相对 HEAD 有改动(含删除)或未跟踪的代码文件;git 出错返回 None。"""
    tracked = (git(root, "diff", "--no-renames", "--relative", "--name-only", "-z", "HEAD") if head
               else git(root, "ls-files", "-z", "--cached"))
    untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return {rel for rel in split_z(tracked) + split_z(untracked) if relevant_code(rel)}


def all_code_files(root):
    """非 git 项目的全量代码文件(含子项目),排除规则与检查器一致。"""
    found = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
        for name in filenames:
            rel = norm(os.path.relpath(os.path.join(dirpath, name), root))
            try:
                rel.encode("utf-8")
            except UnicodeEncodeError:
                continue  # 无法用 UTF-8 表示的文件名,不参与维护
            if relevant_code(rel):
                found.add(rel)
    return found


def fingerprint(root, rel, cache=None):
    path = os.path.join(root, rel)
    try:
        stat = os.stat(path)
    except OSError:
        return {"sha": None, "exists": False}
    key = [stat.st_mtime_ns, stat.st_size]
    previous = (cache or {}).get(rel)
    if previous and previous.get("key") == key:
        return previous  # 内容未动,复用上次的指纹与导出
    item = {"sha": sha(path), "key": key, "exists": True}
    item.update(view_of(path))
    return item


def tracked_view(root, head, rel):
    """文件在某个提交里的导出与 [OUTPUT] 行;不存在返回 None。"""
    text = git(root, "show", "%s:./%s" % (head, rel)) if head else None
    if text is None:
        return None
    ext = os.path.splitext(rel)[1]
    return {"exports": analyze_text(text, ext)[1] if analysis_ok(text, ext) else None,
            "output_line": tag_line(head_of(text), "[OUTPUT]"), "text": text}


# ---------------- 工具调用记录 ----------------

def tool_rel(root, payload, path):
    """把工具参数里的路径换成项目内相对路径;不在项目内返回 None。不解析文件本身的链接。"""
    if not path:
        return None
    base = payload.get("cwd") or root
    full = os.path.normpath(os.path.join(base, os.path.expanduser(str(path))))
    full = os.path.join(os.path.realpath(os.path.dirname(full)), os.path.basename(full))
    rel = norm(os.path.relpath(full, root))
    return None if rel == "." or rel.startswith("../") else rel


def append_log(session_id, records, root=None):
    if not records:
        return
    if root:
        for record in records:
            record["abs"] = os.path.join(root, record["rel"])
    path = session_file(session_id, ".log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="ascii") as f:
        f.write("".join(json.dumps(r, ensure_ascii=True) + "\n" for r in records))


def pre_record(root, rel, cache=None):
    item = fingerprint(root, rel, cache)
    return {"rel": rel, "pre": {k: item.get(k) for k in ("sha", "exists", "exports", "output_line")}}


def bash_key(session_id, payload):
    raw = payload.get("tool_use_id") or json.dumps(payload.get("tool_input"), sort_keys=True)
    return session_file(session_id, ".bash-" + hashlib.sha256(str(raw).encode()).hexdigest()[:24] + ".json")


def bash_snapshot(root, session_id):
    if not is_git(root):
        return {"kind": "mtime", "since": time.time(), "files": sorted(all_code_files(root))}
    head = current_head(root)
    names = dirty_names(root, head)
    if names is None:
        return None
    cache_path = session_file(session_id, ".fingerprints.json")
    cache = read_json(cache_path) or {}
    dirty = {rel: fingerprint(root, rel, cache) for rel in sorted(names)}
    write_json(cache_path, dirty)
    return {"kind": "git", "head": head, "dirty": dirty}


def bash_changes(root, snap):
    """命令前后的差异:返回日志记录列表。"""
    records = []
    if snap["kind"] == "mtime":
        before = set(snap["files"])
        current = all_code_files(root)
        for rel in sorted(current):
            if rel not in before:
                records.append({"rel": rel, "pre": {"sha": None, "exists": False}})
            else:
                try:
                    if os.path.getmtime(os.path.join(root, rel)) >= snap["since"]:
                        records.append({"rel": rel, "pre": {"sha": None, "exists": True}})
                except OSError:
                    continue
        records += [{"rel": rel, "pre": {"sha": None, "exists": True}} for rel in sorted(before - current)]
        return records
    head = current_head(root)
    names = dirty_names(root, head)
    if names is None:
        return records
    for rel in sorted(names | set(snap["dirty"])):
        previous = snap["dirty"].get(rel)
        current = sha(os.path.join(root, rel))
        if previous is not None:
            if previous.get("sha") == current:
                continue
            pre = {k: previous.get(k) for k in ("sha", "exists", "exports", "output_line")}
        elif rel in names:
            base = tracked_view(root, snap["head"], rel)
            pre = {"sha": None, "exists": base is not None, "tracked_clean": base is not None,
                   "exports": (base or {}).get("exports"), "output_line": (base or {}).get("output_line")}
        else:
            continue
        records.append({"rel": rel, "pre": pre})
    return records


def others_files(session_id):
    """其他会话近期记录过的文件(绝对路径):命令运行期间它们写的文件不算本会话的。"""
    directory, found = os.path.join(data_dir(), "sessions"), set()
    cutoff = time.time() - 24 * 3600
    try:
        names = os.listdir(directory)
    except OSError:
        return found
    for name in names:
        path = os.path.join(directory, name)
        if name.startswith(session_id + ".") or os.path.getmtime(path) < cutoff:
            continue
        if ".log" in name:
            try:
                with open(path, encoding="ascii") as f:
                    found.update(json.loads(line).get("abs") for line in f if line.strip())
            except (OSError, ValueError):
                continue
        elif name.endswith(".json") and ".bash-" not in name and ".fingerprints" not in name:
            state = read_json(path) or {}
            found.update(os.path.join(state.get("root", ""), rel) for rel in state.get("authored", {}))
    found.discard(None)
    return found


def bash_records(session_id, root, snap):
    """命令前后的改动,去掉其他会话写的文件;一次新增过多代码文件视为导入,整体不归入。"""
    records = bash_changes(root, snap)
    created = [r for r in records if not (r.get("pre") or {}).get("exists")]
    if len(created) > BULK_LIMIT:
        records = [r for r in records if (r.get("pre") or {}).get("exists")]
    others = others_files(session_id)
    return [r for r in records if os.path.join(root, r["rel"]) not in others]


def flush_bash(session_id, root, path):
    saved = read_json(path)
    try:
        os.remove(path)
    except OSError:
        pass
    if saved and saved.get("snapshot"):
        append_log(session_id, bash_records(session_id, root, saved["snapshot"]), root)


def flush_leftover_bash(session_id, root):
    """失败、被中断或转入后台的命令没有 PostToolUse:收工时按快照补记。"""
    directory = os.path.join(data_dir(), "sessions")
    try:
        names = sorted(n for n in os.listdir(directory) if n.startswith(session_id + ".bash-"))
    except OSError:
        return
    for name in names:
        flush_bash(session_id, root, os.path.join(directory, name))


def merge_log(state):
    """把本轮工具记录并入会话状态;同一文件保留第一次写入前的视图。"""
    path = session_file(state["session_id"], ".log")
    claimed = "%s.%s.merging" % (path, uuid.uuid4().hex)
    try:
        os.replace(path, claimed)  # 先改名再读,读取期间新追加的记录写进新日志,不会丢
        with open(claimed, encoding="ascii") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    authored = state.setdefault("authored", {})
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        rel, pre = record.get("rel"), record.get("pre") or {}
        if not rel or rel in authored:
            continue
        authored[rel] = {"status": "M" if pre.get("exists") else "A",
                         "view": ({"exports": pre["exports"], "output_line": pre.get("output_line")}
                                  if pre.get("exports") is not None else None),
                         "pre_sha": pre.get("sha"), "tracked_clean": bool(pre.get("tracked_clean"))}
    state["authored"] = authored
    write_json(session_file(state["session_id"], ".json"), state)  # 先存再清日志,出错也不丢记录
    os.remove(claimed)


def drop_touched_by_others(root, state):
    """两轮之间文件又被别人改过(用户、编辑器或其他会话),就不再算本会话的。"""
    for rel, entry in list(state.get("authored", {}).items()):
        if entry.get("seen_sha") and sha(os.path.join(root, rel)) != entry["seen_sha"]:
            del state["authored"][rel]


# ---------------- 索引归属 ----------------

def governing_root(root, rel):
    """离文件最近的含 PROJECT_INDEX.md 的目录(子项目),否则项目根。"""
    parts = rel.split("/")[:-1]
    for depth in range(len(parts), 0, -1):
        if os.path.isfile(os.path.join(root, *parts[:depth], "PROJECT_INDEX.md")):
            return "/".join(parts[:depth])
    return ""


class Projects:
    """按子项目缓存目录结构与规模判定;口径与 geb_check 一致(含只有索引的目录)。"""

    def __init__(self, root):
        self.root, self.cache = root, {}

    def info(self, rel):
        sub = governing_root(self.root, rel)
        if sub not in self.cache:
            path = os.path.join(self.root, sub) if sub else self.root
            dir_map = walk_project(path, include_indexes=True)
            self.cache[sub] = (path, dir_map, is_small_project(dir_map))
        path, dir_map, small = self.cache[sub]
        local = norm(os.path.relpath(os.path.join(self.root, rel), path))
        return sub, path, dir_map, small, local


def project_sizes(root):
    """会话开始时各(子)项目是否属于小项目,用来识别“本会话越过了阈值”。"""
    sizes, queue = {}, [""]
    while queue:
        sub = queue.pop()
        path = os.path.join(root, sub) if sub else root
        nested = []
        sizes[sub] = is_small_project(walk_project(path, nested, include_indexes=True))
        queue += [norm(os.path.join(sub, n)) if sub else norm(n) for n in nested]
    return sizes


def index_for(project, local_dir, small):
    """返回 (索引绝对路径, 表中文件名写法)。规则与 geb_check 一致。"""
    if local_dir == ".":
        return find_index_file(project, L1_NAMES), "name"
    own = find_index_file(os.path.join(project, local_dir), L2_NAMES)
    if own:
        return own, "name"
    if small:
        return find_index_file(project, L1_NAMES), "path"
    return None, None


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table_rows(index_path):
    text = read_strict(index_path) if index_path else None
    rows = {}
    for line in (text or "").splitlines():
        if line.lstrip().startswith("|"):
            cells = split_row(line)
            if cells and cells[0]:
                import geb_sync
                rows.setdefault(geb_sync.row_path(cells[0]), line)
    return rows


def ledger_duties(index_path):
    """只从“## 文件清单”表、按表头的“职责”列读取人写的职责。"""
    import geb_sync
    text = read_strict(index_path) if index_path else None
    duties, column, in_table = {}, None, False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_table, column = stripped == "## 文件清单", None
            continue
        if not in_table or not stripped.startswith("|"):
            continue
        cells = split_row(line)
        if column is None:
            column = next((i for i, c in enumerate(cells) if "职责" in c), -1)
            continue
        if column < 0 or set("".join(cells)) <= set("-: "):
            continue
        if column < len(cells) and cells[column] and not any(m in cells[column] for m in TODO_MARKERS):
            duties[geb_sync.row_path(cells[0])] = cells[column]
    return duties


def set_duty(index_path, cell, duty):
    """只替换仍是占位的职责格,不覆盖人写的内容。"""
    import geb_sync
    text = read_strict(index_path)
    if text is None:
        return False
    lines, changed = text.splitlines(True), False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|"):
            cells = split_row(line)
            if cells and geb_sync.row_path(cells[0]) == cell and len(cells) > 1 and TODO_DUTY in cells[1]:
                ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
                cells[1] = duty
                lines[i] = "| " + " | ".join(cells) + " |" + ending
                changed = True
    if changed:
        with open(index_path, "w", encoding="utf-8", newline="") as f:
            f.write("".join(lines))
    return changed


# ---------------- 维护 ----------------

def pending_entries(root, state, git_mode):
    """本会话写过、仍有净改动、仍未提交的代码文件;已提交或改回原样的从记录中移除。"""
    authored = state.get("authored", {})
    names = dirty_names(root, current_head(root)) if git_mode else None
    if git_mode and names is None:
        return None
    pending = {}
    for rel, entry in list(authored.items()):
        path = os.path.join(root, rel)
        exists = os.path.isfile(path)
        if not inside(root, rel) or (git_mode and rel not in names) or (not exists and entry["status"] == "A"):
            del authored[rel]
            continue
        if exists and entry.get("pre_sha") and sha(path) == entry["pre_sha"]:
            continue  # 写过但内容回到写之前的样子
        pending[rel] = entry
    return pending


def renamed_from(root, head, gone, new_text):
    """删除的旧文件与新文件内容足够相似时才视为改名。"""
    old = tracked_view(root, head, gone) if head else None
    old_text = (old or {}).get("text")
    if not old_text or not new_text or max(len(old_text), len(new_text)) > 200000:
        return False
    return difflib.SequenceMatcher(None, old_text, new_text).ratio() >= RENAME_SIMILARITY


def maintain(root, state, pending):
    """同步机器字段并收集语义缺口。返回 (缺口, 写入, 跳过的文件)。"""
    import geb_sync
    texts = {rel: read_strict(os.path.join(root, rel)) for rel in pending
             if os.path.isfile(os.path.join(root, rel))}
    # 非 UTF-8、生成代码、冲突或语法错误的文件逐个跳过;清单里保留它们原有的机器字段
    skipped = sorted(rel for rel, text in texts.items()
                     if text is None or is_generated(text) or not analysis_ok(text, os.path.splitext(rel)[1]))
    existing = set(texts) - set(skipped)
    added = {rel for rel in existing if pending[rel]["status"] == "A"}
    deleted = {rel for rel in pending if rel not in texts}
    projects = Projects(root)
    writes, gaps, extra_dirs = [], [], set()

    # 新目录:所属子项目中该目录的代码文件全是本会话新增;新顶层目录同理
    new_dirs, new_tops = set(), set()
    for rel in added:
        sub, _path, dir_map, _small, local = projects.info(rel)
        prefix = (sub + "/") if sub else ""
        local_dir = rel_dir(local)
        if local_dir != "." and all(norm(prefix + os.path.join(local_dir, f)) in added
                                    for f in dir_map.get(local_dir, [])):
            new_dirs.add(rel_dir(rel))
        top = local.split("/")[0]
        if "/" in local and all(norm(prefix + os.path.join(d, f)) in added
                                for d, files in dir_map.items()
                                if d == top or d.startswith(top + "/") for f in files):
            new_tops.add((sub, top))

    # 1) 新文件补 L3 头骨架(已有部分头部的不动,交给模型补全)
    for rel in sorted(added):
        path = os.path.join(root, rel)
        if len(check_l3(path)) == len(L3_TAGS):
            inputs, outputs, _ok = analyze_file_checked(path)
            ext = os.path.splitext(rel)[1].lower()
            text = render_header(ext, header_lines(inputs, outputs),
                                 has_module_docstring(path) if ext == ".py" else False)
            if insert_header(path, text):
                writes.append("[L3] " + rel)

    # 2) 需要 L2 的目录:新目录;或本会话让项目越过小项目阈值时,职责还写在 L1 里的全部目录
    l2_dirs, seen = {}, set()
    sizes = state.get("small_at_start", {})
    for rel in sorted(existing | deleted):
        sub, project, dir_map, small, _local = projects.info(rel)
        if small or sub in seen:
            continue
        seen.add(sub)
        crossed = sizes.get(sub) is True
        duties = ledger_duties(find_index_file(project, L1_NAMES)) if crossed else {}
        prefix = (sub + "/") if sub else ""
        for local_dir, files in dir_map.items():
            if local_dir == "." or not files or find_index_file(os.path.join(project, local_dir), L2_NAMES):
                continue
            carried = {f: duties.get(norm(os.path.join(local_dir, f))) for f in files}
            d = norm(prefix + local_dir)
            if d in new_dirs or (crossed and any(carried.values())):
                l2_dirs[d] = (project, local_dir, sorted(files), carried)
                extra_dirs.add(sub or ".")
    for d, (project, local_dir, files, carried) in sorted(l2_dirs.items()):
        analyses = {f: analyze_file_checked(os.path.join(project, local_dir, f))[:2] for f in files}
        path = os.path.join(project, local_dir, "FOLDER_INDEX.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render_l2(local_dir, files, analyses, project, os.path.join(project, local_dir)))
        for f, duty in carried.items():
            if duty:
                set_duty(path, f, duty)
        writes.append("[L2] " + norm(os.path.join(d, "FOLDER_INDEX.md")))
    if l2_dirs:
        projects.cache.clear()

    # 3) 同目录一删一增、内容相似时视为改名,把旧行的职责带到新行
    renames = []
    head = current_head(root)
    for d in {rel_dir(rel) for rel in deleted}:
        gone = [rel for rel in deleted if rel_dir(rel) == d]
        new = [rel for rel in added if rel_dir(rel) == d]
        if len(gone) == 1 and len(new) == 1 and renamed_from(root, head, gone[0], texts.get(new[0])):
            _sub, project, _map, small, local = projects.info(new[0])
            index, style = index_for(project, rel_dir(local), small)
            old_local = norm(os.path.join(rel_dir(local), os.path.basename(gone[0])))
            old_cell = os.path.basename(gone[0]) if style == "name" else old_local
            new_cell = os.path.basename(local) if style == "name" else local
            row = table_rows(index).get(old_cell) if index else None
            cells = split_row(row) if row else []
            if len(cells) > 1 and cells[1] and not any(m in cells[1] for m in TODO_MARKERS):
                renames.append((index, new_cell, cells[1]))

    # 4) 机器字段增量同步:[INPUT] 与清单表(含删除清理)
    scope = {"files": set(existing), "dirs": {rel_dir(rel) for rel in pending} | extra_dirs}
    writes += [w.replace("(dry-run)", "") for w in geb_sync.sync(root, scope=scope, scope_rows_only=True)]
    for index, cell, duty in renames:
        if set_duty(index, cell, duty):
            writes.append("[职责沿用] " + cell)

    # 5) 语义缺口,每个文件一条;证据只取头部标签行、清单行和导出,改函数体不会重复提示
    for rel in sorted(existing):
        path = os.path.join(root, rel)
        entry = pending[rel]
        head_text = head_of(read_strict(path))
        needs, evidence = [], []
        missing = [t for t in L3_TAGS if t not in head_text]
        if rel in added and missing:
            needs.append("文件头缺 " + "、".join(missing))
            evidence += tag_lines(head_text) + missing
        elif rel in added and any(m in head_text for m in TODO_MARKERS):
            needs.append("补文件头 [POS] 等占位")
            evidence += tag_lines(head_text)
        elif not missing and entry.get("view") and entry["view"].get("exports") is not None:
            current = view_of(path)
            if current["output_line"] != entry["view"].get("output_line"):
                entry["view"] = current  # 模型已改过 [OUTPUT]:以当前内容为新的确认点
            elif current["exports"] is not None and current["exports"] != entry["view"]["exports"]:
                plus = [e for e in current["exports"] if e not in entry["view"]["exports"]]
                minus = [e for e in entry["view"]["exports"] if e not in current["exports"]]
                delta = " ".join(["+" + e for e in plus[:4]] + ["-" + e for e in minus[:4]])
                needs.append("导出变化(%s),若对外能力变了改 [OUTPUT] 一行" % (delta or "顺序"))
                evidence.append(json.dumps(current, ensure_ascii=True))
        if rel in added:
            _sub, project, _map, small, local = projects.info(rel)
            index, style = index_for(project, rel_dir(local), small)
            cell = os.path.basename(local) if style == "name" else local
            row = table_rows(index).get(cell) if index else None
            if row and any(m in row for m in TODO_MARKERS):
                needs.append("%s 中该行职责" % norm(os.path.relpath(index, root)))
                evidence.append(row.strip())
        if needs:
            gaps.append(("file:" + rel, text_digest("\n".join(evidence)), "%s:%s" % (rel, ";".join(needs))))
    for d in sorted(set(new_dirs) | set(l2_dirs)):
        _sub, project, _map, _small, local = projects.info(d + "/_")
        l2 = find_index_file(os.path.join(project, rel_dir(local)), L2_NAMES)
        section = (read_strict(l2) or "").split("## 文件清单")[0] if l2 else ""
        if any(m in section for m in TODO_MARKERS):
            gaps.append(("module:" + d, text_digest(section), "%s:补模块定位" % norm(os.path.relpath(l2, root))))
    for sub, top in sorted(new_tops):
        project = os.path.join(root, sub) if sub else root
        l1 = find_index_file(project, L1_NAMES)
        if l1 and top not in (read_strict(l1) or ""):
            gaps.append(("l1:%s/%s" % (sub, top), text_digest(top),
                         "%s:目录结构未提及新目录 %s" % (norm(os.path.relpath(l1, root)), top)))
    return gaps, writes, skipped


def fresh_gaps(gaps, state):
    """同一缺口证据不变时只提示一次;模型选择不改,交给提交检查和 CI 兜底。"""
    prompted = state.setdefault("prompted", {})
    return [(key, digest, text) for key, digest, text in gaps if prompted.get(key) != digest]


def block_reason(items, writes):
    lines = ["赋格:机器字段已自动同步%s。请只补下列语义字段(各一句,依据刚读过的代码),不要运行脚本:"
             % ("(%d 处)" % len(writes) if writes else "")]
    lines += ["- " + text for _key, _digest, text in items[:MAX_GAPS]]
    if len(items) > MAX_GAPS:
        lines.append("- 其余 %d 项可留给提交检查" % (len(items) - MAX_GAPS))
    return "\n".join(lines)


# ---------------- 计量 ----------------

def transcript_usage(path):
    """按消息 ID 去重累计对话记录里的用量。格式未公开,读不到就返回未知。"""
    if not path or not os.path.isfile(path):
        return None, "transcript_missing"
    per, models, sidechain = {}, set(), set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                usage = message.get("usage")
                key = message.get("id") or entry.get("requestId") or entry.get("uuid")
                if not isinstance(usage, dict) or not key:
                    continue
                values = {}
                for field in USAGE_FIELDS:
                    value = usage.get(field, 0)
                    if value is None:
                        value = 0
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        break
                    values[field] = value
                else:
                    previous = per.get(key)
                    # 流式写入时同一条消息会出现多行,取各字段最大值
                    per[key] = values if previous is None else {
                        field: max(previous[field], values[field]) for field in USAGE_FIELDS}
                    if message.get("model") and message["model"] != "<synthetic>":
                        models.add(message["model"])
                    if entry.get("isSidechain"):
                        sidechain.add(key)
    except OSError:
        return None, "transcript_unreadable"
    totals = {field: sum(v[field] for v in per.values()) for field in USAGE_FIELDS}
    totals.update(messages=len(per), sidechain_messages=len(sidechain), models=sorted(models))
    return totals, "ok" if per else "no_usage_entries"


def normalize_usage(raw):
    """换算成账本口径:缓存读取是输入的子集;未缓存输入含缓存写入。"""
    uncached = raw["input_tokens"] + raw["cache_creation_input_tokens"]
    total_input = uncached + raw["cache_read_input_tokens"]
    return {"input_tokens": total_input, "cached_input_tokens": raw["cache_read_input_tokens"],
            "cache_creation_input_tokens": raw["cache_creation_input_tokens"],
            "output_tokens": raw["output_tokens"], "total_tokens": total_input + raw["output_tokens"],
            "uncached_input_tokens": uncached, "uncached_plus_output": uncached + raw["output_tokens"],
            "messages": raw["messages"]}


def interval(start, end):
    if start is None or end is None:
        return None, "missing_measurements"
    delta = {field: end[field] - start[field] for field in USAGE_FIELDS + ("messages",)}
    if any(value < 0 for value in delta.values()):
        return None, "counter_reset"
    if delta["messages"] == 0:
        return None, "no_new_usage"  # 对话记录可能尚未落盘,没有新数据不记成零
    return normalize_usage(delta), "measured_interval"


def usage_point(totals):
    return None if totals is None else {k: totals[k] for k in USAGE_FIELDS + ("messages",)}


def record_metering(state, payload, event):
    session_id = state["session_id"]
    end, reason = transcript_usage(payload.get("transcript_path") or state.get("transcript_path"))
    usage, status = interval(state.get("usage_start"), end)
    if reason != "ok":
        usage, status = None, reason
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "fugue-claude-session:" + session_id))
    path = os.path.join(data_dir(), "metrics", run_id + ".json")
    record = read_json(path) or {
        "schema": "geb.metrics.v2", "run_id": run_id, "agent": "claude-code",
        "task": "claude-session", "condition": "fugue", "root": state["root"],
        "started_at": state["started_at"], "git": state.get("git_state"),
        "saved_tokens": None, "saving_status": "no_comparable_baseline", "source": "claude_transcript",
        "coverage": ("transcript entries de-duplicated by message id; transcript format is "
                     "internal to Claude Code; subagent transcripts in other files excluded")}
    record.update(session_id=session_id, status=status, usage=usage, updated_at=now(), last_event=event,
                  coverage_start=state.get("coverage_start", "session_start"),
                  models=(end or {}).get("models"), sidechain_messages=(end or {}).get("sidechain_messages"),
                  maintenance={k: state.get(k, 0) for k in ("stops", "blocks", "block_chars", "auto_writes",
                                                            "skipped_turns")})
    write_json(path, record)


# ---------------- 入口 ----------------

def new_state(session_id, root, payload, coverage_start="session_start"):
    totals, _reason = transcript_usage(payload.get("transcript_path"))
    if totals is None and coverage_start == "session_start" and payload.get("source") in (None, "startup", "clear"):
        totals = {field: 0 for field in USAGE_FIELDS}
        totals["messages"] = 0
    head = current_head(root)
    return {"schema": "geb.hook-session.v3", "session_id": session_id, "root": root,
            "started_at": now(), "transcript_path": payload.get("transcript_path"),
            "small_at_start": project_sizes(root), "authored": {}, "prompted": {},
            "git_state": {"commit": head, "dirty": bool(git(root, "status", "--porcelain"))} if head else None,
            "usage_start": usage_point(totals), "coverage_start": coverage_start,
            "stops": 0, "blocks": 0, "block_chars": 0, "auto_writes": 0, "skipped_turns": 0}


def prune_states():
    directory = os.path.join(data_dir(), "sessions")
    cutoff = time.time() - STATE_TTL_SECONDS
    try:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass


def adopted(payload):
    """返回 (会话 ID, 项目根, 状态);项目未采用协议时会话 ID 为 None。"""
    session_id = safe_id(payload.get("session_id"))
    root = project_root(payload)
    if not session_id or not find_index_file(root, L1_NAMES):
        return None, None, None
    state = read_json(session_file(session_id, ".json"))
    if state is not None and (state.get("root") != root or state.get("schema") != "geb.hook-session.v3"):
        state = None
    return session_id, root, state


def save_state(state):
    write_json(session_file(state["session_id"], ".json"), state)


def session_start(payload):
    session_id, root, state = adopted(payload)
    if not session_id:
        return None
    prune_states()
    if state is None:
        state = new_state(session_id, root, payload)
    elif payload.get("source") == "resume":
        drop_touched_by_others(root, state)  # 恢复会话:排除会话之间别人做的改动
    save_state(state)
    if os.environ.get("FUGUE_HOOK_QUIET"):
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": NAVIGATION_HINT}}


def prompt(payload):
    """用户发消息:两轮之间被别人再改过的文件不再算本会话的。不输出任何内容。"""
    _session_id, root, state = adopted(payload)
    if state is not None:
        drop_touched_by_others(root, state)
        save_state(state)
    return None


def pre_tool(payload):
    session_id, root, _state = adopted(payload)
    if not session_id:
        return None
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool in FILE_TOOLS:
        rel = tool_rel(root, payload, tool_input.get("file_path") or tool_input.get("notebook_path"))
        if rel and relevant_code(rel):
            append_log(session_id, [pre_record(root, rel)], root)
    elif tool == "Bash":
        command = str(tool_input.get("command") or "")
        snap = None if GIT_WORKTREE.search(command) else bash_snapshot(root, session_id)
        write_json(bash_key(session_id, payload), {"snapshot": snap})
    return None


def post_tool(payload):
    session_id, root, _state = adopted(payload)
    if not session_id or payload.get("tool_name") != "Bash":
        return None
    path = bash_key(session_id, payload)
    if os.path.exists(path):
        flush_bash(session_id, root, path)
    return None


def stop(payload):
    session_id, root, state = adopted(payload)
    if not session_id:
        return None
    if state is None:
        # 钩子在会话中途才装上:从现在起记录工具调用和计量
        state = new_state(session_id, root, payload, coverage_start="hook_installed_mid_session")
    flush_leftover_bash(session_id, root)
    merge_log(state)
    state["stops"] = state.get("stops", 0) + 1
    output, writes, skipped = None, [], []
    git_mode = is_git(root)
    busy = git_mode and git_busy(root)
    pending = None if busy else pending_entries(root, state, git_mode)
    if pending is None:
        state["skipped_turns"] = state.get("skipped_turns", 0) + 1
        state["last_skip"] = "merge_or_rebase_in_progress" if busy else "git_unavailable"
    elif pending:
        gaps, writes, skipped = maintain(root, state, pending)
        if skipped:
            state["last_skip"] = "files_skipped:" + ",".join(skipped[:3])
        items = fresh_gaps(gaps, state)
        if items and not payload.get("stop_hook_active"):
            reason = block_reason(items, writes)
            for key, digest, _text in items[:MAX_GAPS]:
                state["prompted"][key] = digest
            state["blocks"] = state.get("blocks", 0) + 1
            state["block_chars"] = state.get("block_chars", 0) + len(reason)
            output = {"decision": "block", "reason": reason}
        elif writes:
            output = {"systemMessage": "赋格:已自动同步 %d 处索引机器字段" % len(writes)}
    # 收工时记下每个文件此刻的内容(含失败编辑、本轮跳过的文件):下一轮开始前若被别人改过就移出记录
    for rel, entry in state.get("authored", {}).items():
        entry["seen_sha"] = sha(os.path.join(root, rel))
    state["auto_writes"] = state.get("auto_writes", 0) + len(writes)
    save_state(state)
    record_metering(state, payload, "stop")
    return output


def session_end(payload):
    session_id = safe_id(payload.get("session_id"))
    state = read_json(session_file(session_id, ".json")) if session_id else None
    if state is not None and state.get("schema") == "geb.hook-session.v3":
        record_metering(state, payload, "session_end")
    return None


HANDLERS = {"session-start": session_start, "prompt": prompt, "pre-tool": pre_tool,
            "post-tool": post_tool, "stop": stop, "session-end": session_end}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in HANDLERS:
        sys.stderr.write("usage: geb_hook.py %s < hook-payload.json\n" % "|".join(HANDLERS))
        return 0
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        output = HANDLERS[argv[0]](payload)
        if output:
            # ASCII JSON:任何终端编码下都能写出,Claude Code 解析后还原中文
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
    except Exception as error:  # noqa: BLE001
        log_error(argv[0], error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
