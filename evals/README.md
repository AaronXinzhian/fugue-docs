# 评测复跑指南

README 中的实测数据由以下流程产生,本目录包含完整复现材料。

## 流程

1. **准备运行目录**(每个用例两份夹具副本):

```bash
mkdir -p /tmp/geb-eval/iteration-1
for name in eval-0-init-legacy-js-project eval-1-add-feature-loop eval-2-delete-refactor-loop; do
  for side in with_skill without_skill; do
    mkdir -p "/tmp/geb-eval/iteration-1/$name/$side/outputs"
  done
done
cp -r fixtures/fixture-a /tmp/geb-eval/iteration-1/eval-0-init-legacy-js-project/with_skill/outputs/project
cp -r fixtures/fixture-a /tmp/geb-eval/iteration-1/eval-0-init-legacy-js-project/without_skill/outputs/project
for name in eval-1-add-feature-loop eval-2-delete-refactor-loop; do
  cp -r fixtures/fixture-b "/tmp/geb-eval/iteration-1/$name/with_skill/outputs/project"
  cp -r fixtures/fixture-b "/tmp/geb-eval/iteration-1/$name/without_skill/outputs/project"
done
```

2. **跑 AI**:对每个用例,把 `evals.json` 里的 prompt 交给你的 AI 工具执行两次——`with_skill` 一侧先注入本协议(Claude Code 装本 skill,或其他工具用 `geb_adapt.py` 注入规则),`without_skill` 一侧裸跑;都直接修改各自 `outputs/project`。同时在**每个用例目录**(如 `eval-0-init-legacy-js-project/`)下放一份 `eval_metadata.json`,内容为该用例的 prompt 与 assertions(从 `evals.json` 复制对应条目即可);tokens/耗时如你的工具提供,可记在各 `<side>/timing.json`。

3. **自动评分**:

```bash
python3 grade_iteration.py /tmp/geb-eval/iteration-1
```

每个运行目录会生成 `grading.json`(逐断言 pass/fail + 证据)。所有断言均为脚本判定:文件存在性、L3 标签、索引条目、`geb_check` 违规数,以及实际运行夹具项目的功能验证(eval-1 会真的执行 `app.py add/export`,eval-2 会执行 `report`)。

## 理解成本测验

`comprehension.md` 用来度量协议的真目标:只读索引是否能用更少 token 获得接近读代码的理解质量。跑完 docs-only / code-only 两个新会话后,把答案与 token 数写成 JSON:

```json
{
  "runs": [
    {
      "condition": "docs_only",
      "token_count": 1200,
      "answers": {
        "1": "……",
        "2": "……"
      }
    },
    {
      "condition": "code_only",
      "token_count": 4200,
      "answers": {
        "1": "……",
        "2": "……"
      }
    }
  ]
}
```

然后运行:

```bash
python3 grade_comprehension.py answers.json
```

默认 rubric 是 `comprehension_fixture_b.json`;输出会包含每题得分、总分、docs-only/code-only 分数比和 token 比。

## 确定性回归套件

`run_regression_suite.py` 用于发布前和外部复核,不调用 AI,只验证工具层可确定的行为:

- `geb_arch.py` 能从 fixture-b 识别 `app.py` 入口、`root/services/storage` 模块、关键依赖边和 legacy 风险。
- `geb_sync.py --changed` 在删除文件后能重建受影响的 L2 清单。
- `geb_check.py` 能发现小项目 L1 清单中的路径级幽灵条目。
- `geb_adapt.py --copy-tools` 会复制 `geb_arch.py / geb_check.py / geb_scaffold.py / geb_sync.py`。
- `grade_comprehension.py` 能产出健康的 docs-only/code-only 分数比与 token 比。
- 本仓库自身通过 `geb_check --strict --complete` 和 `geb_sync --dry-run --graph`。

复跑命令:

```bash
python3 -B evals/run_regression_suite.py --rounds 5 --out /tmp/fugue-regression.json
```

`--rounds` 用于重复运行临时夹具测试,降低偶发环境因素影响;输出 JSON 会记录每轮每项结果。

## 诚实声明

- 原始实测使用 Claude Code 子代理并行跑两侧;用其他工具复跑时,绝对数值(tokens/耗时)会不同,但断言通过率可直接对比。
- `grade_iteration.py` 依赖 `../scripts/geb_check.py`;`grade_comprehension.py` 只依赖本目录 rubric JSON。
