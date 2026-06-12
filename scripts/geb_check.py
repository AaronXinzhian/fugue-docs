#!/usr/bin/env python3
"""
[INPUT]: 依赖 argparse, json, os, re, sys
[OUTPUT]: 提供 GEB 同构性检查命令行工具(默认结构层检查 + --strict 语义漂移检查);退出码 0=同构, 1=存在违规
[POS]: GEB 协议工具层-一致性验证器
[PROTOCOL]: 变更时更新此头部,然后检查上级 FOLDER_INDEX.md 与 SKILL.md 中对本脚本的描述
"""

import argparse
import json
import os
import re
import sys

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs",
    ".rb", ".php", ".sh", ".bash", ".zsh", ".lua",
    ".scala", ".m", ".mm", ".vue", ".svelte",
    ".cxx", ".c++",
}

EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "target",
    "__pycache__", ".venv", "venv", "env", ".next", ".nuxt",
    "vendor", "coverage", ".idea", ".vscode", "migrations",
    ".pytest_cache", ".mypy_cache", "site-packages",
    # 测试夹具是样本数据而非系统的一部分,不参与同构要求
    "fixtures", "__fixtures__", "testdata",
}

EXCLUDED_FILE_PATTERNS = [
    re.compile(r"\.min\.(js|css)$"),
    re.compile(r"\.d\.ts$"),
    re.compile(r"(^|/)conftest\.py$"),
]

L1_NAMES = ("PROJECT_INDEX.md", "CLAUDE.md")
L2_NAMES = ("FOLDER_INDEX.md", "CLAUDE.md")
L3_TAGS = ("[INPUT]", "[OUTPUT]", "[POS]")
L3_SCAN_LINES = 50  # L3 头必须出现在文件前 50 行内

# 规模弹性(默认 profile 的伸缩):代码文件 ≤ 20、一级代码目录 ≤ 5 且无嵌套时,
# L2 可省略——清单义务转嫁给 L1。层数由复杂度决定,而非教条。
SMALL_PROJECT_FILE_LIMIT = 20
SMALL_PROJECT_DIR_LIMIT = 5


def is_small_project(dir_map):
    files = sum(len(v) for v in dir_map.values())
    non_root = [d for d in dir_map if d != "."]
    return (files <= SMALL_PROJECT_FILE_LIMIT
            and len(non_root) <= SMALL_PROJECT_DIR_LIMIT
            and all(os.sep not in d and "/" not in d for d in non_root))


def is_code_file(path):
    if os.path.splitext(path)[1].lower() not in CODE_EXTENSIONS:
        return False
    rel = path.replace(os.sep, "/")
    return not any(p.search(rel) for p in EXCLUDED_FILE_PATTERNS)


def walk_project(root, subprojects=None):
    """返回 {相对目录路径: [代码文件名]},仅含至少有一个代码文件的目录。

    递归分形:子目录若含 PROJECT_INDEX.md,即是一个子项目(它的 L1 同时充当
    父项目视角下的 L2)。父项目的遍历在子项目边界剪枝——子项目内部文件不计入
    父项目的 L2/L3 义务,由子项目自查。subprojects 传入 list 时收集其相对路径。
    """
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel != "." and os.path.isfile(os.path.join(dirpath, "PROJECT_INDEX.md")):
            if subprojects is not None:
                subprojects.append(rel)
            dirnames[:] = []  # 子项目内部不归父项目管
            continue
        dirnames[:] = sorted(
            d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")
        )
        code_files = sorted(f for f in filenames if is_code_file(f))
        if code_files:
            result[rel] = code_files
    return result


def check_l3(filepath):
    """检查文件前 L3_SCAN_LINES 行是否含全部 L3 标签,返回缺失标签列表。"""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            head = "".join(f.readline() for _ in range(L3_SCAN_LINES))
    except OSError:
        return list(L3_TAGS)
    return [t for t in L3_TAGS if t not in head]


# CLAUDE.md 被认作索引所需的协议标识(任一命中即可)。
# 必须是强信号:裸 "GEB" 三个字母曾导致"散文里提了一句这本书"的项目被误判采纳
# (外部评审两次抓到),故仅认协议专名与协议产物名。
GEB_MARKERS = (
    "[PROTOCOL]", "PROJECT_INDEX", "FOLDER_INDEX",
    "GEB 协议", "GEB 分形", "GEB Protocol", "GEB-PROTOCOL",
)

# 语义占位标记:脚手架生成、应由 AI/人补全;--complete 模式下残留即违规
# (拆开拼接,避免本文件自己的头部 50 行被自己的检查误判)
TODO_MARKERS = ("TODO(" + "语义)", "TODO(" + "semantic)")


def find_index_file(dirpath, names):
    """寻找该目录的索引文件。

    CLAUDE.md 是 AI 工具的通用指令文件,内容可能与本协议毫无关系;只有当它
    包含 GEB 协议标识时才被认作索引——否则一份普通散文 CLAUDE.md 就能
    "形式上采纳"协议而绕过结构检查(外部评审指出的漏洞)。
    """
    for name in names:
        candidate = os.path.join(dirpath, name)
        if not (os.path.isfile(candidate) and os.path.getsize(candidate) > 0):
            continue
        if name == "CLAUDE.md":
            with open(candidate, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if not any(m in text for m in GEB_MARKERS):
                continue
        return candidate
    return None


_FILENAME_PATTERN = re.compile(
    r"\b[\w][\w.\-]*\.(?:%s)(?!\w)"
    % "|".join(re.escape(ext.lstrip(".")) for ext in sorted(CODE_EXTENSIONS))
)


def extract_referenced_files(index_text):
    """提取索引中的代码文件名引用。

    返回 (anywhere, in_tables):
    - anywhere: 全文中出现的文件名(用于"缺漏条目"宽松判定——提到了就算)
    - in_tables: 仅 Markdown 表格行中出现的文件名(用于"幽灵条目"判定——
      散文中合法地提及其他目录的文件,如"被 app.py 调用",不应误报)
    """
    anywhere = {m.group(0) for m in _FILENAME_PATTERN.finditer(index_text)}
    table_lines = "\n".join(
        line for line in index_text.splitlines() if line.lstrip().startswith("|")
    )
    in_tables = {m.group(0) for m in _FILENAME_PATTERN.finditer(table_lines)}
    return anywhere, in_tables


def head_text(filepath):
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return "".join(f.readline() for _ in range(L3_SCAN_LINES))
    except OSError:
        return ""


def run_strict_checks(root, dir_map, l1_path):
    """语义漂移检查(--strict):保守启发式,宁可漏报不可误报。

    1) L1 目录树应提及每个含代码的顶级目录——目录改名/新增后 L1 没跟上是常见漂移。
    2) L3 [INPUT] 应提及文件实际 import 的项目内部模块——依赖变了头部没改是另一种漂移。
       匹配按 token 宽松进行(路径任一段出现在头部即算提及)。
    """
    violations = []
    top_dirs = sorted({d.replace(os.sep, "/").split("/")[0]
                       for d in dir_map if d != "."})
    # 根目录的单文件模块(如 utils.py → "utils")同样是项目内部依赖
    root_modules = {os.path.splitext(f)[0] for f in dir_map.get(".", [])}
    if l1_path:
        with open(l1_path, encoding="utf-8", errors="replace") as f:
            l1_text = f.read()
        for d in top_dirs:
            if d not in l1_text:
                violations.append({
                    "level": "L1", "path": d,
                    "problem": "L1 未提及顶级代码目录 %s(strict)" % d,
                })
    try:
        import geb_scaffold  # 延迟导入,避免与 geb_scaffold 的顶层互相导入冲突
    except ImportError:
        print("警告: 找不到 geb_scaffold,--strict 的 [INPUT] 漂移检查已跳过"
              "(请把两个脚本放在同一目录)", file=sys.stderr)
        return violations
    for rel_dir, files in sorted(dir_map.items()):
        for f_name in files:
            fpath = os.path.join(root, rel_dir, f_name)
            head = head_text(fpath)
            if "[INPUT]" not in head:
                continue  # 结构层已另行报缺
            # 只在 [INPUT] 声明段内匹配——不能让代码里的 import 语句自己满足检查
            idx_in, idx_out = head.find("[INPUT]"), head.find("[OUTPUT]")
            if 0 <= idx_in < idx_out:
                input_segment = head[idx_in:idx_out]
            else:
                input_segment = next(
                    (l for l in head.splitlines() if "[INPUT]" in l), "")
            imports, _outputs = geb_scaffold.analyze_file(fpath)
            for imp in imports:
                tokens = [t for t in re.split(r"[./:\\]+", imp) if t]
                # 仅核查项目内部依赖:相对导入,或首段命中顶级目录/根模块名
                internal = imp.startswith(".") or (
                    tokens and (tokens[0] in top_dirs or tokens[0] in root_modules)
                )
                if not internal or not tokens:
                    continue
                if not any(t in input_segment for t in tokens):
                    violations.append({
                        "level": "L3",
                        "path": os.path.normpath(os.path.join(rel_dir, f_name)),
                        "problem": "[INPUT] 未提及实际依赖 %s(strict)" % imp,
                    })
    return violations


def run_checks(root, strict=False, complete=False, recursive=True):
    violations = []  # 每项: {"level", "path", "problem"}
    subprojects = []
    dir_map = walk_project(root, subprojects)
    all_code_files = [
        (d, f) for d, files in dir_map.items() for f in files
    ]

    # --- L1 ---
    l1_path = find_index_file(root, L1_NAMES)
    if not l1_path:
        violations.append({
            "level": "L1", "path": ".",
            "problem": "根目录缺少 PROJECT_INDEX.md(或 CLAUDE.md)项目索引",
        })

    # --- 规模弹性判定 ---
    small = is_small_project(dir_map)

    # --- L2 + 清单对账 ---
    for rel_dir, code_files in sorted(dir_map.items()):
        abs_dir = os.path.join(root, rel_dir)
        own_l2 = None
        if rel_dir == ".":
            index_path = l1_path  # 根目录代码文件记录在 L1 中
        else:
            own_l2 = find_index_file(abs_dir, L2_NAMES)
            if own_l2:
                index_path = own_l2  # 有 L2 优先用 L2(即便是小项目)
            elif small:
                index_path = l1_path  # 小项目免 L2,清单义务转嫁给 L1
            else:
                violations.append({
                    "level": "L2", "path": rel_dir,
                    "problem": "缺少 FOLDER_INDEX.md 文件夹索引",
                })
                continue
        if not index_path:
            continue
        with open(index_path, encoding="utf-8", errors="replace") as f:
            index_text = f.read()
        referenced_anywhere, referenced_in_tables = extract_referenced_files(
            index_text
        )
        own_name = os.path.basename(index_path)
        # 缺漏:实际存在但索引未提及
        for f_name in code_files:
            if f_name not in referenced_anywhere:
                violations.append({
                    "level": "L2",
                    "path": os.path.normpath(os.path.join(rel_dir, f_name)),
                    "problem": "索引 %s 未列出该文件(缺漏条目)" % own_name,
                })
        # 幽灵:清单表格提及但文件不存在(仅当用的是本目录自己的 L2 时核对——
        # 回落到 L1 时,L1 表格里合法引用着其他目录的文件,不能误判)
        if own_l2:
            for ref in sorted(referenced_in_tables):
                if ref != own_name and not os.path.exists(
                    os.path.join(abs_dir, ref)
                ):
                    violations.append({
                        "level": "L2", "path": os.path.join(rel_dir, ref),
                        "problem": "索引 %s 引用了不存在的文件(幽灵条目)" % own_name,
                    })

    # --- L3 ---
    for rel_dir, code_files in sorted(dir_map.items()):
        for f_name in code_files:
            rel_file = os.path.normpath(os.path.join(rel_dir, f_name))
            missing = check_l3(os.path.join(root, rel_file))
            if missing:
                violations.append({
                    "level": "L3", "path": rel_file,
                    "problem": "文件头缺少标签: %s" % ", ".join(missing),
                })

    if strict:
        violations += run_strict_checks(root, dir_map, l1_path)
        # 父项目的 L1 必须提及每个子项目(子项目对父级而言就是一个模块)
        if l1_path and subprojects:
            with open(l1_path, encoding="utf-8", errors="replace") as f:
                l1_text = f.read()
            for sp in subprojects:
                name = os.path.basename(sp)
                if name not in l1_text:
                    violations.append({
                        "level": "L1", "path": sp,
                        "problem": "L1 未提及子项目 %s(strict)" % sp,
                    })

    if complete:
        # 初始化完成度:任何头部或索引里残留的 TODO 占位都是"半成品同构"
        index_files = {p for p in (l1_path,) if p}
        for rel_dir in dir_map:
            idx = find_index_file(os.path.join(root, rel_dir), L2_NAMES)
            if idx:
                index_files.add(idx)
        for idx in sorted(index_files):
            with open(idx, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if any(m in text for m in TODO_MARKERS):
                violations.append({
                    "level": "L1" if idx == l1_path else "L2",
                    "path": os.path.relpath(idx, root),
                    "problem": "索引中残留 TODO(语义) 占位,初始化未完成(complete)",
                })
        for rel_dir, files in sorted(dir_map.items()):
            for f_name in files:
                head = head_text(os.path.join(root, rel_dir, f_name))
                if any(m in head for m in TODO_MARKERS):
                    violations.append({
                        "level": "L3",
                        "path": os.path.normpath(os.path.join(rel_dir, f_name)),
                        "problem": "文件头残留 TODO(语义) 占位,初始化未完成(complete)",
                    })

    # 递归分形:子项目按同一套规则独立检查,违规带路径前缀汇入父报告
    n_sub_files, n_sub_dirs = 0, 0
    if recursive:
        for sp in subprojects:
            sub_v, sub_stats = run_checks(
                os.path.join(root, sp), strict=strict, complete=complete)
            for v in sub_v:
                v["path"] = sp if v["path"] == "." else os.path.join(sp, v["path"])
            violations += sub_v
            n_sub_files += sub_stats["code_files"]
            n_sub_dirs += sub_stats["code_dirs"]

    stats = {
        "code_files": len(all_code_files) + n_sub_files,
        "code_dirs": len(dir_map) + n_sub_dirs,
        "subprojects": len(subprojects),
        "violations": len(violations),
        "l2_waived_small_project": small,
        "strict": strict,
    }
    return violations, stats


def emit_facts(root, out_path):
    """机器事实源:扫描结果落为结构化 JSON,供 LLM/工具消费,免得各处重复推导。"""
    import geb_scaffold
    subprojects = []
    dir_map = walk_project(root, subprojects)
    files = {}
    analyses_by_file = {}
    for d, names in dir_map.items():
        for f in names:
            rel = os.path.normpath(os.path.join(d, f))
            imports, exports = geb_scaffold.analyze_file(os.path.join(root, rel))
            analyses_by_file[(d, f)] = (imports, exports)
            files[rel.replace(os.sep, "/")] = {"imports": imports, "exports": exports}
    manifests = [m for m in (
        "package.json", "pyproject.toml", "requirements.txt", "go.mod",
        "Cargo.toml", "pom.xml", "build.gradle", "Gemfile", "composer.json",
        "Makefile",
    ) if os.path.isfile(os.path.join(root, m))]
    langs = {}
    for rel in files:
        ext = os.path.splitext(rel)[1]
        langs[ext] = langs.get(ext, 0) + 1
    facts = {
        "root": os.path.basename(root),
        "dirs": sorted(d.replace(os.sep, "/") for d in dir_map),
        "subprojects": sorted(s.replace(os.sep, "/") for s in subprojects),
        "files": files,
        "edges": ["%s -> %s" % e for e in
                  geb_scaffold.mermaid_edges(root, dir_map, analyses_by_file)],
        "manifests": manifests,
        "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
        "small_project": is_small_project(dir_map),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    return facts


def main():
    parser = argparse.ArgumentParser(description="GEB 分形文档同构性检查器")
    parser.add_argument("root", help="项目根目录")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--strict", action="store_true",
                        help="额外执行语义漂移检查(L1 目录提及 + L3 [INPUT] 与实际 import 对账)")
    parser.add_argument("--complete", action="store_true",
                        help="额外检查 TODO(语义) 占位是否清零(初始化完成度)")
    parser.add_argument("--if-adopted", action="store_true",
                        help="项目未采纳协议(无 L1 索引)时静默跳过并返回 0;供钩子/CI 用,采纳判定的唯一事实源")
    parser.add_argument("--report", action="store_true",
                        help="末尾追加一行机器生成的回环报告(GEB 回环:L3 ✓ | L2 ✓ | L1 ✓)")
    parser.add_argument("--emit-facts", metavar="FILE",
                        help="把扫描出的机器事实(文件/依赖/导出/边/清单)写为 JSON,供 LLM 与工具消费")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误: %s 不是目录" % root, file=sys.stderr)
        return 2

    if args.if_adopted and not find_index_file(root, L1_NAMES):
        return 0  # 未采纳,零打扰

    if args.emit_facts:
        emit_facts(root, args.emit_facts)
        print("机器事实已写入 %s" % args.emit_facts)

    violations, stats = run_checks(root, strict=args.strict, complete=args.complete)

    if args.json:
        print(json.dumps(
            {"isomorphic": not violations, "stats": stats,
             "violations": violations},
            ensure_ascii=False, indent=2,
        ))
    else:
        print("GEB 同构性检查 — %s" % root)
        line = "代码文件 %(code_files)d 个 / 代码目录 %(code_dirs)d 个" % stats
        if stats.get("subprojects"):
            line += " / 子项目 %(subprojects)d 个(递归检查)" % stats
        print(line)
        if stats["l2_waived_small_project"]:
            print("(小项目 profile:L2 可省略,清单并入 L1)")
        if not violations:
            print("✓ 两相同构,无违规。")
        else:
            print("✗ 发现 %d 处违规:" % len(violations))
            for v in violations:
                print("  [%(level)s] %(path)s — %(problem)s" % v)
    if args.report:
        marks = []
        for lvl in ("L3", "L2", "L1"):
            n = sum(1 for v in violations if v["level"] == lvl)
            marks.append("%s %s" % (lvl, "✓" if n == 0 else "✗%d" % n))
        print("GEB 回环:%s" % " | ".join(marks))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
