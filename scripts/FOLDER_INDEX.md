# scripts/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
协议的全部可执行工具:检查、脚手架、适配、硬约束。零第三方依赖,均可脱离本仓库单独分发使用。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| geb_check.py | 同构性检查器:结构层 + --strict 漂移层 + --complete 占位清零;--if-adopted 是采纳判定的唯一事实源(钩子/CI 调用) | CLI;run_checks(), walk_project(), check_l3(), find_index_file() |
| geb_scaffold.py | 确定性脚手架:静态分析生成 L3/L2/L1 骨架,语义留 TODO | CLI;analyze_file() 供 --strict 复用 |
| geb_adapt.py | 万模通用适配器:把 adapters/PROTOCOL 注入各工具规则文件,安装 pre-commit/CI | CLI;--tool/--pre-commit/--ci/--dry-run |
| geb_stop_hook.py | Claude Code Stop 钩子:项目不同构时阻止收工 | stdin JSON → {"decision":"block"} |
| git-pre-commit-hook.sh | git 提交钩(模板):不同构时拒绝提交,跨工具硬约束 | sh 脚本,复制到 .git/hooks/pre-commit |
