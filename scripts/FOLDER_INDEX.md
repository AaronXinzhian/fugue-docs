# scripts/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
协议的全部可执行工具:检查、脚手架、适配、硬约束。零第三方依赖,均可脱离本仓库单独分发使用。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| geb_adapt.py | 万模通用适配器:把 adapters/PROTOCOL 注入各工具规则文件,安装 pre-commit/CI | SCRIPT_DIR, ADAPTERS_DIR, BEGIN, END, TOOLS, PRE_COMMIT_SH, CI_YML, load_protocol(), inject(), TOOLS_L2 |
| geb_check.py | 同构性检查器:结构层 + --strict 漂移层 + --complete 占位清零;--if-adopted 是采纳判定的唯一事实源;子项目递归检查 | CODE_EXTENSIONS, EXCLUDED_DIRS, EXCLUDED_FILE_PATTERNS, L1_NAMES, L2_NAMES, L3_TAGS, L3_SCAN_LINES, TINY_PROJECT_FILE_LIMIT, is_code_file(), walk_project() |
| geb_scaffold.py | 确定性脚手架:静态分析生成 L3/L2/L1 骨架,语义留 TODO | _TODO, TODO_POS, TODO_MODULE, TODO_PROJECT, MAX_ITEMS, analyze_python(), analyze_js(), analyze_go(), analyze_rust(), analyze_java() |
| geb_stop_hook.py | Claude Code Stop 钩子:项目不同构时阻止收工 | MAX_LISTED, main() |
| geb_sync.py | 视图同步器:重写 L3 [INPUT] 行与 L2/L1 清单表(语义列保留),--graph 重绘依赖图;递归进子项目 | _TODO, INPUT_TAG, read_keepnl(), write_keepnl(), sync_l3_input(), TABLE_HEADINGS, _ROW, rebuild_table(), rebuild_graph(), sync() |
| git-pre-commit-hook.sh | git 提交钩(模板):不同构时拒绝提交,跨工具硬约束 | — |
