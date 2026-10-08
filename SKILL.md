---
name: fugue-docs
description: Maintain PROJECT_INDEX.md / FOLDER_INDEX.md / file-header indexes for coding agents. In Claude Code with the fugue-docs plugin hooks, routine index sync, checks and token metering run automatically, so invoke this skill only to initialize indexes, to fill semantic fields a fugue hook prompt asks for, or to answer questions about the indexes. In agents without these hooks (such as Codex), use it whenever developing code to keep indexes in sync and record token usage. Also applies when the user mentions 赋格, GEB, PROJECT_INDEX, or FOLDER_INDEX.
---

# 赋格文档

L1 `PROJECT_INDEX.md` 是项目入口,L2 `FOLDER_INDEX.md` 是模块清单,L3 是代码文件头的 `[INPUT]`、`[OUTPUT]`、`[POS]`、`[PROTOCOL]`。依赖、清单行、一致性检查和计量由程序维护;模型只写语义:`[POS]`、`[OUTPUT]` 的含义、清单职责和模块定位。`<skill-dir>` 是本文件所在目录,`<root>` 是项目根目录。

## 定位

先读 L1,再读目标目录 L2 和相关文件头,然后读代码。索引用来缩小范围,不能替代阅读要改的实现。只读审查不初始化文档,不扩大用户的编辑范围。

## 维护

- **Claude Code + 插件钩子**:改完代码不用运行任何脚本。每轮结束时,钩子补新文件的头部骨架、同步依赖与清单、记录用量。只有出现语义缺口时才会收到以“赋格:”开头的提示,按提示每项补一句;提示以外的内容不要顺带改写。
- **没有钩子的工具(Codex 等)**:按 [references/manual-workflow.md](references/manual-workflow.md) 手动运行同步、检查和计量。

## 初始化(项目还没有索引)

1. `python3 <skill-dir>/scripts/geb_arch.py <root>` 生成架构事实与候选;未解析项需要核对,分数是启发式权重,不是正确概率。
2. `python3 <skill-dir>/scripts/geb_scaffold.py <root> --dry-run` 查看范围,再去掉 `--dry-run` 生成骨架。大型存量项目分模块迁移,如实报告覆盖范围。
3. 读代码后自底向上补齐 L3、L2、L1 的语义占位。小项目用 L1 + L3。生成代码、依赖、构建产物和纯配置不加 L3。
4. `python3 <skill-dir>/scripts/geb_check.py <root> --strict --complete --report` 验证。模板与协议细节见 [references/templates.md](references/templates.md)、[adapters/PROTOCOL.md](adapters/PROTOCOL.md)。

## 计量

只记实际用量;没有可比对照时节省量是未知,不是 0。钩子账本默认在 `~/.claude/fugue/metrics`,用 `python3 <skill-dir>/scripts/geb_metrics.py --ledger ~/.claude/fugue/metrics report` 汇总。口径见 [references/token-accounting.md](references/token-accounting.md)。
