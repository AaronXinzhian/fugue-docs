# Token 用量与对照账本

## 实际用量

Codex 开发任务开始、结束时调用技能内的脚本：

```bash
python3 <skill-dir>/scripts/geb_metrics.py start <project-root> --task <task-key>
python3 <skill-dir>/scripts/geb_metrics.py finish <returned-run-id>
python3 <skill-dir>/scripts/geb_metrics.py report
python3 <skill-dir>/scripts/geb_metrics.py report --root <project-root>
```

默认账本位于 `${CODEX_HOME:-~/.codex}/fugue/metrics/`，不进入项目 Git。可用脚本全局参数 `--ledger <dir>` 指定隔离账本。`start` 默认通过 `CODEX_THREAD_ID` 精确找到会话；也可显式传 `--session <rollout.jsonl>`。匹配不唯一、没有计数或无法访问时保持未知。

每个记录保留任务标识、项目路径、开始时的 Git 提交与脏状态、模型、会话标识、来源路径、两个计数快照。只写数值和定位元数据，不复制会话正文，也不上传账本。

`usage` 是两个已落盘遥测快照的差：输入、缓存输入、输出、总 token、未缓存输入。缓存输入是输入的子集，不额外加入总量；缓存输入不等于赋格节省。模型切换、累计计数重置或缺数据时不生成可靠差值。`finish` 幂等，防止重复累计。

记录覆盖的是快照区间，可能受遥测写入延迟影响；结束后发送的回复及未合并的子代理会话不在该区间内。任务中途才开始计量时，只能称后续区间用量。跨任务总数包含基线试验成本，不能当作账单或余额。

## 对照差值

单次开发只能观测使用赋格的用量，无法同时知道未使用赋格会消耗多少。因此 `saved_tokens` 默认 `null`，不是 0。

要测量收益，准备相同干净提交的两份副本，在相互独立的新会话里完成同一任务，使用相同模型与任务标识。基线侧 `start --condition baseline`，赋格侧默认 `fugue`。真实任务必须计入定位、修改、补文档和验证的成本；仅比较阅读阶段时，在任务标识及报告中明确限定该阶段。

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
