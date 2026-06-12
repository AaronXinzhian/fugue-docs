#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, os, re, subprocess, sys, geb_check, geb_scaffold
[OUTPUT]: 提供机器字段同步命令——重写 L3 [INPUT] 行、重建 L2/L1 清单表(语义列保留),--graph 重绘依赖图
[POS]: fugue-docs 工具层-视图同步器:衍生数据重新生成而非人工维护,这是 v2.0 架构的核心
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md 与 SKILL.md 中对本脚本的描述

第一性原理:[INPUT]、清单表的"关键导出"列、依赖图都是从代码可推导的衍生数据。
让人或 AI 手工维护可推导信息,等于制造第二份事实源,漂移是必然的。本工具把它们
变成"视图":每次运行从代码重新生成;真正需要智能的语义([POS]、职责、模块定位、
全局约定)一个字不碰。回环因此从"全手工"变成"机器字段一条命令 + 语义字段按需"。

字段归属约定:
- 机器维护(本工具重写):L3 [INPUT];L2/L1 清单表的行集合与"关键导出"列;--graph 时的 Mermaid 图
- 人/AI 维护(本工具保留):L3 [OUTPUT] 的语义加注与 [POS];清单表"职责"列;模块定位、全局约定等散文
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geb_check import (L1_NAMES, L2_NAMES, find_index_file,  # noqa: E402
                       is_small_project, walk_project)
from geb_scaffold import analyze_file, mermaid_edges  # noqa: E402

_TODO = "TODO(" + "语义)"
INPUT_TAG = "[INPUT]"


def read_keepnl(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        text = f.read()
    return text, ("\r\n" if "\r\n" in text else "\n")


def write_keepnl(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# ---------------- L3:[INPUT] 行重写 ----------------

def sync_l3_input(filepath, imports, dry=False):
    """把文件头第一条 [INPUT] 行重写为静态分析结果;无 [INPUT] 行则不动(那是脚手架的事)。"""
    text, nl = read_keepnl(filepath)
    lines = text.splitlines(keepends=True)
    payload = ", ".join(imports) if imports else "(未检出外部依赖)"
    for i, line in enumerate(lines[:60]):
        if INPUT_TAG in line:
            prefix = line[: line.index(INPUT_TAG)]
            ending = nl if line.endswith(("\n", "\r")) else ""
            new_line = "%s%s: 依赖 %s%s" % (prefix, INPUT_TAG, payload, ending)
            if new_line == line:
                return False
            if not dry:
                lines[i] = new_line
                write_keepnl(filepath, "".join(lines))
            return True
    return False


# ---------------- 清单表重建(职责列保留) ----------------

TABLE_HEADINGS = ("## 文件清单", "## 根目录文件")
_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


def _split_row(line):
    return [c.strip() for c in _ROW.match(line).group(1).split("|")]


def rebuild_table(index_path, files, analyses, dry=False, headings=None):
    """重建索引中清单表的数据行:行集合 = 实际文件(增列删清),
    '职责'列沿用旧值(没有则 TODO),'关键导出'列由分析结果重写。
    表不存在或表头不含'文件'列时跳过,不强行改造人家的格式。"""
    text, nl = read_keepnl(index_path)
    lines = text.splitlines(keepends=True)
    # 定位清单表:标题行 → 其后第一个表头行
    heads = headings or TABLE_HEADINGS
    head_i = next((i for i, l in enumerate(lines)
                   if l.strip().rstrip(nl) in heads or l.strip() in heads), None)
    if head_i is None:
        return None
    tbl = next((i for i in range(head_i + 1, min(head_i + 6, len(lines)))
                if _ROW.match(lines[i])), None)
    if tbl is None or tbl + 1 >= len(lines) or not _ROW.match(lines[tbl + 1]):
        return None
    header = _split_row(lines[tbl])
    ncol = len(header)
    if "文件" not in header[0]:
        return None
    # 收集旧数据行:文件名 → 整行单元格
    old = {}
    end = tbl + 2
    while end < len(lines) and _ROW.match(lines[end]):
        cells = _split_row(lines[end])
        if cells and cells[0]:
            old[cells[0].strip("`")] = cells
        end += 1
    # 生成新数据行
    export_col = next((j for j, h in enumerate(header) if "导出" in h), None)
    duty_col = next((j for j, h in enumerate(header) if "职责" in h), None)
    new_rows = []
    for f in files:
        cells = list(old.get(f, [])) or [f] + [""] * (ncol - 1)
        cells += [""] * (ncol - len(cells))
        cells = cells[:ncol]
        cells[0] = f
        if duty_col is not None and not cells[duty_col]:
            cells[duty_col] = _TODO + ":职责"
        if export_col is not None:
            exports = analyses.get(f, ([], []))[1]
            cells[export_col] = ", ".join(exports) if exports else "—"
        new_rows.append("| " + " | ".join(cells) + " |" + nl)
    new_lines = lines[: tbl + 2] + new_rows + lines[end:]
    new_text = "".join(new_lines)
    if new_text == text:
        return False
    if not dry:
        write_keepnl(index_path, new_text)
    return True


# ---------------- Mermaid 图重绘(--graph,显式开启) ----------------

def rebuild_graph(l1_path, root, dir_map, analyses_by_file, dry=False):
    edges = mermaid_edges(root, dir_map, analyses_by_file)
    if not edges:
        return None  # 算不出边就不动手,绝不拿空图覆盖人写的图
    text, nl = read_keepnl(l1_path)
    m = re.search(r"(```mermaid\s*?%s)(.*?)(```)" % re.escape(nl), text, re.S)
    if not m:
        return None
    body = nl.join(["graph TD"] + ["    %s --> %s" % e for e in edges]) + nl
    new_text = text[: m.start(2)] + body + text[m.start(3):]
    if new_text == text:
        return False
    if not dry:
        write_keepnl(l1_path, new_text)
    return True


# ---------------- 主流程 ----------------

def git_changed(root):
    """git 工作区变更范围(含未跟踪文件);拿不到则返回 None 表示退回全量。"""
    try:
        r = subprocess.run(["git", "-C", root, "diff", "--name-only", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        u = subprocess.run(["git", "-C", root, "ls-files", "--others",
                            "--exclude-standard"],
                           capture_output=True, text=True, timeout=15)
        return {os.path.normpath(p) for p in
                r.stdout.splitlines() + u.stdout.splitlines() if p.strip()}
    except Exception:  # noqa: BLE001
        return None


def sync(root, graph=False, dry_run=False, prefix="", only=None):
    subprojects = []
    dir_map = walk_project(root, subprojects)
    analyses_by_file = {
        (d, f): analyze_file(os.path.join(root, d, f))
        for d, files in dir_map.items() for f in files
    }
    changed = []
    suffix = "(dry-run)" if dry_run else ""

    for (d, f), (imports, _o) in sorted(analyses_by_file.items()):
        rel = os.path.normpath(os.path.join(d, f))
        if only is not None and rel not in only:
            continue  # --changed 增量:未动过的文件不碰
        if sync_l3_input(os.path.join(root, rel), imports, dry=dry_run):
            changed.append("[L3] %s%s" % (prefix + rel, suffix))

    # 小项目 profile:L1 持有全量文件清单(无 L2),整表一次重建
    handled_small = False
    if is_small_project(dir_map):
        l1 = find_index_file(root, L1_NAMES)
        if l1:
            names, analyses = [], {}
            for d, files in sorted(dir_map.items()):
                for f in sorted(files):
                    name = os.path.normpath(os.path.join(d, f)).replace(os.sep, "/")
                    names.append(name)
                    analyses[name] = analyses_by_file[(d, f)]
            r = rebuild_table(l1, names, analyses, dry=dry_run,
                              headings=("## 文件清单",))
            if r is not None:  # L1 没有该标题时回落到默认逐目录处理
                handled_small = True
                if r:
                    changed.append("[L1] %s%s" % (prefix + os.path.relpath(l1, root), suffix))
    for d, files in sorted(dir_map.items()):
        if handled_small:
            break
        if only is not None and not any(
                os.path.normpath(os.path.join(d, f)) in only for f in files):
            continue
        idx = (find_index_file(root, L1_NAMES) if d == "."
               else find_index_file(os.path.join(root, d), L2_NAMES))
        if not idx:
            continue
        analyses = {f: analyses_by_file[(d, f)] for f in files}
        if rebuild_table(idx, files, analyses, dry=dry_run):
            changed.append("[%s] %s%s" % ("L1" if d == "." else "L2",
                                          prefix + os.path.relpath(idx, root), suffix))
    if graph:
        l1 = find_index_file(root, L1_NAMES)
        if l1 and rebuild_graph(l1, root, dir_map, analyses_by_file, dry=dry_run):
            changed.append("[图] %s%s" % (prefix + os.path.relpath(l1, root), suffix))
    # 递归分形:子项目同样同步(--changed 时把变更范围换算到子项目坐标)
    for sp in subprojects:
        sub_only = None
        if only is not None:
            spp = sp + os.sep
            sub_only = {rel[len(spp):] for rel in only if rel.startswith(spp)}
            if not sub_only:
                continue
        changed += sync(os.path.join(root, sp), graph=graph, dry_run=dry_run,
                        prefix=prefix + sp + os.sep, only=sub_only)
    return changed


def main():
    parser = argparse.ArgumentParser(
        description="GEB 视图同步器:机器字段从代码重新生成(语义字段保留)")
    parser.add_argument("root", help="项目根目录")
    parser.add_argument("--graph", action="store_true",
                        help="同时重绘 L1 的 Mermaid 依赖图(会覆盖手绘节点名,显式开启)")
    parser.add_argument("--changed", action="store_true",
                        help="只同步 git 工作区有改动的文件(含未跟踪),大仓库提速")
    parser.add_argument("--dry-run", action="store_true", help="只报告,不写文件")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误: %s 不是目录" % root, file=sys.stderr)
        return 2
    only = None
    if args.changed:
        only = git_changed(root)
        if only is None:
            print("警告: 拿不到 git 变更范围(不是 git 仓库?),退回全量同步",
                  file=sys.stderr)
    changed = sync(root, graph=args.graph, dry_run=args.dry_run, only=only)
    for line in changed:
        print(line)
    print("同步%s:%d 处%s。语义字段([POS]/职责/定位)未触碰,如有语义变化请人工/AI 补全后跑 geb_check。"
          % ("预演" if args.dry_run else "完成", len(changed),
             "存在漂移" if args.dry_run else "更新"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
