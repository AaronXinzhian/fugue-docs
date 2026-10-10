# evals/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
完整可复跑的评测包:测试用例定义、样例项目夹具、自动评分器。复跑方法见 [README.md](README.md)。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| analyze_navigation.py | 离线定位成本分析:解析 Codex exec 事件或 Claude Code stream-json(折算为同一套条目,原子编辑失败不算修改),统计确认首次修改前的命令、读文件、索引读取与工具输出;失败或未完成修改导致边界未知,保留全程统计;做同块配对与 A/A 提示,不调用模型 | INDEX_NAMES, KIND_PRIORITY, READ_COMMANDS, SEARCH_COMMANDS, LIST_COMMANDS, WRAPPERS, SHELLS, VALUE_OPTIONS, HEREDOC, PATCH_PATH, load_events(), CLAUDE_EDIT_TOOLS, is_claude_stream(), result_text(), claude_tool_item(), from_claude_stream(), items_in_order(), item_type(), strip_heredocs(), tokenize(), shell_script(), simple_commands(), unwrap(), split_redirects(), positionals(), looks_like_path(), classify(), normalize_path(), SCRATCH_PREFIXES, SYSTEM_PREFIXES, PYTHON_WRITE, PYTHON_INLINE, PYTHON_ASSIGN, PYTHON_VAR_WRITE, PYTHON_SCRATCH_HINT, python_write_targets(), is_scratch(), is_outside_workspace(), is_skill_path(), empty_bucket(), analyze_events(), nav_value(), NAV_METRICS, paired_stats(), power_hint(), sign_test(), infer_comparisons(), trial_directory(), analyze_output(), main() |
| claude_mock_api.py | Claude Code 零成本自检用的本地 Messages API 替身:只监听 127.0.0.1,按脚本依次返回工具调用再结束,支持流式 SSE | MockMessagesAPI |
| grade_comprehension.py | 理解成本评分器:按 rubric 给 docs-only/code-only 答案打分,计算分数比与 token 比 | WS, DEFAULT_SPEC, read_json(), normalize(), answer_map(), has_any(), has_all(), point_passed(), grade_question(), grade_run(), condition_key(), compare_runs(), load_runs(), main() |
| grade_iteration.py | 自动评分器:对每个运行目录逐断言打分,生成 grading.json | WS, GEB_CHECK, INDEX_NAMES, L3_TAGS, read(), head_lines(), first_docstring(), find_index(), run_geb_check(), run_app(), expectation(), grade_eval0(), grade_eval1(), grade_eval2(), GRADERS, main() |
| run-claude-pilot.sh | Claude Code 试点安全入口:plan 无模型校验 docutils 任务、本机自检并保存计划;aa 跑 A/A 噪声,compare 跑 index/hint/fugue 三组;缺令牌直接退出,结束后离线汇总定位结果 | — |
| run-first-round.sh | 首轮试点安全入口:默认无模型校验并展示计划,显式执行时才调用 Codex、保持预算并离线汇总定位结果 | — |
| run_regression_suite.py | 确定性回归测试套件:多轮验证架构候选、增量同步、路径级检查、适配器复制、理解评分与仓库自检 | WS, ROOT, run(), fail(), ok(), require(), copy_fixture(), test_arch_fixture_b(), test_sync_changed_delete(), test_check_l1_path_ghost(), test_adapt_copy_tools(), test_comprehension_grader(), test_self_checks(), TESTS, run_round(), git_commit(), source_digest(), run_boundaries(), main() |
| run_token_pilot.py | 配对块 token 试点:Codex 或 Claude Code(插件钩子)执行,无索引/仅索引/完整赋格三组或 A/A 噪声设计;外部源码固定提交加索引覆盖,隐藏验收文件或测试补丁、测试重置、运行目录与参考补丁校验;Claude 白名单环境、单一凭据、沙箱读取禁区与凭据清除、付费前本地假接口自检;插件/钩子/沙箱/环境漂移检查,接口故障与执行器故障单列并停止;主指标为未缓存输入+输出,另报标价成本;保留失败、不自动声称节省 | ROOT, DEFAULT_TASKS_FILE, ARMS, HINT_TEXT, HINT_FILES, DESIGNS, PRIMARY_METRIC, COST_KEYS, HEADER_LINE, EMPTY_BLOCK, INDEX_FILE_NAMES, DEFAULT_TEST_RULE, BASE_PROMPT, PLAIN_PROMPT, design(), load_tasks(), patch_targets(), glob_regex(), matching(), checked_members(), extract(), materialize_source(), archive_hashes(), leaked(), SKILL_PATHS, PLUGIN_PATHS, skill_archive(), build_schedule(), is_excluded(), strip_indexes(), drop_empty_blocks(), count_index_files(), git_environment(), write_hint(), prepare_workspace(), protected_snapshot(), expand(), prepare_acceptance(), validate(), cli_usage(), partial_usage(), CLAUDE_MODEL_KEYS, CLAUDE_MESSAGE_KEYS, counter(), claude_tokens(), claude_result(), claude_usage(), claude_partial_usage(), INFRA_STATUSES, INFRA_API_ERRORS, INFRA_TEXT, claude_status(), claude_session(), hook_stats(), usage_value(), OPTIONAL_USAGE_KEYS, token_metrics(), budget_cost(), sum_known(), budget_tokens(), failure_aware(), summarize_trials(), run_process(), run_codex(), CLAUDE_ENV_ALLOW, CREDENTIAL_VARS, claude_credentials(), claude_environment(), claude_settings(), claude_command(), run_claude(), kill_group(), build_prompt(), trial(), SANDBOX_DISABLED, environment_signature(), setup_problem(), run_trial(), verify_tasks(), parse_weights(), SELFTEST_MARKER, sandbox_test_command(), claude_selftest(), deny_read_paths(), stale_trial_dirs(), agent_version(), resolve_source(), main() |
| test_boundaries.py | 同步写盘、路径、依赖、有向环、暂存区与评分负对照 | ROOT, header(), BoundaryTests |
| test_first_round.py | 不调用模型的启动入口测试:首轮与 Claude 入口的默认计划、令牌与校验阻断、显式执行、参数保持与输出防覆盖 | SCRIPT, CLAUDE_SCRIPT, FAKE_PYTHON, FirstRoundTests, ClaudePilotScriptTests |
| test_hooks.py | 钩子回归:按工具调用归属、静默放行、新文件骨架、缺口只提示一次、切分支/提交/用户与其他会话的改动不归入、链接/冲突/编码/生成代码保护、阈值迁移、改名、对话记录计量 | ROOT, HOOK, GIT_ENV, L1, APP, CORE, HEADER, usage_line(), HookCase, MaintenanceTests, AttributionTests, MeteringTests, PluginTests |
| test_metrics.py | token 用量、缺失值、重置、对照证据与重复计量回归 | MetricsTests |
| test_navigation.py | 定位分析器的命令分类、首次修改边界、旧事件格式、目录汇总与 Claude stream-json 折算(变量写入、被拒调用)回归 | command(), kinds(), ClassifierTests, AnalyzerTests, ClaudeStreamTests |
| test_token_pilot.py | 不调用模型的试点测试:配对块、三组/A-A 汇总、成本口径、索引剥离、预算与未知停止、外部源码与归档安全、隐藏测试补丁与重置、Claude 用量换算、凭据选择与白名单环境、接口故障与缺金额停止、自检通过与失败、假 Codex/假 Claude 端到端 | TWO_ARM, ROOT_DOCUTILS_TASKS, HAS_SOURCE_REF, usage(), trial(), FAKE_CODEX, FAKE_CLAUDE, FAKE_SELFTEST_CLAUDE, SummaryTests, stream(), ClaudeTests, SelfTestTests, SourceTests, DesignTests, RunnerTests, ClaudeRunnerTests |

## 数据文件
- `results/2026-10-10-claude-three-arm-hint-sonnet.json` — Claude Code 三组对比(Docutils,Sonnet 5.5)的脱敏数值:72 次、通过 19/24/24、三个比较的配对统计、钩子计数、整套测试运行方式与定位代理
- `results/2026-10-10-claude-aa-sonnet.json` — Claude Code A/A(Docutils,Sonnet 5.5)的脱敏数值:16 次、15 次通过、自检 12 项、噪声与功效提示、重算后的定位代理
- `results/2026-10-08-three-arm-xhigh.json` — 首轮真实三组试点的脱敏数值:6 次调用、5 次验收通过、预算停止;保留负差值与准备失败未知用量
- `results/2026-10-08-v2.6-guard-regression.json` — 干净提交上的 97 项回归、6 组集成与 8 组无模型任务预检脱敏证据
- `TOKEN_PILOT_RESULTS.md` — Claude Code 三组对比与 A/A、Codex 首轮三组试点、失败与未运行任务;历史预检与结论边界
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
- `fixtures/docutils-pilot/` — 外部仓库试点:docutils 固定提交、包级索引覆盖补丁、四个上游任务的症状提示词、隐藏测试补丁与参考补丁
- `fixtures/fixture-b` — 已有完整 GEB 结构的 Python 样例项目(测"变更回环"与"删除重构"场景)
