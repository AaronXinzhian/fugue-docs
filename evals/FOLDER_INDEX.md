# evals/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
完整可复跑的评测包:测试用例定义、样例项目夹具、自动评分器。复跑方法见 [README.md](README.md)。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| analyze_navigation.py | 离线定位成本分析:解析 Codex exec 事件,统计首次修改前的命令、读文件、索引读取与工具输出,做同块配对统计与 A/A 样本量提示;不调用模型 | INDEX_NAMES, KIND_PRIORITY, READ_COMMANDS, SEARCH_COMMANDS, LIST_COMMANDS, WRAPPERS, SHELLS, VALUE_OPTIONS, HEREDOC, PATCH_PATH, load_events(), items_in_order(), item_type(), strip_heredocs(), tokenize(), shell_script(), simple_commands(), unwrap(), split_redirects(), positionals(), looks_like_path(), classify(), normalize_path(), SCRATCH_PREFIXES, SYSTEM_PREFIXES, PYTHON_WRITE, PYTHON_INLINE, is_scratch(), is_outside_workspace(), is_skill_path(), empty_bucket(), analyze_events(), nav_value(), NAV_METRICS, paired_stats(), power_hint(), sign_test(), infer_comparisons(), trial_directory(), analyze_output(), main() |
| grade_comprehension.py | 理解成本评分器:按 rubric 给 docs-only/code-only 答案打分,计算分数比与 token 比 | WS, DEFAULT_SPEC, read_json(), normalize(), answer_map(), has_any(), has_all(), point_passed(), grade_question(), grade_run(), condition_key(), compare_runs(), load_runs(), main() |
| grade_iteration.py | 自动评分器:对每个运行目录逐断言打分,生成 grading.json | WS, GEB_CHECK, INDEX_NAMES, L3_TAGS, read(), head_lines(), first_docstring(), find_index(), run_geb_check(), run_app(), expectation(), grade_eval0(), grade_eval1(), grade_eval2(), GRADERS, main() |
| run_regression_suite.py | 确定性回归测试套件:多轮验证架构候选、增量同步、路径级检查、适配器复制、理解评分与仓库自检 | WS, ROOT, run(), fail(), ok(), require(), copy_fixture(), test_arch_fixture_b(), test_sync_changed_delete(), test_check_l1_path_ghost(), test_adapt_copy_tools(), test_comprehension_grader(), test_self_checks(), TESTS, run_round(), git_commit(), source_digest(), run_boundaries(), main() |
| run_token_pilot.py | 配对块 token 试点:无索引/仅索引/完整赋格三组或 A/A 噪声设计,隐藏验收与参考补丁校验,主指标为未缓存输入+输出;保留失败、不自动声称节省 | ROOT, DEFAULT_TASKS_FILE, ARMS, DESIGNS, PRIMARY_METRIC, COST_KEYS, HEADER_LINE, EMPTY_BLOCK, INDEX_FILE_NAMES, BASE_PROMPT, PLAIN_PROMPT, design(), load_tasks(), archive_hashes(), leaked(), SKILL_PATHS, skill_archive(), build_schedule(), is_excluded(), strip_indexes(), drop_empty_blocks(), count_index_files(), git_environment(), prepare_workspace(), protected_snapshot(), expand(), validate(), cli_usage(), partial_usage(), usage_value(), token_metrics(), sum_known(), budget_tokens(), failure_aware(), summarize_trials(), run_codex(), kill_group(), build_prompt(), trial(), run_trial(), verify_tasks(), parse_weights(), main() |
| test_boundaries.py | 同步写盘、路径、依赖、有向环、暂存区与评分负对照 | ROOT, header(), BoundaryTests |
| test_metrics.py | token 用量、缺失值、重置、对照证据与重复计量回归 | MetricsTests |
| test_navigation.py | 定位分析器的命令分类、首次修改边界、旧事件格式与目录汇总回归 | command(), kinds(), ClassifierTests, AnalyzerTests |
| test_token_pilot.py | 不调用模型的试点测试:配对块、三组/A-A 汇总、成本口径、索引剥离、预算与未知停止、假执行器端到端 | TWO_ARM, HAS_SOURCE_REF, usage(), trial(), FAKE_CODEX, SummaryTests, DesignTests, RunnerTests |

## 数据文件
- `TOKEN_PILOT_RESULTS.md` — 已发生的模型预检、限时失败与暂无节省结论
- `results/2026-09-30-token-preflight.json` — medium 基线预检原始数值;无赋格配对
- `results/2026-09-30-token-pair-incomplete.json` — xhigh 基线超时记录;完整用量未知
- `token-pilot.md` — 三组配对块试点设计(索引收益/流程开销分离)、A/A 噪声、任务格式、预算与结论边界
- `results/2026-09-30-v2.5-regression.json` — v2.5 干净提交上的 6 组集成与 56 项回归证据
- `evals.json` — 3 个测试用例(提示词 + 断言)
- `REGRESSION_RESULTS.md` — 多轮确定性回归测试的公开结果摘要与复跑说明
- `comprehension.md` — 理解测验(度量"理解成本"这一真目标的方法与 fixture-b 标准题组)
- `comprehension_fixture_b.json` — fixture-b 理解测验的确定性关键词 rubric 与健康阈值
- `results/2026-07-08-v2.3-regression.json` — v2.3 回归套件 5 轮原始机器结果
- `results/2026-09-30-v2.4-regression.json` — v2.4 集成与边界/计量回归结果,绑定提交和源码摘要
- `fixtures/fixture-a` — 无文档的 JS 样例项目(测"初始化"场景)
- `fixtures/token-pilot/` — 内置试点任务文件:两种提示词、隐藏验收脚本与针对 fdf4810 的参考补丁(不进入试验工作区)
- `fixtures/fixture-b` — 已有完整 GEB 结构的 Python 样例项目(测"变更回环"与"删除重构"场景)
