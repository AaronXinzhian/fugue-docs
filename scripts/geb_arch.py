#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, collections, json, os, re, sys, geb_check, geb_scaffold
[OUTPUT]: 提供程序化架构候选生成命令——从代码事实生成模块角色、入口、依赖边、风险提示与 AI handoff brief
[POS]: fugue-docs 工具层-架构事实与候选生成器(先由程序给出可验证事实,再交给 AI 做语义归纳)
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md 与 README/SKILL 中对本脚本的描述

定位:本脚本只生成机器可推导的事实和候选判断,不替代语义文档。
它的输出应被 AI/人当作"证据包":先看事实、再确认职责边界、最后写入 L1/L2/L3 的语义字段。
"""

import argparse
from collections import defaultdict
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geb_check import is_small_project, walk_project  # noqa: E402
from geb_scaffold import analyze_file  # noqa: E402


ROLE_RULES = [
    ("interface", "接口/路由层", ("route", "routes", "api", "controller", "controllers", "view", "views")),
    ("business", "业务服务层", ("service", "services", "usecase", "usecases", "domain")),
    ("persistence", "持久化/数据访问层", ("storage", "store", "db", "database", "repo", "repository", "repositories")),
    ("model", "数据模型/实体层", ("model", "models", "entity", "entities", "schema", "schemas")),
    ("utility", "通用工具层", ("util", "utils", "helper", "helpers", "common", "lib", "libs")),
    ("tooling", "工程工具层", ("script", "scripts", "tool", "tools", "bin", "cli")),
    ("adapter", "适配/集成层", ("adapter", "adapters", "plugin", "plugins", "integration", "integrations")),
    ("evaluation", "测试/评测层", ("test", "tests", "spec", "specs", "eval", "evals", "benchmark", "benchmarks")),
]

ENTRYPOINT_NAMES = {
    "app", "main", "index", "server", "cli", "manage", "run", "start",
}

LEGACY_WORDS = ("legacy", "deprecated", "old", "compat")


def norm(path):
    return os.path.normpath(path).replace("\\", "/")


def top_module(rel_dir):
    if rel_dir == ".":
        return "root"
    return norm(rel_dir).split("/")[0]


def module_path(name):
    return "." if name == "root" else name


def ext_counts(files):
    counts = defaultdict(int)
    for rel in files:
        counts[os.path.splitext(rel)[1] or "(none)"] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def has_legacy_marker(path):
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", path.lower()) if t]
    return any(t in LEGACY_WORDS for t in tokens)


def load_project(root):
    subprojects = []
    dir_map = walk_project(root, subprojects)
    analyses_by_file = {}
    files = {}
    for rel_dir, names in sorted(dir_map.items()):
        for name in sorted(names):
            rel = norm(os.path.join(rel_dir, name))
            imports, exports = analyze_file(os.path.join(root, rel))
            analyses_by_file[(rel_dir, name)] = (imports, exports)
            files[rel] = {
                "dir": rel_dir.replace(os.sep, "/"),
                "module": top_module(rel_dir),
                "extension": os.path.splitext(name)[1].lower(),
                "imports": imports,
                "exports": exports,
            }
    return dir_map, subprojects, analyses_by_file, files


def import_index(files):
    index = {}
    for rel, meta in files.items():
        stem = os.path.splitext(os.path.basename(rel))[0]
        dotted = os.path.splitext(rel)[0].replace("/", ".")
        index.setdefault(stem, set()).add(meta["module"])
        index.setdefault(dotted, set()).add(meta["module"])
    return index


def resolve_internal_import(imp, rel_dir, import_map, top_dirs):
    if not imp:
        return None
    if imp.startswith("."):
        dots = len(imp) - len(imp.lstrip("."))
        rest = imp.lstrip(".").replace(".", "/")
        base = rel_dir
        for _i in range(max(dots - 1, 0)):
            base = os.path.dirname(base) or "."
        return top_module(norm(os.path.join(base, rest)) or ".")
    cleaned = imp.strip()
    first = re.split(r"[./:]", cleaned)[0]
    if first in top_dirs:
        return first
    if cleaned in import_map and len(import_map[cleaned]) == 1:
        return next(iter(import_map[cleaned]))
    if first in import_map and len(import_map[first]) == 1:
        return next(iter(import_map[first]))
    return None


def dependency_edges(dir_map, files):
    top_dirs = {top_module(d) for d in dir_map}
    idx = import_index(files)
    edges = set()
    for rel, meta in files.items():
        src = meta["module"]
        for imp in meta["imports"]:
            dst = resolve_internal_import(imp, meta["dir"], idx, top_dirs)
            if dst and dst != src:
                edges.add((src, dst))
    return sorted(edges)


def manifests(root):
    names = (
        "package.json", "pyproject.toml", "requirements.txt", "go.mod",
        "Cargo.toml", "pom.xml", "build.gradle", "Gemfile", "composer.json",
        "Makefile",
    )
    return [name for name in names if os.path.isfile(os.path.join(root, name))]


def role_by_name(name):
    lowered = name.lower()
    parts = set(re.split(r"[-_/.\s]+", lowered))
    for role_id, label, keywords in ROLE_RULES:
        if lowered in keywords or parts.intersection(keywords):
            return role_id, label, ["名称命中: %s" % name], 0.72
    if name == "root":
        return "root", "项目根/入口区", ["根目录含代码文件"], 0.55
    return "module", "功能模块", ["未命中强命名规则"], 0.35


def role_by_edges(name, outgoing, incoming):
    evidence = []
    score = 0.0
    role_id, label = None, None
    if name == "root":
        return None, None, evidence, score
    if "root" in incoming and outgoing:
        role_id, label = "app-facing", "入口下游功能模块"
        evidence.append("被 root 调用且继续依赖其他模块")
        score = 0.12
    if not outgoing and len(incoming) >= 2:
        role_id, label = "foundation", "基础能力/工具模块"
        evidence.append("被多个模块依赖且没有项目内下游依赖")
        score = 0.18
    if outgoing and not incoming:
        role_id, label = "driver", "上游驱动/入口候选模块"
        evidence.append("有项目内下游依赖但暂无上游模块")
        score = 0.1
    return role_id, label, evidence, score


def find_entrypoints(files):
    candidates = []
    for rel, meta in sorted(files.items()):
        base = os.path.splitext(os.path.basename(rel))[0].lower()
        exports = meta["exports"]
        evidence = []
        confidence = 0.0
        if base in ENTRYPOINT_NAMES:
            evidence.append("文件名命中入口模式: %s" % os.path.basename(rel))
            confidence += 0.45
        if any(e.startswith("main") or e in ("app", "server") for e in exports):
            evidence.append("导出入口样式符号: %s" % ", ".join(exports))
            confidence += 0.25
        if evidence and meta["module"] == "root":
            evidence.append("位于项目根目录")
            confidence += 0.15
        if evidence:
            candidates.append({
                "file": rel,
                "confidence": round(min(confidence, 0.95), 2),
                "evidence": evidence,
            })
    return sorted(candidates, key=lambda x: (-x["confidence"], x["file"]))


def build_modules(dir_map, files, edges):
    grouped = defaultdict(list)
    for rel, meta in files.items():
        grouped[meta["module"]].append(rel)
    outgoing = defaultdict(set)
    incoming = defaultdict(set)
    for src, dst in edges:
        outgoing[src].add(dst)
        incoming[dst].add(src)
    modules = []
    for name in sorted(grouped):
        by_name_id, by_name_label, evidence, confidence = role_by_name(name)
        by_edge_id, by_edge_label, edge_evidence, edge_score = role_by_edges(
            name, outgoing[name], incoming[name])
        role_id = by_edge_id or by_name_id
        role_label = by_edge_label or by_name_label
        evidence = evidence + edge_evidence
        confidence = min(confidence + edge_score, 0.92)
        warnings = []
        legacy_files = [f for f in grouped[name] if has_legacy_marker(f)]
        if legacy_files:
            warnings.append("疑似历史兼容/待退役文件: %s" % ", ".join(legacy_files[:5]))
        if name != "root" and not outgoing[name] and not incoming[name]:
            warnings.append("暂无项目内依赖边,可能是孤立模块或静态分析未识别")
        modules.append({
            "name": name,
            "path": module_path(name),
            "role_candidate": role_label,
            "role_id": role_id,
            "confidence": round(confidence, 2),
            "evidence": evidence,
            "file_count": len(grouped[name]),
            "extensions": ext_counts(grouped[name]),
            "imports_to": sorted(outgoing[name]),
            "imported_by": sorted(incoming[name]),
            "fan_out": len(outgoing[name]),
            "fan_in": len(incoming[name]),
            "files": sorted(grouped[name]),
            "warnings": warnings,
        })
    return modules


def find_cycles(edges):
    graph = defaultdict(list)
    for src, dst in edges:
        graph[src].append(dst)
    cycles = set()

    def canonical(cycle):
        body = cycle[:-1]
        rotations = [tuple(body[i:] + body[:i]) for i in range(len(body))]
        rev = list(reversed(body))
        rotations += [tuple(rev[i:] + rev[:i]) for i in range(len(rev))]
        best = min(rotations)
        return best + (best[0],)

    def visit(node, stack):
        if node in stack:
            cycle = stack[stack.index(node):] + [node]
            cycles.add(canonical(cycle))
            return
        if len(stack) > 20:
            return
        for nxt in graph.get(node, []):
            visit(nxt, stack + [node])

    for node in sorted(graph):
        visit(node, [])
    return [list(c) for c in sorted(cycles)]


def project_warnings(modules, cycles):
    warnings = []
    if cycles:
        warnings.append("检测到顶层模块循环依赖: %s" % "; ".join(" -> ".join(c) for c in cycles[:5]))
    for module in modules:
        if module["fan_out"] >= 5:
            warnings.append("%s fan_out=%d,可能承担过多编排职责" % (module["name"], module["fan_out"]))
        if module["fan_in"] >= 5:
            warnings.append("%s fan_in=%d,是高复用/高影响模块" % (module["name"], module["fan_in"]))
        warnings += ["%s: %s" % (module["name"], w) for w in module["warnings"]]
    return warnings


def build_report(root):
    root = os.path.abspath(root)
    dir_map, subprojects, analyses_by_file, files = load_project(root)
    edges = dependency_edges(dir_map, files)
    modules = build_modules(dir_map, files, edges)
    cycles = find_cycles(edges)
    languages = defaultdict(int)
    for meta in files.values():
        languages[meta["extension"]] += 1
    return {
        "schema": "geb.arch.v1",
        "root": os.path.basename(root),
        "profile": {
            "code_files": len(files),
            "code_dirs": len(dir_map),
            "small_project": is_small_project(dir_map),
            "subprojects": sorted(s.replace(os.sep, "/") for s in subprojects),
            "manifests": manifests(root),
            "languages": dict(sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        "files": files,
        "edges": [{"from": src, "to": dst} for src, dst in edges],
        "entrypoint_candidates": find_entrypoints(files),
        "module_candidates": modules,
        "cycles": cycles,
        "warnings": project_warnings(modules, cycles),
        "llm_handoff": {
            "principle": "这是静态分析生成的事实与候选,不是最终语义文档。",
            "next_steps": [
                "先核对入口、模块角色、依赖边是否符合真实架构。",
                "把高置信候选写入 L1/L2 的语义字段,低置信候选必须读代码确认。",
                "对 warnings 中的循环依赖、孤立模块、legacy 文件给出处理判断。",
                "最后运行 geb_sync 与 geb_check 完成回环。",
            ],
        },
    }


def render_markdown(report):
    lines = [
        "# GEB 架构候选报告(机器生成)",
        "",
        "> 本报告来自静态分析,只作为事实包和候选判断;最终职责命名与架构解释必须由 AI/人读代码确认。",
        "",
        "## 概览",
        "",
        "- 代码文件:%d" % report["profile"]["code_files"],
        "- 代码目录:%d" % report["profile"]["code_dirs"],
        "- 小项目 profile:%s" % ("是" if report["profile"]["small_project"] else "否"),
        "- 语言:%s" % (", ".join("%s=%s" % (k, v) for k, v in report["profile"]["languages"].items()) or "—"),
        "- 清单:%s" % (", ".join(report["profile"]["manifests"]) or "—"),
        "",
        "## 入口候选",
        "",
    ]
    if report["entrypoint_candidates"]:
        for item in report["entrypoint_candidates"]:
            lines.append("- `%s` 置信度 %.2f;%s" % (
                item["file"], item["confidence"], "；".join(item["evidence"])))
    else:
        lines.append("- 未识别强入口候选")
    lines += ["", "## 模块候选", "", "| 模块 | 候选角色 | 置信度 | fan-in | fan-out | 证据 |", "|------|----------|--------|--------|---------|------|"]
    for module in report["module_candidates"]:
        lines.append("| %s | %s | %.2f | %d | %d | %s |" % (
            module["name"], module["role_candidate"], module["confidence"],
            module["fan_in"], module["fan_out"], "<br>".join(module["evidence"])))
    lines += ["", "## 依赖图", "", "```mermaid", "graph TD"]
    for edge in report["edges"]:
        lines.append("    %s --> %s" % (edge["from"], edge["to"]))
    if not report["edges"]:
        lines.append("    %% 未识别顶层模块依赖边")
    lines += ["```", "", "## 风险提示", ""]
    if report["warnings"]:
        for warning in report["warnings"]:
            lines.append("- %s" % warning)
    else:
        lines.append("- 未发现结构级风险提示")
    lines += ["", "## 给 AI 的下一步", ""]
    for step in report["llm_handoff"]["next_steps"]:
        lines.append("- %s" % step)
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="GEB 架构事实与候选生成器")
    parser.add_argument("root", help="项目根目录")
    parser.add_argument("--out", help="写出 JSON 报告")
    parser.add_argument("--brief", help="写出 Markdown handoff brief")
    parser.add_argument("--json", action="store_true", help="打印 JSON 到 stdout")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误: %s 不是目录" % root, file=sys.stderr)
        return 2
    report = build_report(root)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    wrote = []
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        wrote.append(args.out)
    if args.brief:
        with open(args.brief, "w", encoding="utf-8") as f:
            f.write(render_markdown(report))
        wrote.append(args.brief)
    if args.json or not wrote:
        print(text)
    else:
        print("架构候选已写入:%s" % ", ".join(wrote))
    return 0


if __name__ == "__main__":
    sys.exit(main())
