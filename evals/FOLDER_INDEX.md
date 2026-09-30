# evals/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
完整可复跑的评测包:测试用例定义、样例项目夹具、自动评分器。复跑方法见 [README.md](README.md)。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| grade_comprehension.py | 理解成本评分器:按 rubric 给 docs-only/code-only 答案打分,计算分数比与 token 比 | WS, DEFAULT_SPEC, read_json(), normalize(), answer_map(), has_any(), has_all(), point_passed(), grade_question(), grade_run(), condition_key(), compare_runs(), load_runs(), main() |
| grade_iteration.py | 自动评分器:对每个运行目录逐断言打分,生成 grading.json | WS, GEB_CHECK, INDEX_NAMES, L3_TAGS, read(), head_lines(), first_docstring(), find_index(), run_geb_check(), run_app(), expectation(), grade_eval0(), grade_eval1(), grade_eval2(), GRADERS, main() |
| run_regression_suite.py | 确定性回归测试套件:多轮验证架构候选、增量同步、路径级检查、适配器复制、理解评分与仓库自检 | WS, ROOT, run(), fail(), ok(), require(), copy_fixture(), test_arch_fixture_b(), test_sync_changed_delete(), test_check_l1_path_ghost(), test_adapt_copy_tools(), test_comprehension_grader(), test_self_checks(), TESTS, run_round(), git_commit(), source_digest(), run_boundaries(), main() |
| run_token_pilot.py | 隔离配置与固定任务的预算受限配对试点;保留失败、不自动声称节省 | ROOT, TASKS, summarize_trials(), cli_usage(), validate(), trial(), main() |
| test_boundaries.py | 同步写盘、路径、依赖、有向环、暂存区与评分负对照 | ROOT, header(), BoundaryTests |
| test_metrics.py | token 用量、缺失值、重置、对照证据与重复计量回归 | MetricsTests |
| test_token_pilot.py | 不调用模型的试点汇总测试:负收益、失败、未知与缓存重复计数 | PilotTests |

## 数据文件
- `token-pilot.md` — 真实仓库小任务的增量技能试点设计、预算与结论边界
- `results/2026-09-30-v2.5-regression.json` — v2.5 干净提交上的 6 组集成与 55 项回归证据
- `evals.json` — 3 个测试用例(提示词 + 断言)
- `REGRESSION_RESULTS.md` — 多轮确定性回归测试的公开结果摘要与复跑说明
- `comprehension.md` — 理解测验(度量"理解成本"这一真目标的方法与 fixture-b 标准题组)
- `comprehension_fixture_b.json` — fixture-b 理解测验的确定性关键词 rubric 与健康阈值
- `results/2026-07-08-v2.3-regression.json` — v2.3 回归套件 5 轮原始机器结果
- `results/2026-09-30-v2.4-regression.json` — v2.4 集成与边界/计量回归结果,绑定提交和源码摘要
- `fixtures/fixture-a` — 无文档的 JS 样例项目(测"初始化"场景)
- `fixtures/fixture-b` — 已有完整 GEB 结构的 Python 样例项目(测"变更回环"与"删除重构"场景)
