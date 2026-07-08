# evals/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
完整可复跑的评测包:测试用例定义、样例项目夹具、自动评分器。复跑方法见 [README.md](README.md)。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| grade_comprehension.py | 理解成本评分器:按 rubric 给 docs-only/code-only 答案打分,计算分数比与 token 比 | WS, DEFAULT_SPEC, read_json(), normalize(), answer_map(), has_any(), has_all(), point_passed(), grade_question(), grade_run() |
| grade_iteration.py | 自动评分器:对每个运行目录逐断言打分,生成 grading.json | WS, GEB_CHECK, INDEX_NAMES, L3_TAGS, read(), head_lines(), first_docstring(), find_index(), run_geb_check(), run_app() |
| run_regression_suite.py | 确定性回归测试套件:多轮验证架构候选、增量同步、路径级检查、适配器复制、理解评分与仓库自检 | WS, ROOT, run(), fail(), ok(), require(), copy_fixture(), test_arch_fixture_b(), test_sync_changed_delete(), test_check_l1_path_ghost() |

## 数据文件
- `evals.json` — 3 个测试用例(提示词 + 断言)
- `REGRESSION_RESULTS.md` — 多轮确定性回归测试的公开结果摘要与复跑说明
- `comprehension.md` — 理解测验(度量"理解成本"这一真目标的方法与 fixture-b 标准题组)
- `comprehension_fixture_b.json` — fixture-b 理解测验的确定性关键词 rubric 与健康阈值
- `results/2026-07-08-v2.3-regression.json` — v2.3 回归套件 5 轮原始机器结果
- `fixtures/fixture-a` — 无文档的 JS 样例项目(测"初始化"场景)
- `fixtures/fixture-b` — 已有完整 GEB 结构的 Python 样例项目(测"变更回环"与"删除重构"场景)
