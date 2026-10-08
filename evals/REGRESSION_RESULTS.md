# 确定性回归测试结果

本页记录可复跑的工具层回归测试结果。它不调用 AI,只验证程序可确定的行为;AI 对照实验仍见根目录 README 的实测数据表。

## 2026-10-08 v2.6 试点保护

干净提交 `ddf15f9e7fa6600156fdf5b8bf23b0550a33a8d7`,本地 macOS / Python 3.14.6:97/97 单元测试、6/6 集成组和严格完整 GEB 检查通过,0 跳过。源码摘要为 `9b7d520cbcffe3ff1466c29a135c711e292b357f38abe58ec537a00159672168`。

4 个内置任务在 `index` / `noindex` 两种工作区上的 8 组无模型预检全部通过:任务验收原先失败、原有回归原先通过、参考补丁可应用、应用后验收与回归通过。赋格组使用相同源码工作区,不重复预检。

补充保护覆盖完整用量缺失立即停止、部分用量只作下限、失败或未完成修改使首次修改边界保持未知,以及启动脚本默认不调用模型、校验失败阻断、参数保持和拒绝覆盖输出。测试执行器是假的,不构成模型节省实测。脱敏机器结果见 [2026-10-08 验证记录](results/2026-10-08-v2.6-guard-regression.json)。

复跑: `python3 -B evals/run_regression_suite.py --rounds 1 --out /tmp/fugue-validation.json` 和 `python3 -B evals/run_token_pilot.py --verify-tasks`。跨平台通过状态以该提交的 GitHub Actions 为准。

## 2026-09-30 v2.5

| 项 | 值 |
|----|----|
| 测试提交 | `4847f44eeca2719ac19b74dfdd6045b5ee19a3eb` |
| 测试时工作区 | 干净 |
| 环境 | 本地 macOS, Python 3.14.6 |
| 集成测试组 | 6/6 通过,1 轮 |
| 边界、计量与试点汇总 | 56/56 通过,0 跳过 |
| 自检 | strict + complete 通过,同步 dry-run 0 处 |
| 源码摘要 | `0cafaf9ddbb8532b3291dea0ed036e7c8a000404671b019ebd029296acefa892` |
| 原始 JSON | [results/2026-09-30-v2.5-regression.json](results/2026-09-30-v2.5-regression.json) |
| JSON SHA-256 | `fd1134d8462afbfae303aea706f61781c00574b5572050ebb47976db44b557f9` |

新增覆盖:分页日志的只读索引发现、归档与环境变量回退、错误身份拒绝、无遥测告警、切页及未更新快照保持未知、阶段快照、收益单、验收文件摘要、实验设置匹配、旧记录兼容、试点失败成本与负收益保留、隔离 HOME 和固定 Git 快照、部分遥测不得作为完整用量。试点运行器的 CI 测试使用假执行器,不调用模型,不构成节省实测。

模型试点与确定性测试分开,方法见 [token-pilot.md](token-pilot.md)。跨平台结果以 GitHub Actions 为准。

## 2026-09-30 v2.4

| 项 | 值 |
|----|----|
| 测试提交 | `5ef599b7580448f78504163a0b1141a5e080c372` |
| 测试时工作区 | 干净 |
| 环境 | 本地 macOS, Python 3.14.6 |
| 命令 | `python3 -B evals/run_regression_suite.py --rounds 1 --out evals/results/2026-09-30-v2.4-regression.json` |
| 集成测试组 | 6/6 通过,1 轮 |
| 边界与计量测试 | 34/34 通过,0 跳过 |
| 自检 | strict + complete 通过,同步 dry-run 0 处 |
| 源码摘要 | `1512353396b065ba95c1a7dca86ccc42e6316eb40db9160842dd374924694979` |
| 原始 JSON | [results/2026-09-30-v2.4-regression.json](results/2026-09-30-v2.4-regression.json) |
| JSON SHA-256 | `85cd6c22017880f611100e8ce9f7edc30bc3d13ec089f52c04f5da6e8ef7a264` |

修复并验证:非代码清单保留、JS 相对依赖、超过 10 项的完整事实、空目录与根目录最后文件删除、同名文件漏登、中文和控制字符 Git 路径、暂存与工作区不一致、有向环方向、安装后可执行命令、评分缺失值。计量测试另覆盖计数重置、模型变化、缓存不重复累加、对照不匹配、质量证据绑定、负差值和幂等收尾。

CI 已配置 macOS/Linux × Python 3.9/3.14,远端结果以对应提交的 GitHub Actions 为准。本地通过不代替远端多环境结果。上述测试使用合成输入验证工具行为;没有在这里进行真实模型 token 节省对照实验。

v2.3 的 30/30 是 6 组重复 5 轮,不应解释为 30 种独立场景。v2.4 明确分别报告测试组和边界测试数量,并记录源码摘要与工作区状态。

## 2026-07-08 v2.3

| 项 | 值 |
|----|----|
| 测试提交 | `72618d4` |
| 命令 | `python3 -B evals/run_regression_suite.py --rounds 5 --out evals/results/2026-07-08-v2.3-regression.json` |
| Python | `Python 3.14.6` |
| 轮数 | 5 |
| 断言组 | 30 |
| 通过 | 30 |
| 失败 | 0 |
| 通过率 | 100% |
| 原始 JSON | [results/2026-07-08-v2.3-regression.json](results/2026-07-08-v2.3-regression.json) |
| JSON SHA-256 | `9e0cd35baa866a8ebef43d196eb767e7cb7315adc73437ee88ea24772ea38b4c` |

## 覆盖范围

| 测试项 | 覆盖内容 | 结果 |
|--------|----------|------|
| `arch_fixture_b` | `geb_arch.py` 从 fixture-b 识别 `app.py` 入口、`root/services/storage` 模块、`root -> services` 与 `services -> storage` 依赖边、`legacy_format.py` 风险提示 | 5/5 通过 |
| `sync_changed_delete` | `geb_sync.py --changed --dry-run` 在删除 `services/legacy_format.py` 后重建 `services/FOLDER_INDEX.md` | 5/5 通过 |
| `check_l1_path_ghost` | `geb_check.py` 检出小项目 L1 清单中的 `services/ghost.py` 路径级幽灵条目 | 5/5 通过 |
| `adapt_copy_tools` | `geb_adapt.py --copy-tools` 复制 `geb_arch.py / geb_check.py / geb_scaffold.py / geb_sync.py / FOLDER_INDEX.md` | 5/5 通过 |
| `comprehension_grader` | `grade_comprehension.py` 产出健康的 docs-only/code-only 分数比与 token 比 | 5/5 通过 |
| `self_checks` | 本仓库通过 `geb_check --strict --complete --report` 与 `geb_sync --dry-run --graph` | 5/5 通过 |

## 复跑方式

```bash
python3 -B evals/run_regression_suite.py --rounds 5 --out /tmp/fugue-regression.json
```

`run_regression_suite.py` 每轮都会创建临时目录并复制 fixture,不依赖上一次运行的产物。`--rounds` 可调大,用于观察确定性工具在重复运行下是否稳定。

## 边界说明

- 这组测试验证的是工具层:静态分析、清单同步、检查器、适配器复制、评分器与仓库自检。
- 它不证明 AI 生成的语义一定正确;语义质量仍应通过理解测验、真实项目案例和人工抽查评估。
- fixture-b 是小型样例项目;大型 monorepo 的性能与架构候选质量仍需要后续真实案例补充。
