#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, ast, os, re, sys, geb_check
[OUTPUT]: 提供确定性脚手架命令——为缺失处生成 L3 头骨架、L2/L1 索引草稿(语义留 TODO)
[POS]: fugue-docs 工具层-确定性脚手架(静态分析填 INPUT/OUTPUT,语义相留给 AI/人)
[PROTOCOL]: 变更时更新此头部,然后检查 SKILL.md 与 README 中对本脚本的描述

设计哲学:机器只填机器擅长的(依赖、导出——可静态分析),绝不假装理解语义。
[POS]、模块定位、项目定位一律留语义 TODO 占位,由 AI 真读代码后补全。
这样大项目初始化从"逐文件精读"降为"补语义",省时省 token,且不产生假文档。

幂等:已有完整 L3 头的文件、已存在的索引文件一律跳过,绝不覆盖。
"""

import argparse
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geb_check import walk_project, check_l3, find_index_file, L1_NAMES, L2_NAMES  # noqa: E402

# 占位前缀拆开拼接,避免本文件头部的源码字面量被 geb_check --complete 误判为残留占位
_TODO = "TODO(" + "语义)"
TODO_POS = _TODO + ":本文件在系统中的定位与职责"
TODO_MODULE = _TODO + ":本模块在整体架构中的角色、被谁调用、调用谁"
TODO_PROJECT = _TODO + ":这个项目是什么、给谁用、解决什么问题"
MAX_ITEMS = 10


# ---------------- 静态分析(各语言) ----------------

def analyze_python(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return analyze_generic(text)
    inputs, outputs = [], []
    for node in tree.body:
        if isinstance(node, ast.Import):
            inputs += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            inputs.append("." * node.level + (node.module or ""))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                suffix = "" if isinstance(node, ast.ClassDef) else "()"
                outputs.append(node.name + suffix)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    outputs.append(t.id)
    return inputs, outputs


def analyze_js(text):
    inputs = re.findall(r"""(?:import[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*)['"]([^'"]+)['"]""", text)
    inputs += re.findall(r"""^\s*import\s*['"]([^'"]+)['"]""", text, re.M)
    outputs = re.findall(
        r"export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var)\s+([\w$]+)", text)
    for blob in re.findall(r"export\s*\{([^}]+)\}", text) + \
            re.findall(r"module\.exports\s*=\s*\{([^}]+)\}", text):
        outputs += [p.split(":")[0].strip() for p in blob.split(",") if p.strip()]
    outputs += re.findall(r"(?:module\.)?exports\.([\w$]+)\s*=", text)
    m = re.search(r"module\.exports\s*=\s*([\w$]+)\s*;", text)
    if m:
        outputs.append(m.group(1))
    return inputs, outputs


def analyze_go(text):
    # 只在 import 语句/块内取路径,避免把 case "x/y" 等普通字符串当依赖
    inputs = re.findall(r'^import\s+(?:\w+\s+)?"([\w./-]+)"', text, re.M)
    for block in re.findall(r"^import\s*\(([^)]*)\)", text, re.M | re.S):
        inputs += re.findall(r'"([\w./-]+)"', block)
    outputs = re.findall(r"^func\s+(?:\([^)]*\)\s+)?([A-Z]\w*)", text, re.M)
    outputs += re.findall(r"^type\s+([A-Z]\w*)", text, re.M)
    return inputs, outputs


def analyze_rust(text):
    inputs = re.findall(r"^\s*use\s+([\w:]+)", text, re.M)
    outputs = re.findall(
        r"^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:fn|struct|enum|trait|mod|const|static)\s+(\w+)",
        text, re.M)
    return inputs, outputs


def analyze_java(text):
    # 分号可选:Kotlin 的 import 没有分号
    inputs = re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+)", text, re.M)
    outputs = re.findall(
        r"^\s*(?:public|open)\s+(?:final\s+|abstract\s+|data\s+)?(?:class|interface|enum|record|object)\s+(\w+)",
        text, re.M)
    return inputs, outputs


def analyze_csharp(text):
    # C# 用 using 而非 import(外部评审指出)
    inputs = re.findall(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", text, re.M)
    outputs = re.findall(
        r"^\s*(?:public|internal)\s+(?:static\s+|abstract\s+|sealed\s+|partial\s+|readonly\s+)*"
        r"(?:class|interface|enum|struct|record)\s+(\w+)",
        text, re.M)
    return inputs, outputs


def analyze_c(text):
    """C / C++ / Objective-C:#include 与 #import;导出对 C 系语言静态难判,仅取 ObjC 接口。"""
    inputs = re.findall(r'^\s*#\s*(?:include|import)\s*[<"]([^">]+)[">]', text, re.M)
    outputs = re.findall(r"^\s*@interface\s+(\w+)", text, re.M)
    return inputs, outputs


def analyze_ruby(text):
    inputs = re.findall(r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]""", text, re.M)
    outputs = re.findall(r"^\s*(?:class|module)\s+([A-Z]\w*)", text, re.M)
    outputs += re.findall(r"^def\s+(?:self\.)?(\w+)", text, re.M)
    return inputs, outputs


def analyze_php(text):
    # use function Foo\bar; / use const X; 应取符号名而非关键字
    inputs = re.findall(r"^\s*use\s+(?:function\s+|const\s+)?([\w\\]+)", text, re.M)
    inputs = [i for i in inputs if i not in ("function", "const")]
    inputs += re.findall(r"""(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+)['"]""", text)
    outputs = re.findall(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+(\w+)", text, re.M)
    outputs += re.findall(r"^function\s+(\w+)", text, re.M)
    return inputs, outputs


def analyze_swift(text):
    # import class Module.Symbol 等带种类关键字的导入应取模块名
    inputs = re.findall(
        r"^\s*import\s+(?:(?:class|struct|enum|protocol|func|var|let|typealias)\s+)?([\w.]+)",
        text, re.M)
    outputs = re.findall(
        r"^(?:public\s+|open\s+|final\s+)*(?:func|class|struct|enum|protocol|extension)\s+([\w.]+)",
        text, re.M)
    return inputs, outputs


def analyze_shell(text):
    inputs = re.findall(r"^\s*(?:source|\.)\s+(\S+)", text, re.M)
    outputs = re.findall(r"^(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{", text, re.M)
    # bash 的 function myfunc { 语法(无括号)
    outputs += re.findall(r"^function\s+([A-Za-z_]\w*)\s*\{", text, re.M)
    return inputs, outputs


def analyze_scala(text):
    inputs = re.findall(r"^\s*import\s+([\w.]+)", text, re.M)
    outputs = re.findall(r"^(?:case\s+)?(?:class|object|trait)\s+(\w+)", text, re.M)
    return inputs, outputs


def analyze_lua(text):
    inputs = re.findall(r"""require\s*\(?\s*['"]([\w./-]+)['"]""", text)
    outputs = re.findall(r"^function\s+([\w.:]+)", text, re.M)
    return inputs, outputs


def analyze_generic(text):
    return [], []


ANALYZERS = {
    ".py": analyze_python,
    ".js": analyze_js, ".ts": analyze_js, ".jsx": analyze_js, ".tsx": analyze_js,
    ".mjs": analyze_js, ".cjs": analyze_js, ".vue": analyze_js, ".svelte": analyze_js,
    ".go": analyze_go,
    ".rs": analyze_rust,
    ".java": analyze_java, ".kt": analyze_java,
    ".cs": analyze_csharp,
    ".c": analyze_c, ".h": analyze_c, ".cpp": analyze_c, ".hpp": analyze_c,
    ".cc": analyze_c, ".cxx": analyze_c, ".c++": analyze_c,
    ".m": analyze_c, ".mm": analyze_c,
    ".rb": analyze_ruby,
    ".php": analyze_php,
    ".swift": analyze_swift,
    ".sh": analyze_shell, ".bash": analyze_shell, ".zsh": analyze_shell,
    ".scala": analyze_scala,
    ".lua": analyze_lua,
}


def dedupe(items):
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out[:MAX_ITEMS]


def analyze_file(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return [], []
    inputs, outputs = ANALYZERS.get(ext, analyze_generic)(text)
    return dedupe(inputs), dedupe(outputs)


# ---------------- L3 头生成与插入 ----------------

def header_lines(inputs, outputs):
    return [
        "[INPUT]: 依赖 %s" % (", ".join(inputs) if inputs else "(未检出外部依赖)"),
        "[OUTPUT]: 提供 %s" % (", ".join(outputs) if outputs else "TODO(语义):对外提供的能力"),
        "[POS]: %s" % TODO_POS,
        "[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md",
    ]


def render_header(ext, lines, has_docstring):
    if ext == ".py" and not has_docstring:
        return '"""\n' + "\n".join(lines) + '\n"""\n'
    if ext in (".py", ".rb", ".sh", ".bash", ".zsh"):
        return "".join("# %s\n" % l for l in lines)
    if ext == ".rs":
        return "".join("//! %s\n" % l for l in lines)
    if ext == ".go":
        return "".join("// %s\n" % l for l in lines)
    if ext == ".lua":
        return "".join("-- %s\n" % l for l in lines)
    if ext in (".vue", ".svelte"):
        return "<!--\n" + "\n".join(lines) + "\n-->\n"
    # 默认块注释:js/ts/java/kt/cs/swift/c/cpp/php/scala 等
    return "/**\n" + "".join(" * %s\n" % l for l in lines) + " */\n"


def insert_header(path, header_text):
    # newline="" 读写,保持文件原有行尾(CRLF 文件不被静默改为 LF)
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        src = f.read()
    if "\r\n" in src:
        header_text = header_text.replace("\n", "\r\n")
    src_lines = src.splitlines(keepends=True)
    idx = 0
    # 跳过 shebang、Python 编码声明、PHP 起始标签
    if src_lines and src_lines[0].startswith("#!"):
        idx = 1
    if idx < len(src_lines) and re.match(r"#.*coding[:=]", src_lines[idx]):
        idx += 1
    if idx < len(src_lines) and src_lines[idx].lstrip().startswith("<?php"):
        idx += 1
    new = src_lines[:idx] + [header_text] + src_lines[idx:]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(new))


def has_module_docstring(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
        return ast.get_docstring(tree) is not None
    except Exception:  # noqa: BLE001
        return True  # 解析失败时保守处理,用 # 注释


# ---------------- L2 / L1 草稿 ----------------

def render_l2(rel_dir, files, analyses, root, abs_dir):
    parent_rel = os.path.relpath(root, abs_dir).replace(os.sep, "/")
    rows = "\n".join(
        "| %s | TODO(语义):职责 | %s |"
        % (f, ", ".join(analyses[f][1]) if analyses[f][1] else "—")
        for f in files
    )
    return """# %s/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[%s/PROJECT_INDEX.md](%s/PROJECT_INDEX.md)

## 模块定位
%s

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
%s
""" % (rel_dir.replace(os.sep, "/"), parent_rel, parent_rel, TODO_MODULE, rows)


def mermaid_edges(root, dir_map, analyses_by_file):
    """从 import 关系推顶级目录间依赖边(best-effort)。"""
    top = lambda rel: "root" if rel == "." else rel.replace(os.sep, "/").split("/")[0]  # noqa: E731
    top_dirs = {top(d) for d in dir_map}
    edges = set()
    for (rel_dir, fname), (inputs, _o) in analyses_by_file.items():
        src = top(rel_dir)
        for imp in inputs:
            dst = None
            if imp.startswith("./") or imp.startswith("../"):
                # JS 风格相对路径导入
                base = os.path.normpath(os.path.join(rel_dir, imp))
                dst = top(base)
            elif imp.startswith("."):
                # Python 风格相对导入:.mod / ..pkg.mod(点数=层级)
                n = len(imp) - len(imp.lstrip("."))
                rest = imp.lstrip(".").replace(".", "/")
                base = rel_dir
                for _ in range(n - 1):
                    base = os.path.dirname(base) or "."
                dst = top(os.path.normpath(os.path.join(base, rest)))
            else:  # 绝对导入:首段命中顶级目录名即记边
                first = re.split(r"[./:]", imp)[0]
                if first in top_dirs:
                    dst = first
            if dst and dst in top_dirs and dst != src:
                edges.add((src, dst))
    return sorted(edges)


def render_l1(root, dir_map, analyses_by_file):
    project = os.path.basename(os.path.abspath(root))
    # 目录树
    tree = ["%s/" % project]
    for rel in sorted(d for d in dir_map if d != "."):
        depth = rel.count(os.sep)
        tree.append("%s├── %s/  # TODO(语义):一句话职责 → %s/FOLDER_INDEX.md"
                    % ("│   " * depth, os.path.basename(rel), rel.replace(os.sep, "/")))
    # 根目录文件表
    root_rows = "\n".join(
        "| %s | TODO(语义):职责 | %s |"
        % (f, ", ".join(analyses_by_file[(".", f)][1]) or "—")
        for f in dir_map.get(".", [])
    ) or "| (无) | | |"
    edges = mermaid_edges(root, dir_map, analyses_by_file)
    mermaid = "\n".join("    %s --> %s" % e for e in edges) or "    %% TODO(语义):补充模块依赖关系"
    n_files = sum(len(v) for v in dir_map.values())
    return """# %s — 项目索引(L1)

> 本文件是项目的语义相入口。架构变更(模块增删、依赖关系变化、技术栈调整)后必须更新本文件。

## 定位
%s

## 技术栈
TODO(语义):语言 / 框架 / 关键依赖 / 运行方式(代码文件共 %d 个)

## 目录结构
```text
%s
```

## 模块依赖关系
```mermaid
graph TD
%s
```

## 根目录文件
| 文件 | 职责 | 关键导出 |
|------|------|----------|
%s

## 全局约定
TODO(语义):错误处理方式、命名约定、其他全项目法则
""" % (project, TODO_PROJECT, n_files, "\n".join(tree), mermaid, root_rows)


# ---------------- 主流程 ----------------

def main():
    parser = argparse.ArgumentParser(description="GEB 确定性脚手架:生成 L3/L2/L1 骨架草稿")
    parser.add_argument("root", help="项目根目录")
    parser.add_argument("--dry-run", action="store_true", help="只报告将做什么,不写文件")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误: %s 不是目录" % root, file=sys.stderr)
        return 2

    dir_map = walk_project(root)
    analyses_by_file = {}
    for rel_dir, files in dir_map.items():
        for f in files:
            analyses_by_file[(rel_dir, f)] = analyze_file(os.path.join(root, rel_dir, f))

    n_l3, n_l2, n_l1, skipped = 0, 0, 0, 0
    # L3
    for rel_dir, files in sorted(dir_map.items()):
        for f in files:
            fpath = os.path.join(root, rel_dir, f)
            if not check_l3(fpath):
                skipped += 1
                continue
            ext = os.path.splitext(f)[1].lower()
            inputs, outputs = analyses_by_file[(rel_dir, f)]
            text = render_header(ext, header_lines(inputs, outputs),
                                 has_module_docstring(fpath) if ext == ".py" else False)
            print("[L3] %s" % os.path.join(rel_dir, f))
            if not args.dry_run:
                insert_header(fpath, text)
            n_l3 += 1
    # L2
    for rel_dir, files in sorted(dir_map.items()):
        if rel_dir == ".":
            continue
        abs_dir = os.path.join(root, rel_dir)
        if find_index_file(abs_dir, L2_NAMES):
            continue
        analyses = {f: analyses_by_file[(rel_dir, f)] for f in files}
        print("[L2] %s/FOLDER_INDEX.md" % rel_dir)
        if not args.dry_run:
            with open(os.path.join(abs_dir, "FOLDER_INDEX.md"), "w", encoding="utf-8") as fh:
                fh.write(render_l2(rel_dir, files, analyses, root, abs_dir))
        n_l2 += 1
    # L1
    if not find_index_file(root, L1_NAMES):
        print("[L1] PROJECT_INDEX.md")
        if not args.dry_run:
            with open(os.path.join(root, "PROJECT_INDEX.md"), "w", encoding="utf-8") as fh:
                fh.write(render_l1(root, dir_map, analyses_by_file))
        n_l1 = 1

    print("\n脚手架%s:L3 头 %d 个,L2 索引 %d 个,L1 索引 %d 个(已有跳过 %d 处)。"
          % ("(dry-run)" if args.dry_run else "完成", n_l3, n_l2, n_l1, skipped))
    if n_l3 + n_l2 + n_l1:
        print("下一步:让 AI 逐个补全 TODO(语义) 占位——补全时必须真读代码;"
              "然后运行 geb_check.py 验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
