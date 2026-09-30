#!/usr/bin/env python3
"""
[INPUT]: 依赖 os, re
[OUTPUT]: 提供共享依赖解析与文件级证据,保留未解析导入
[POS]: fugue-docs 工具层-架构生成与文档依赖图共享的事实解析
[PROTOCOL]: 变更时同步 scripts/FOLDER_INDEX.md 并运行边界测试
"""

import os
import re


def normalized(path):
    return os.path.normpath(path).replace(os.sep, "/")


def top_module(directory):
    return "root" if directory == "." else normalized(directory).split("/")[0]


def dependency_facts(dir_map, analyses):
    """Resolve only scanned targets. Unresolved is not evidence of no dependency."""
    files = {normalized(os.path.join(d, f)) for d, names in dir_map.items() for f in names}
    directories = {normalized(d) for d in dir_map}
    for path in files:
        parent = os.path.dirname(path)
        while parent:
            directories.add(parent)
            parent = os.path.dirname(parent)
    suffixes = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")

    def target(path):
        path = normalized(path)
        if path.startswith("../") or os.path.isabs(path):
            return None
        if path in files:
            return path
        for suffix in suffixes:
            if path + suffix in files:
                return path + suffix
        for stem in ("__init__.py", "index.js", "index.ts", "index.tsx"):
            candidate = normalized(os.path.join(path, stem))
            if candidate in files:
                return candidate
        if path in directories:
            return path + "/"
        return None

    edges, resolved, unresolved = set(), [], []
    for (directory, name), (imports, _exports) in sorted(analyses.items()):
        source = normalized(os.path.join(directory, name))
        ext = os.path.splitext(name)[1].lower()
        for imp in imports:
            candidates = []
            if ext == ".py":
                if imp.startswith("."):
                    levels = len(imp) - len(imp.lstrip("."))
                    base = directory
                    for _ in range(levels - 1):
                        base = os.path.dirname(base) or "."
                    candidates.append(os.path.join(base, imp[levels:].replace(".", "/")))
                else:
                    path = imp.replace(".", "/")
                    candidates.extend([path, os.path.join(directory, path)])
            elif imp.startswith(("./", "../")):
                candidates.append(os.path.join(directory, imp))
            elif ext == ".rs" and imp.startswith("crate::"):
                candidates.append(imp[len("crate::"):].replace("::", "/"))
            elif ext not in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"):
                candidates.append(re.split(r"::", imp)[0].replace(".", "/"))
            destination = next((found for candidate in candidates
                                for found in [target(candidate)] if found), None)
            if destination is None:
                unresolved.append({"file": source, "import": imp,
                                   "reason": "external_or_unresolved"})
                continue
            dst_dir = destination.rstrip("/") if destination.endswith("/") else os.path.dirname(destination) or "."
            src, dst = top_module(directory), top_module(dst_dir)
            resolved.append({"file": source, "import": imp, "target": destination,
                             "from": src, "to": dst})
            if src != dst:
                edges.add((src, dst))
    return {"edges": sorted(edges), "resolved": resolved, "unresolved": unresolved}
