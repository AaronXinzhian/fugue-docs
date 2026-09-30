---
name: fugue-docs
description: Maintain project, folder, and file indexes when developing code, initializing a repository, or checking documentation drift. Use indexes to locate relevant code, generate dependency facts, synchronize L1/L2/L3, and record task token usage in Codex. Also applies when the user mentions 赋格, GEB, PROJECT_INDEX, or FOLDER_INDEX.
---

# 赋格文档

程序提取依赖和清单,AI 阅读相关代码后补充职责。L1 是项目入口 `PROJECT_INDEX.md`,L2 是模块 `FOLDER_INDEX.md`,L3 是代码文件头的 `[INPUT]`、`[OUTPUT]`、`[POS]` 与 `[PROTOCOL]`。

下列命令中的 `<skill-dir>` 是本 SKILL.md 所在目录,`<root>` 是当前项目根目录。优先调用技能内的工具,不假定用户项目已经复制了 `scripts/geb/`。

## 开始开发

1. 读取项目自身规则,再读 L1、目标目录 L2 和相关文件头定位代码。索引不能替代修改前对相关实现的阅读。
2. Codex 开发开始时运行 `python3 <skill-dir>/scripts/geb_metrics.py start <root> --task <本次任务短标识>`。保留 `run_id`,检查 `measurement_ready`;为 false 时运行 `doctor`,明确报告原因而非忽略告警。只读审查不初始化文档,不扩大用户编辑范围。
3. 项目尚无索引且当前任务允许编辑代码时,按下方初始化;已有索引按下方维护。已有 AGENTS.md、CLAUDE.md 和其他文档保留原有要求,协议段采用追加或托管块更新。

## 初始化

1. 运行 `python3 <skill-dir>/scripts/geb_arch.py <root>`。需要留档时用 `--out` 和 `--brief` 指向临时产物目录。事实中的未解析项需要核对,候选分数是启发式权重,不是正确概率。
2. 运行 `python3 <skill-dir>/scripts/geb_scaffold.py <root> --dry-run` 查看范围,再去掉 `--dry-run` 生成骨架。对大型存量项目分模块迁移并如实报告覆盖范围,避免一次任务顺带改写全部代码。
3. 阅读代码后补齐职责与语义占位,自底向上完成 L3、L2、L1。小项目自动采用 L1 + L3,已有 L2 继续维护。生成代码、依赖、构建产物和纯配置不加 L3。
4. 完整初始化用 `python3 <skill-dir>/scripts/geb_check.py <root> --strict --complete --report` 验证。需要语言模板或协议细节时读 [references/templates.md](references/templates.md) 或 [adapters/PROTOCOL.md](adapters/PROTOCOL.md)。

## 维护与收尾

1. 代码修改后运行 `python3 <skill-dir>/scripts/geb_sync.py <root> --changed`。非 Git 项目自动回退全量。首次接入或怀疑历史漂移时使用全量同步。清单职责与非代码条目由人维护;依赖和代码行集合由机器维护。
2. 检查所改文件的 `[OUTPUT]`、`[POS]` 和模块职责,结构变化时更新 L1。`--graph` 显式重绘依赖图,使用前核对是否会替换人工图。
3. 运行 `python3 <skill-dir>/scripts/geb_check.py <root> --strict --complete --report` 以及项目本身的相关测试。报告实际通过、遗留或无法验证的情况,不把结构检查当语义正确证明。
4. Codex 中运行 `python3 <skill-dir>/scripts/geb_metrics.py finish <run_id> --receipt`。最终回复附简短收益单:已测 token、记录区间耗时、验收结果及净节省(无对照为未知)。有测试日志时传 `--outcome passed|failed|partial --evidence <文件>` 绑定验收依据。计量不含之后回复与未汇总的独立子会话。

计量只记录实际用量。没有可比对照时,节省量保持 `null`,不能把缓存命中、字符压缩比或没读的文件数记成已节省 token。需要衡量收益或查看跨项目账本时读 [references/token-accounting.md](references/token-accounting.md)。

## 可选提交约束

用户要求安装提交检查或团队 CI 时运行 `python3 <skill-dir>/scripts/geb_adapt.py <root> --tool codex --pre-commit --ci`。提交钩子检查暂存快照;已有非托管钩子会保留并生成待合并旁路文件,安装输出须如实说明。普通开发不改全局 Git 配置或覆盖已有钩子。
