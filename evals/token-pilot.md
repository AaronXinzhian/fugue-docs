# Token 增量试点

当前实际进展见 [TOKEN_PILOT_RESULTS.md](TOKEN_PILOT_RESULTS.md)。首轮完成两个三组块后预算停止,有自动验收通过的负差值配对,仍没有普遍节省率或已复核净节省结论。

## 要回答的问题

旧设计只比较“已有索引、不用 skill”和“已有索引、用 skill”。两组都能读索引,索引带来的定位收益在两边同时存在、互相抵消,剩下的只有 skill 流程本身的开销。所以那个设计最多能说明“流程是否额外费 token”,测不到索引是否省 token。

现在默认使用三组,把两个问题分开:

| 比较 | 对照 → 处理 | 回答的问题 |
|------|-------------|------------|
| `index_effect` | `noindex` → `index` | 同一仓库有 L1/L2/L3 索引时,完成同一任务是否更省 |
| `workflow_effect` | `index` → `fugue` | 已有索引时,再加载 skill 并执行计量、同步、检查,是省还是费 |
| `total_effect` | `noindex` → `fugue` | 两者合计 |

差值一律为“对照减处理”,正数表示处理组用得更少,负数原样保留。

## 组别

| 组 | 工作区 | 技能 |
|----|--------|------|
| `noindex` | 删除 `PROJECT_INDEX.md`、`FOLDER_INDEX.md`;删除代码文件前 50 行中以 `[INPUT]:`/`[OUTPUT]:`/`[POS]:`/`[PROTOCOL]:` 开头的标签行,标签删光后留下的空文档字符串或空注释块一并删除;删除规则文件中独占一行标记的 GEB 托管块;任务文件 `strip_exclude` 声明的样本数据不动 | 不安装 |
| `index` | 源码原样 | 不安装 |
| `fugue` | 源码原样 | 隔离 HOME 内安装冻结的 skill 副本,并在 `AGENTS.md` 启用 |

每次试验在 `strip` 字段记录删除的索引文件数、头部行数、空块数和托管块数,便于复核剥离范围。`noindex` 和 `index` 两组的提示词完全相同,只有工作区不同。

**防止看到答案或组别**:

- 赋格组安装的 skill 取自 `--skill-ref`。测本仓库时默认等于源码提交 `fdf4810`,且不允许比源码新:更新的 skill 脚本和文档已经实现了这四个任务。旧执行器从当前工作树复制 skill,存在这个泄漏;它的预检没有运行赋格组,已有数据不受影响。测外部仓库时默认用本仓库 `HEAD`。
- 每次试验在系统临时目录下一个随机命名的目录里运行,模型看到的工作目录和 HOME 路径不含任务名、组名或输出目录名;结束后整个目录移入输出目录的 `trial-NNN/`。同一时刻只存在当前试验的临时目录。
- 源码归档或 skill 归档中只要出现与隐藏验收文件、参考补丁内容相同的文件,执行器就拒绝运行。
- `workspace-write` 沙箱不限制读取,所以分析器会把工作区、skill 和系统目录以外的读取路径单列为 `outside_workspace_reads`,汇总里列出出现过这类读取的试验,供人工复核。

## 任务

任务定义在 JSON 任务文件中(schema `geb.token-pilot.tasks.v1`)。内置任务在 [fixtures/token-pilot/tasks.json](fixtures/token-pilot/tasks.json),是 `fdf4810` 上的四个计量小任务,每个任务有两种提示词:

- `symptom`(默认):只描述可观察到的问题和需要输出的字段名,不点名文件或函数,模型需要自己定位代码。
- `named`:原试点措辞,直接点名文件和函数,用于与 2026-09-30 的预检保持可比。

验收脚本和参考补丁放在任务文件目录,不进入试验工作区。模型结束后,执行器先保存 `changes.patch`,再复制隐藏验收文件,依次运行验收命令和回归命令(原有测试),并检查受保护文件没有被改动。

### 零成本任务校验

```bash
python3 -B evals/run_token_pilot.py --verify-tasks
```

不调用模型。对每个任务,分别在 `noindex` 和 `index` 两种工作区上检查四件事:验收先失败(任务没有预先被解决)、回归先通过(剥离没有破坏原有测试)、参考补丁能打上、打上后全部通过。`fugue` 组与 `index` 组工作区相同,不重复校验。

这一步曾发现剥离逻辑误删了 `geb_adapt.py` 中定义托管标记的源码常量,导致无索引组的原有测试失败;修正后 4 个任务 × 2 种工作区全部通过。正式试验前应先跑这一步。内置任务固定在历史提交上,浅克隆需要先 `git fetch --unshallow`;CI 已改为拉取完整历史。

### 外部仓库

自托管仓库有三个固有限制:`fdf4810` 本身就是赋格 skill 的源码,`SKILL.md` 和讲解索引的文档在所有组的工作区里都存在;无索引组里仓库自带的同构自检会失败,模型若去运行或"修复"它会多花 token;代码文件约 20 个,`grep` 几次就能看遍,定位收益天然很小。报告中 `self_hosted` 为 true 时,`index_effect` 应视为有混杂因素。要测中等规模仓库,用 `--source-repo` 和自己的任务文件:

```json
{
  "schema": "geb.token-pilot.tasks.v1",
  "source_ref": "<固定提交>",
  "strip_exclude": ["tests/fixtures/"],
  "protected": ["tests/**/test_*.py"],
  "regression": [["{python}", "-m", "pytest", "-q", "tests"]],
  "tasks": {
    "<任务名>": {
      "prompts": {"symptom": "<只描述现象>", "named": "<可选:点名位置>"},
      "acceptance_files": {".pilot_acceptance/test_task.py": "hidden/test_task.py"},
      "acceptance": [["{python}", "-m", "pytest", "-q", ".pilot_acceptance/test_task.py"]],
      "reference_patch": "hidden/task.patch"
    }
  }
}
```

`{python}` 会替换为当前解释器;`acceptance_files` 和 `reference_patch` 的路径相对任务文件解析。源提交里必须已有索引文件,否则执行器拒绝运行,因为这里测的是已有索引的热启动场景。

## 调度与预算

- **配对块**:同一任务、同一重复编号的所有组连续执行,块内顺序随机,块与块之间也按 seed 随机。预算中途停止时,已完成的块都是完整配对,不会留下大量单侧结果。
- **预算**:每个完整块结束后用完整用量检查,最多超出一个完整块。拿不到完整用量时立即停止后续调用,即使已有部分用量也不继续:部分用量只能给出下限,无法确定尚未上报的请求消耗。保留不完整块及部分用量,供诊断使用。软上限不是服务端硬限额。
- **块内位置**:每次试验记录 `block_position`,汇总给出各位置的主指标中位数,用于检查相邻试验之间是否有顺序或服务端缓存效应。
- **重复次数**:上限放宽到 20,真正的约束是预算。需要多少次由 A/A 噪声决定(见下)。

按初次预检单次约 12.3 万 total token 的量级粗估,三组 × 4 任务 × 3 次 = 36 次试验约 440 万 total token,其中大部分是缓存输入。这只是基于一次观测的预算参考,不是预测。

## 指标

**主指标 `uncached_plus_output` = 未缓存输入 + 输出。** 初次预检的 12.1 万输入中有 10.4 万是缓存命中,`total_tokens` 基本反映“轮数 × 上下文长度”,而缓存输入的计费通常远低于未缓存输入。`total_tokens`、未缓存输入、缓存输入、输出仍逐项报告。

需要按价格折算时,用 `--cost-weights uncached_input_tokens=1,cached_input_tokens=0.1,output_tokens=4` 传入相对权重(示例数值,不是任何供应商的价格)。权重写入报告,执行器不内置价格。

**定位代理指标**(来自 `analyze_navigation.py`,不是 token):首次修改前的命令数、工具输出字符数、读取文件数,以及全程命令数和输出字符数。索引如果有效,应当先在这里显出差别,而且通常比 token 噪声小。工具输出按模型侧截断前计算,只能作为上界代理。

每个比较报告配对数、两组中位数、差值的合计/均值/中位数/标准差,以及处理组更少/更多/持平的块数。相对效应用对数比 log(对照/处理) 的均值和标准差,换算为几何节省率 1 − exp(−均值);同时给出节省率中位数和范围。不用 (对照−处理)/对照 的均值:两组同分布时它也会系统性偏负,会在 A/A 里制造假效应。

只有两侧都通过验收且用量完整的同块试验才进入上述差值,指标缺失(例如没检测到首次修改)的配对单独计数。为避免只看成功配对造成的幸存者偏差,每个比较另给 `failure_aware`:一侧失败时通过的一侧算更好,两侧都失败算持平,再做双侧精确符号检验。各组验收率也单列。

## A/A 噪声与样本量

```bash
python3 -B evals/run_token_pilot.py --model <模型> --design aa --aa-arm index \
  --tasks session-fallback --repeats 5 --timeout 300 --max-total-tokens <预算> \
  --output /private/tmp/fugue-aa --execute
```

A/A 设计把同一组跑两遍(`index-a`、`index-b`),比较名为 `noise`。报告中的 `power_hint` 根据对数比的标准差,给出检出 10%/20%/30% 相对节省所需的配对数(双侧 5%、80% 功效、正态近似)。举例:若 A/A 对数比标准差为 0.3,检出 10% 需要约 64 对,20% 约 15 对,30% 约 6 对。这说明每组 3 次重复一般只能看出很大的效应。正式试验前先跑 A/A,再决定 `--repeats`。

## 离线定位分析

```bash
python3 -B evals/analyze_navigation.py /private/tmp/<试验输出目录> --output nav.json
```

不调用模型。可直接分析已有私有输出目录,包括 2026-09-30 预检保留在本地的 `events.jsonl`(旧标签 `baseline`/`fugue` 自动按 `workflow_effect` 配对)。没有 `report.json` 时只输出逐次结果。

命令分类是启发式的:展开 `bash -lc` 包装,按管道和 `&&` 拆分;先判修改(`apply_patch`、`sed -i`、`tee`、重定向写入、内联 Python 写文件、`file_change` 事件;写入临时目录或未展开的 shell 变量路径不算首次修改),再判运行测试(`pytest`、`python -m unittest`、直接运行 `test_*.py`),然后是 `cat`/`sed`/`head` 等读取和 `rg`/`grep` 搜索;读到 `test_*.py` 仍记为读取。以 Python 运行 `geb_*.py` 记为赋格流程。只支持 Codex `exec --json` 事件流,兼容 `type` 与旧 `item_type` 字段;其他代理需要另写适配。

首次修改边界要求事件已完成且没有失败状态。边界之前出现失败或未完成的修改时,可能已有部分写入,因此 `first_edit_boundary_uncertain` 为 true,首次修改前的定位指标保持未知;全程命令与输出统计仍保留。该保护避免把失败的修改尝试当成定位完成。

## 运行

需要已登录的 Codex CLI。没有 `--execute` 只输出计划和配对块顺序,不调用模型。

首次运行入口已纳入仓库,不依赖下载附件:

```bash
# 默认只校验并保存计划,不调用模型
bash evals/run-first-round.sh

# 核对计划和预算后再执行;结束时自动生成 navigation.json
bash evals/run-first-round.sh --execute
```

入口默认 `gpt-6.1-sol`、`xhigh`、单任务 `session-fallback`、三组各 1 次、600 秒限时与 500,000 total token 软上限。它只是流程冒烟测试,不是节省结论。原先的单任务两组预算不自动扩展为四任务三组试验;软上限仍可能超出一个完整块。默认私有结果在 `~/fugue-pilot/first-round-<时间>-<进程号>/`,相邻的 `.plan.json` 和 `.preflight.json` 保留计划与无模型校验。已有输出不会覆盖。

可通过 `MODEL`、`EFFORT`、`BUDGET`、`TIMEOUT`、`REPEATS`、`TASKS`、`DESIGN`、`PILOT_CODEX` 与 `PILOT_OUTPUT` 显式修改设置。例如 `TASKS="session-fallback coverage-summary"` 选择多个任务;可用任务名以任务 JSON 为准。`PILOT_CODEX` 可指向桌面 App 内置客户端,不自动切换客户端或模型。执行模式在 macOS 有 `caffeinate` 时阻止空闲休眠,其他系统直接运行。无模型校验失败时不启动任何模型调用。

```bash
# 0. 零成本校验任务
python3 -B evals/run_token_pilot.py --verify-tasks

# 1. 查看计划
python3 -B evals/run_token_pilot.py --model <可用模型> --output /private/tmp/fugue-plan

# 2. A/A 噪声(见上)

# 3. 三组正式试点
python3 -B evals/run_token_pilot.py --model <可用模型> --design three-arm \
  --repeats <按 A/A 结果> --timeout 300 --max-total-tokens <预算> \
  --output /private/tmp/fugue-3arm --execute

# 4. 定位分析
python3 -B evals/analyze_navigation.py /private/tmp/fugue-3arm --output nav.json
```

`--design two-arm --prompt-style named` 复现旧的两组设计;旧记录中的 `baseline` 即现在的 `index` 组。不会自动扩大预算或切换模型。

## 固定约束

- 单次调用前后没有共享对话,隔离 HOME/CODEX_HOME,忽略用户 config,不继承原会话 ID;各组模型、推理强度、workspace-write 权限、任务提示词和验收相同。
- 源码 archive、skill 提交与内容、任务文件、验收文件和参考补丁都绑定 SHA-256;执行器自身也记录摘要。
- `changes.patch` 包含模型新建的文件,在复制隐藏验收文件之前生成。
- 使用独立 Codex CLI 的完整 `turn.completed` 用量,输入包含缓存,总量为输入加输出,包含技能阅读、修改、文档维护、代理自测和最终回复。外部验收不消耗模型 token。
- 保留隔离会话日志,不使用 `--ephemeral`;超时时可单列已上报的 `partial_usage`,它不替代完整用量,也不进入节省统计。
- 程序限时终止整个进程组。失败和超时不删除,计入尝试数和实验成本。
- 自动通过不等于独立语义复核。结果等级为 `test_passed_pairs_only_pending_independent_semantic_review`,不写入日常账本的已复核节省。

私有输出包含工作副本和原始事件,不要整目录提交或上传。每次运行临时链接本地登录文件,调用结束即移除。公开时仅发布脱敏的 `report.json` 数值、测试方法和经检查的差异摘要。模型尚未完成时没有最终遥测,其消耗保持未知,不按 0 计入“省下”。

## 结论边界

这不是无文档项目的首次接入测试,不包含初始化成本,不能据此推断长期回本。内置四个任务集中在 metrics 模块,属于窄范围试点。即使 `index_effect` 为正,也只说明在该仓库、该模型、该任务集上观察到配对差值,不是普遍节省率。
