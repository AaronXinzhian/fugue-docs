# Token 增量试点

当前实际进展见 [TOKEN_PILOT_RESULTS.md](TOKEN_PILOT_RESULTS.md)。尚未取得完整可比配对,没有节省率结论。

## 问题与范围

在同样已有索引的仓库里,额外启用赋格 skill 流程是否减少完成任务的全程 token? 两组都能搜索、阅读代码和已有索引。只隔离全局规则和个人技能,不故意限制基线的代码访问。

这不是无文档项目首次接入测试,不包含初始化成本,不能据此推断大型项目、跨模块任务、长期回本或普遍节省率。四个真实仓库小任务集中在 metrics 模块,属于窄范围试点,不是跨项目代表性样本。

## 固定设计

- 源码固定为 `fdf4810`,任务为会话环境变量回退、计量覆盖率、对照差值比例、finish 墙钟耗时。
- 各任务各条件最多重复 3 次,共 24 次;固定 seed 随机运行顺序。单次调用前后没有共享对话,隔离 HOME/CODEX_HOME,忽略用户 config,不继承原会话 ID。
- 两侧使用相同模型、推理强度、workspace-write 权限、任务提示词和验收标准。基线按常规编码,赋格侧额外安装冻结的 skill 副本和启用规则。
- 源码 archive 和 skill 内容绑定 SHA-256。固定的预存验收断言在外部执行,两侧都跑原有及新增测试,原有测试文件不得修改。
- 使用独立 Codex CLI 的完整 `turn.completed` 用量,输入包含缓存,总量为输入加输出。包含技能阅读、修改、文档维护、代理自测和最终回复。外部 Python 验收不消耗模型 token,其时间不在模型执行时长内。
- 隔离 HOME 内保留会话日志,不使用 `--ephemeral`,以便技能自身正常计量并在超时时保留已上报的部分用量。部分用量单列 `partial_usage`,不替代完整用量,也不进入节省统计;未知的在途请求仍可能已消耗 token。
- 程序限时终止整个进程组。总 token 上限只在每次完整试验后检查,可能超出一次试验用量;不是服务端硬 token 限额。用量未知时立即停止后续试验,防止失控重复。
- 失败/超时不删除,计入尝试数和已知实验成本。只有两侧都通过自动验收且计量完整的配对进入差值统计。必须同时报告验收率,避免只看成功配对造成偏差。
- 自动通过不等于独立语义复核。默认结果为 `test_passed_pairs_only_pending_independent_semantic_review`,不写入日常账本的已复核节省。负值原样保留。

## 运行

需要已登录的 Codex CLI。没有 `--execute` 只输出计划,不调用模型。

```bash
python3 -B evals/run_token_pilot.py --ref fdf4810 --model <可用模型> \
  --output /private/tmp/fugue-pilot-plan

python3 -B evals/run_token_pilot.py --ref fdf4810 --model <可用模型> \
  --tasks session-fallback --repeats 1 --timeout 300 --max-total-tokens 60000 \
  --output /private/tmp/fugue-pilot-first-pair --execute
```

通过预检后再在新输出目录执行全部 24 次,根据实际吞吐设置限时和预算。不自动扩大预算或切换模型。

私有输出包含工作副本和原始事件,不要整目录提交或上传。每次运行临时链接本地登录文件,调用结束即移除链接。公开时仅发布脱敏的 `report.json` 数值、测试方法和经检查的差异摘要。模型尚未完成时无最终遥测,其消耗保持未知,不按 0 计费或计入“省下”。
