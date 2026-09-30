# Token 用量与对照账本

## 实际用量

Codex 开发任务开始、结束时调用技能内的脚本：

```bash
python3 <skill-dir>/scripts/geb_metrics.py start <project-root> --task <task-key>
python3 <skill-dir>/scripts/geb_metrics.py finish <returned-run-id> --receipt
python3 <skill-dir>/scripts/geb_metrics.py report
python3 <skill-dir>/scripts/geb_metrics.py report --root <project-root>
```

默认账本位于 `${CODEX_HOME:-~/.codex}/fugue/metrics/`，不进入项目 Git。可用脚本全局参数 `--ledger <dir>` 指定隔离账本。`start` 优先使用 `CODEX_THREAD_ID`,缺失时回退 `CODEX_SESSION_ID`;通过本地 Codex SQLite 索引只读定位当前日志页,核对日志内会话 ID。索引不可用时才查找唯一匹配的普通或归档日志,不会按项目或修改时间猜会话。也可显式传 `--session <rollout.jsonl>`。

检查 `measurement_ready`。为 false 时执行 `doctor`(支持 `--session-id` 或 `--session`),区分缺 ID、未找到、分页歧义、没有遥测和读取异常。警告不阻止代码开发,但报告必须说明用量未知。旧记录不猜填;v1/v2 账本可以一起汇总。`report` 新增状态分布和已结束任务的有效计量覆盖率,活动任务不计入分母。

每个记录保留任务标识、项目路径、开始时的 Git 提交与脏状态、模型、会话标识、来源路径、两个计数快照。只写数值和定位元数据，不复制会话正文，也不上传账本。

`usage` 是两个已落盘遥测快照的差：输入、缓存输入、输出、总 token、未缓存输入。缓存输入是输入的子集，不额外加入总量；缓存输入不等于赋格节省。模型或推理强度切换、计数重置、缺数据、没有新遥测时保持未知。任务期间切页的计数连续性尚未自动证明,记为 `source_rotated_unverified`,不从两页直接相减。`finish` 幂等,防止重复累计。

记录覆盖的是快照区间，可能受遥测写入延迟影响；结束后发送的回复及未合并的子代理会话不在该区间内。任务中途才开始计量时，只能称后续区间用量。跨任务总数包含基线试验成本，不能当作账单或余额。

## 每次任务的收益单

`finish --receipt` 或 `receipt <run-id>` 输出实际输入/缓存/输出、耗时、验收、文档阶段和对照差值。`elapsed_seconds` 是 start 到 finish 的墙钟时间,含等待,不是模型运行时间。`--outcome passed|failed|partial --evidence <测试日志>` 只表示调用方记录的验收,保存文件路径和 SHA-256,不能替代独立质量审查。不提供时显示 `unreviewed`。

需要分离文档维护开销时,在该阶段开始前运行 `checkpoint <run-id> --phase implement`,结束后运行 `checkpoint <run-id> --phase documentation`。每个标签归属从上次快照到本次快照的区间,不是从本次开始。阶段数值为总区间的子集,不能重复加入总量;没有完整边界时显示未知。这也不代表 skill 加载等所有附加成本。

没有合格对照就展示未知。历史同类任务预测尚未实现,也不会作为实测差值入账。项目回本必须额外计入首次建索引成本;热启动试点不测初始化,不输出回本结论。

## 对照差值

单次开发只能观测使用赋格的用量，无法同时知道未使用赋格会消耗多少。因此 `saved_tokens` 默认 `null`，不是 0。

要测量收益，准备相同干净提交的两份副本，在相互独立的新会话里完成同一任务，使用相同模型与任务标识。基线侧 `start --condition baseline`，赋格侧默认 `fugue`。真实任务必须计入定位、修改、补文档和验证的成本；仅比较阅读阶段时，在任务标识及报告中明确限定该阶段。

两侧 `start` 传相同的 `--experiment <settings.json>`,记录模型版本、推理强度、工具权限、上下文约束、预算、验收标准和冷/热启动场景。`compare` 要求设置相同;日志能提供的推理强度和供应商也会核对。该文件是显式实验约定,工具不能仅凭配置声明证明隔离已生效。无设置的旧记录仍能看用量,不能新建可比对照。

完成后独立检查两侧任务质量，保存审核 JSON：

```json
{
  "baseline_run_id": "实际基线 UUID",
  "fugue_run_id": "实际赋格 UUID",
  "quality_passed": true,
  "reviewer": "实际审核人或工具",
  "method": "任务完成标准、测试命令及证据位置"
}
```

```bash
python3 <skill-dir>/scripts/geb_metrics.py compare \
  --baseline <baseline-run-id> --fugue <fugue-run-id> \
  --quality-evidence <review.json>
```

脚本核对提交、模型、任务、独立会话和计量状态，绑定质量证据的 SHA-256。它校验元数据，不能自行保证任务难度和审核判断公平。差值是基线总 token 减赋格总 token，可以为负；同一赋格运行只能进入一次汇总。它是配对观察差值，不是单次试验就成立的因果证明。正式对外结论需要多项目、重复独立任务与质量复核。

理解评分器的 `proxy_healthy` 只表示关键词得分与用量阈值满足。未提供两侧的 `quality_review: {"passed": true, "evidence": "审核记录位置"}` 时，最终 `healthy` 为 `null`。缺失 token 不能通过质量审核补成零；人工预填的测试数字不是实际测量。

受限重复试点见 [evals/token-pilot.md](../evals/token-pilot.md)。试验总消耗包含失败与对照组,与日常开发账本分开;通过自动验收的配对差值不自动加入已复核的节省总数。
