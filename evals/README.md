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

## 诚实声明

- 原始实测使用 Claude Code 子代理并行跑两侧;用其他工具复跑时,绝对数值(tokens/耗时)会不同,但断言通过率可直接对比。
- 评分器依赖 `../scripts/geb_check.py`,请在仓库内原位运行。
