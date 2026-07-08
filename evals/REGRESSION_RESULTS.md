# 确定性回归测试结果

本页记录可复跑的工具层回归测试结果。它不调用 AI,只验证程序可确定的行为;AI 对照实验仍见根目录 README 的实测数据表。

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
