# scripts/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
协议的全部可执行工具:检查、脚手架、视图同步、适配、硬约束。零第三方依赖,均可脱离本仓库单独分发使用。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| geb_adapt.py | 万模通用适配器:把 adapters/PROTOCOL 注入各工具规则文件,安装 pre-commit/CI,复制 arch/check/scaffold/sync 工具 | SCRIPT_DIR, ADAPTERS_DIR, BEGIN, END, TOOLS, PRE_COMMIT_SH, CI_YML, load_protocol(), inject(), TOOLS_L2, COPY_TOOLS, copy_tools(), install_pre_commit(), install_ci(), main() |
| geb_arch.py | 架构事实与候选生成器:从静态分析事实生成入口、模块角色、依赖边、风险提示与 AI handoff brief | ROLE_RULES, ENTRYPOINT_NAMES, LEGACY_WORDS, norm(), top_module(), module_path(), ext_counts(), has_legacy_marker(), load_project(), dependency_edges(), manifests(), role_by_name(), role_by_edges(), find_entrypoints(), build_modules(), find_cycles(), project_warnings(), build_report(), render_markdown(), main() |
| geb_check.py | 同构性检查器:结构/路径级清单对账/--strict 漂移/--complete 占位三层;--if-adopted 采纳判定唯一事实源;--report 回环行;--emit-facts 机器事实源;小项目 profile 与子项目递归 | CODE_EXTENSIONS, EXCLUDED_DIRS, EXCLUDED_FILE_PATTERNS, L1_NAMES, L2_NAMES, L3_TAGS, L3_SCAN_LINES, SMALL_PROJECT_FILE_LIMIT, SMALL_PROJECT_DIR_LIMIT, is_small_project(), is_code_file(), walk_project(), check_l3(), GEB_MARKERS, TODO_MARKERS, find_index_file(), _FILE_REF_PATTERN, normalize_ref(), extract_reference_sets(), extract_referenced_files(), rel_code_path(), run_l1_table_ghost_checks(), head_text(), run_strict_checks(), run_checks(), emit_facts(), main() |
| geb_facts.py | 共享依赖解析:解析实际扫描目标并保留文件级证据与未解析导入 | normalized(), top_module(), dependency_facts() |
| geb_metrics.py | Codex 任务 token 快照与跨项目本地账本;可比对照后才计算差值 | COUNTERS, now(), codex_home(), default_ledger(), read_json(), save_json(), find_session(), session_snapshot(), usage_delta(), git_state(), record_path(), start_run(), finish_run(), compare_records(), summarize(), main() |
| geb_scaffold.py | 确定性脚手架:静态分析生成 L3/L2/L1 骨架,语义留 TODO | _TODO, TODO_POS, TODO_MODULE, TODO_PROJECT, analyze_python(), analyze_js(), analyze_go(), analyze_rust(), analyze_java(), analyze_csharp(), analyze_c(), analyze_ruby(), analyze_php(), analyze_swift(), analyze_shell(), analyze_scala(), analyze_lua(), analyze_generic(), ANALYZERS, dedupe(), analyze_file(), header_lines(), render_header(), insert_header(), has_module_docstring(), render_l2(), mermaid_edges(), render_l1(), main() |
| geb_staged.py | 暂存快照校验器:验证实际提交内容并保留工作区状态 | check_staged(), main() |
| geb_stop_hook.py | Claude Code Stop 钩子:项目不同构时阻止收工 | MAX_LISTED, main() |
| geb_sync.py | 视图同步器:重写 L3 [INPUT] 行与 L2/L1 清单表(语义列保留);--graph 重绘依赖图;--changed git 增量并覆盖删除/重命名目录;小项目整表模式;递归进子项目 | _TODO, INPUT_TAG, read_keepnl(), write_keepnl(), sync_l3_input(), TABLE_HEADINGS, _ROW, row_path(), rebuild_table(), rebuild_graph(), git_changed(), sync(), main() |
| git-pre-commit-hook.sh | git 提交钩(模板):不同构时拒绝提交,跨工具硬约束 | — |
