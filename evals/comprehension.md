# 理解测验(度量协议的真目标:理解成本)

结构合规率只是代理指标;协议的终极目标是**让新会话用更少的 token 获得正确的项目理解**。本测验直接度量它。

## 方法

对同一个项目准备两个条件,各开一个**全新** AI 会话:

- **条件 A(仅文档)**:只允许读 PROJECT_INDEX.md、各 FOLDER_INDEX.md、各文件头部注释(前 10 行),禁止读代码本体。
- **条件 B(仅代码)**:删除全部索引文件后只读代码。

向两个会话提同一组问题,对照标准答案打分(每题 0/0.5/1),同时记录各会话消耗的 token。**协议的价值 = A 的得分接近 B,而 token 远低于 B。**

## 针对 fixtures/fixture-b 的标准题组

| # | 问题 | 标准答案要点 |
|---|------|-------------|
| 1 | 这个项目是做什么的?怎么运行? | 命令行团队名册工具;`python3 app.py <command>`;数据存本地 JSON |
| 2 | 新增一个"按邮箱查用户"的功能,应该改哪几个文件? | services/user_service.py(业务)+ app.py(命令分发);数据读取经 storage |
| 3 | 哪个模块允许直接读写数据文件?为什么? | 只有 storage 层;全局约定规定 services 不直接碰文件 |
| 4 | report 功能依赖哪些模块? | storage.store(读用户)+ services.legacy_format(format_date)+ time |
| 5 | 删除 legacy_format.py 前需要处理什么? | report.py 还在用它的 format_date,需先迁移引用 |
| 6 | 用户数据校验发生在哪里? | utils.validate_email,被 user_service.add_user 调用 |

## 评分与报告

报告四个数:A 得分 / B 得分 / A token / B token。健康的协议实施应满足:A 得分 ≥ B 得分的 85%,A token ≤ B token 的 40%。对更大的项目,差距应该更显著(索引价值随规模超线性增长)。

本目录提供 fixture-b 的确定性 rubric 与评分器:

```bash
python3 grade_comprehension.py answers.json
```

`answers.json` 包含 `docs_only` 与 `code_only` 两个 run、各自 `token_count`、以及题号到答案文本的 `answers` 映射。评分器按 `comprehension_fixture_b.json` 的关键词 rubric 给每题累计得分,并输出 docs-only/code-only 的分数比与 token 比;rubric 是自动化代理指标,人工复核仍应优先看低分题的证据。

> 给自动化任务的提示:周三案例研究可在重构完成后,对 with-skill 副本跑一轮本测验(问题需按目标项目现编 4-6 题,标准答案先由通读代码的会话产出)。
