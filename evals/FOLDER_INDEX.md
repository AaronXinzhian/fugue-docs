# evals/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
完整可复跑的评测包:测试用例定义、样例项目夹具、自动评分器。复跑方法见 [README.md](README.md)。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| grade_iteration.py | 自动评分器:对每个运行目录逐断言打分,生成 grading.json | CLI:`python3 grade_iteration.py <iteration目录>` |

## 数据文件
- `evals.json` — 3 个测试用例(提示词 + 断言)
- `fixtures/fixture-a` — 无文档的 JS 样例项目(测"初始化"场景)
- `fixtures/fixture-b` — 已有完整 GEB 结构的 Python 样例项目(测"变更回环"与"删除重构"场景)
