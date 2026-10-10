# Token 增量试点

当前实际进展见 [TOKEN_PILOT_RESULTS.md](TOKEN_PILOT_RESULTS.md)。首轮完成两个三组块后预算停止,有自动验收通过的负差值配对,仍没有普遍节省率或已复核净节省结论。

执行器支持两种代理:Codex(`--agent codex`,默认)和 Claude Code(`--agent claude`)。Claude Code 的赋格组以插件形式加载 v2.7 钩子,用来测"钩子化之后流程开销还剩多少";外部仓库任务见下文 [Claude Code 与 docutils](#claude-code-与-docutils)。

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
| `fugue` | 源码原样 | Codex:隔离 HOME 内安装冻结的 skill 副本,并在 `AGENTS.md` 启用。Claude Code:同一副本(含 `hooks/`、`.claude-plugin/`)经 `--plugin-dir` 作为插件加载 |

每次试验在 `strip` 字段记录删除的索引文件数、头部行数、空块数和托管块数,便于复核剥离范围。剥离按字节读写,CRLF 文件保持原样。`noindex` 和 `index` 两组的提示词完全相同,只有工作区不同;Claude Code 三组的提示词完全相同,差别只有工作区和插件。

**防止看到答案或组别**:

- 赋格组安装的 skill 取自 `--skill-ref`。测本仓库时默认等于源码提交 `fdf4810`,且不允许比源码新:更新的 skill 脚本和文档已经实现了这四个任务。旧执行器从当前工作树复制 skill,存在这个泄漏;它的预检没有运行赋格组,已有数据不受影响。测外部仓库时默认用本仓库 `HEAD`。
- 每次试验在系统临时目录下一个随机命名的目录里运行,模型看到的工作目录和 HOME 路径不含任务名、组名或输出目录名;结束后整个目录移入输出目录的 `trial-NNN/`。同一时刻只存在当前试验的临时目录。
- 源码归档或 skill 归档中只要出现与隐藏验收文件、参考补丁内容相同的文件,执行器就拒绝运行。
- `workspace-write` 沙箱不限制读取,所以分析器会把工作区、skill 和系统目录以外的读取路径单列为 `outside_workspace_reads`,汇总里列出出现过这类读取的试验,供人工复核。

## 任务

任务定义在 JSON 任务文件中(schema `geb.token-pilot.tasks.v1`)。内置任务在 [fixtures/token-pilot/tasks.json](fixtures/token-pilot/tasks.json),是 `fdf4810` 上的四个计量小任务,每个任务有两种提示词:

- `symptom`(默认):只描述可观察到的问题和需要输出的字段名,不点名文件或函数,模型需要自己定位代码。
- `named`:原试点措辞,直接点名文件和函数,用于与 2026-09-30 的预检保持可比。

验收脚本和参考补丁放在任务文件目录,不进入试验工作区。模型结束后,执行器先保存 `changes.patch`,再准备验收:把 `reset_before_validation` 匹配的已跟踪文件(例如既有测试代码)恢复到快照,恢复并打上隐藏的 `acceptance_patch`,复制隐藏的 `acceptance_files`;然后依次运行验收命令和回归命令(原有测试),并检查受保护文件没有被改动。被恢复掉的模型改动记在 `acceptance_setup.discarded_edits`,原文仍在 `changes.patch`。

任务文件字段(都可写在顶层作为默认值,或写在单个任务里覆盖):

| 字段 | 作用 |
|------|------|
| `prompts` | `symptom` 与可选的 `named` 提示词 |
| `test_rule` | 拼进提示词的测试要求;默认"不改已有测试文件,新测试放新文件" |
| `acceptance_files` | 结束后复制进工作区的隐藏验收脚本 |
| `acceptance_patch` | 结束后打上的隐藏测试补丁(上游提交的测试改动);补丁涉及的文件先恢复到快照,且不受保护检查约束 |
| `reset_before_validation` | 验收前恢复到快照的已跟踪文件 glob(`**/` 跨目录);防止模型改弱既有测试来通过回归 |
| `protected` | 不允许改动的文件 glob |
| `regression` | 回归命令 |
| `cwd` | 验收与回归命令的运行目录(相对工作区);上游要求在子目录运行测试时必须设置,否则测试可能空跑 |
| `strip_exclude` | 无索引组剥离时跳过的路径前缀 |
| `workspace_ignore` | 写入工作区 `.git/info/exclude` 的规则(子目录归档里缺少上游 `.gitignore` 时补上测试输出等) |
| `source`(仅顶层) | 外部源码:`git`、`commit`、可选 `subdir`、`tree`(子目录树哈希校验)、`overlay`(索引覆盖补丁) |

### 零成本任务校验

```bash
python3 -B evals/run_token_pilot.py --verify-tasks
```

不调用模型。对每个任务,分别在 `noindex` 和 `index` 两种工作区上检查:每一条验收命令都先失败(任务没有预先被解决,也没有空跑的验收)、回归在原样快照上先通过(剥离没有破坏原有测试;回归在打隐藏测试补丁之前运行)、隐藏测试补丁能打上、参考补丁能打上、打上后全部通过。`fugue` 组与 `index` 组工作区相同,不重复校验。

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

`{python}` 会替换为当前解释器;`acceptance_files`、`acceptance_patch`、`reference_patch` 和 `overlay` 的路径相对任务文件解析。源提交里必须已有索引文件,否则执行器拒绝运行,因为这里测的是已有索引的热启动场景。

任务文件声明 `source` 且没有给 `--source-repo` 时,执行器在 `--source-cache`(默认 `~/.cache/fugue-pilot/sources`)里浅拉取固定提交,取出子目录,核对树哈希,提交一次"上游原样",再打上索引覆盖补丁提交一次;之后复用缓存。归档里树内的相对符号链接保留,指向树外的链接、硬链接和设备文件一律拒绝。

## Claude Code 与 docutils

### 要回答的问题

v2.7 把同步、检查、计量交给钩子,模型只在语义缺口出现时补一句。这一轮在 Claude Code 上回答:已有索引时,装上钩子插件(`fugue`)比只有索引(`index`)多花还是少花。先跑 A/A 确定噪声,再跑 `index` 对 `fugue` 两组;`DESIGN=three-arm` 时加上 `noindex`。

### 外部仓库与任务

自托管仓库太小且混杂(见上),这一轮改用 [Docutils](https://github.com/docutils/docutils):固定在 2026-08-31 的提交 `92a50f6f7`,只取 `docutils/` 子目录(约 760 个文件,包内 129 个代码文件,测试 2,307 项,约 3 秒跑完,只依赖标准库)。任务文件在 [fixtures/docutils-pilot/tasks.json](fixtures/docutils-pilot/tasks.json):

| 任务 | 上游提交 | 需要定位的位置 | 隐藏验收 |
|------|----------|----------------|----------|
| `language-fallback` | `f7b99cdf7` | 语言模块加载器 | `test_language.py` |
| `source-date-epoch` | `ce2dcf47b` | `date` 指令与页脚时间戳(两个模块) | 两个测试模块 |
| `figure-figalign` | `35b59ca7d` | 解析器设置与 `figure` 指令 | `test_figures.py` |
| `rfc-base-url` | `0b812bdc5` | 解析器设置默认值与内联标记的 RFC 模板 | 两个测试模块、功能测试期望输出、帮助文本 |

四个上游提交都在 2026 年 9–10 月,晚于快照;所用模型的训练截止若更早,就不可能背过答案(报告里记录模型名,便于事后核对)。症状提示词只描述行为和接口名(设置名、选项名、URL),不点名文件或函数。隐藏验收是上游提交的测试改动(`acceptance_patch`);`figure-figalign` 只取 figure 测试,因为帮助文本取决于选项怎么措辞,参考补丁里带上上游的帮助文本。既有测试代码 `test/**/*.py` 在验收前恢复到快照,模型可以按需加改测试,但不能靠改弱旧测试通过回归;测试数据(帮助文本、期望输出)的改动保留。回归是在 `test/` 下运行的整套 `alltests.py`(含功能测试的端到端输出比对)。

L1 里写了"在 `test/` 下运行 `alltests.py`";无索引组同样能从 docutils 自带的 README 和 `docs/dev/testing.rst` 读到这一点,三组设计里它算作索引收益的一部分。索引覆盖补丁 [indexes.patch](fixtures/docutils-pilot/indexes.patch) 只为 `docutils/` 包建索引:1 个 L1、19 个 L2、129 个文件头,语义字段是读代码后写的通用描述,不针对任务。`test/`、`docs/`、`tools/` 不建索引,L1 里写明;所以对整个仓库跑 `geb_check --complete` 会列出这些目录,包内检查是干净的。这也意味着:模型在 `test/` 下新建测试文件时,钩子会给它补文件头骨架并要求补 `[POS]`,这是当前钩子在部分接入项目里的真实行为,计入流程开销。

零成本校验(首次运行会下载固定提交,之后复用缓存):

```bash
bash evals/run-claude-pilot.sh plan
```

`index` 与 `noindex` 两种工作区里,四个任务都满足:快照本身回归通过、每一条验收命令都先失败、参考补丁能打上、打上后验收与回归全部通过。`noindex` 工作区与上游文件逐字节相同。所有命令在 `test/` 下运行(任务文件 `cwd`),与上游测试说明一致:从仓库根目录运行时,功能测试找不到输入文件,会"通过"0 项,这个问题由独立复核发现,`rfc-base-url` 的功能测试验收曾因此在修复前就显示通过。

### 隔离

- 每次试验使用新建的 `CLAUDE_CONFIG_DIR` 和 HOME:不读你的 `CLAUDE.md`、记忆、设置、已装插件和对话记录。
- 环境变量走白名单:只传 `PATH`、`SHELL`、语言与时区、临时目录、证书与代理设置(加 `LC_*`),其余一律不传,包括 `PWD`、调用脚本的 `TASKS`/`PILOT_OUTPUT` 等设置和其他工具的令牌,模型运行 `env` 也看不到任务名或输出目录。确有需要的变量用 `--claude-env 名字` 显式放行。HOME 换掉后 pyenv/asdf 的 shim 会找不到安装,执行器会把 `PYENV_ROOT`/`ASDF_DATA_DIR` 指回真实位置。
- 凭据只用一种。钥匙串登录在私有配置目录里不可见,所以先运行一次 `claude setup-token`,再 `export CLAUDE_CODE_OAUTH_TOKEN=...`。有令牌时只传令牌:Claude Code 里 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 优先级更高,一起传就会按 API 计费,所以它们不会传进试验。没有令牌时才用 `ANTHROPIC_API_KEY`(连同 `ANTHROPIC_BASE_URL`)。报告的 `claude_credential` 记录用了哪一种,`claude_session.api_key_source` 记录 CLI 自己报告的来源。
- 权限:`acceptEdits`;网页工具拒绝;`-p` 模式下需要确认的操作一律拒绝,原因记入 `claude_session.denial_reasons`。
- 沙箱(`--claude-sandbox`,默认 `on`):Bash 在 Claude Code 沙箱里自动放行,写入限于工作区,网络不通,不允许逃逸;沙箱起不来时拒绝启动(`failIfUnavailable`),而不是悄悄降级。`on` 还禁止 Bash 读取答案所在的目录(本仓库及其中的隐藏补丁、任务目录、源码缓存及其带索引的副本、本次输出目录、`~/fugue-pilot` 下历史结果、你的 `~/.claude` 与 `~/.codex`),并在 Bash 环境里清掉凭据变量。没有用 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`:实测它会把权限模式强制改回 default。`basic` 只用早期版本也认识的三个沙箱开关,**没有读取禁区和凭据清除**:Bash 能读到之前试验的 `changes.patch` 和隐藏补丁,结果只能算较弱证据,报告的 `claude_isolation` 会写明,`evidence_level` 带 `_weaker_isolation` 后缀;`off` 不用沙箱、直接放行 Bash,只适合一次性虚拟机。macOS 自带沙箱;Linux 需要 `bubblewrap` 和 `socat`。
- **零成本自检**:实测发现,沙箱设置里有一个键 CLI 不认识时,整段沙箱设置会被静默丢掉,没有任何提示;沙箱缺依赖时,不设 `failIfUnavailable` 也只是降级运行。所以每次 `--execute` 之前(以及 `run-claude-pilot.sh plan`),执行器先用本地假接口(`claude_mock_api.py`,只监听 127.0.0.1,用假 key,不碰你的凭据、不调用模型)驱动你机器上真实的 `claude` 跑一次赋格组式会话,工作区就是第一个任务的有索引工作区,逐项确认:运行完成、赋格插件加载、钩子运行且在新文件缺语义时拦截一次、Bash 能在工作区内运行、沙箱里 `python3` 和 `git` 能用(macOS 的 `/usr/bin/python3`、`git` 是转调 `xcrun` 的壳,PATH 上的解释器也可能落在读取禁区里)、任务自己的回归命令在沙箱里能跑通、沙箱已生效、相对路径写到工作区外被沙箱拦下,`on` 模式下还确认读取禁区与凭据清除生效。任何一项失败就不开始付费试验,结果与提示写在报告的 `claude_selftest`(或 `*.selftest.json`)。不建议使用 `--skip-claude-selftest`。
- 托管设置(managed settings)、内置插件和 MCP 不受私有配置目录影响。执行器把第一次试验的 CLI 版本、模型、凭据来源、插件、工具和 MCP 列表记为 `claude_environment`;之后任何一次不同(赋格插件本身和只因技能出现的 `Skill` 工具除外)就记为 `setup_failed:environment_changed` 并停止,例如你在实验中途升级了 Claude Code。
- 赋格组必须在 init 事件里看到 `fugue-docs` 插件,且钩子在私有数据目录留下会话状态;其他组不得加载它。CLI 在 stderr 报告沙箱不可用时记为 `setup_failed:sandbox_unavailable`。不满足即停止,不浪费后续调用。
- 插件目录不加入 `--add-dir`:钩子流程不需要模型读技能文件,加了反而会在赋格组的环境说明里多出一个目录。
- 试验目录正常结束后会移进输出目录;被强行中断(断电、`kill -9`)时会留在系统临时目录,里面可能是已经解完的工作区,新试验用 `ls ..` 就能看到。所以 `--execute` 前若发现残留的 `run-*` 试验目录,执行器拒绝运行并列出它们,请检查后删除。同理,同一台机器上不要同时跑两轮试点:后启动的一轮会看到前一轮正在进行的试验目录而拒绝运行。
- 已知限制:CLI 的静态检查会在执行前拒绝写工作区外绝对路径的命令(提示"需要批准"),这与沙箱无关,各组相同,只记录不判故障;模型若自行提交,`changes.patch`、测试重置和验收都以快照提交为基准,`model_commits` 记录提交次数。自检走的是假 key 登录路径,订阅令牌加私有配置目录这条路径要到第一次付费试验才真正用上,失败时会作为 `infrastructure_error` 立即停止,损失很小。没有断点续跑:订阅额度用尽会干净地停止,但不能接着写同一份报告,`REPEATS` 宜让一轮在一个额度窗口内跑完。Linux 上 bubblewrap 会在运行期间往工作区放几个空的占位文件(`.zshrc`、`.mcp.json` 等),模型的 `git status` 能看到;macOS 没有这个现象。

### 用量口径

完整用量取自唯一的 `result` 事件的 `modelUsage`,包含子代理和后台小模型;缺 `modelUsage` 时退回只含主线程的 `usage`,`usage_coverage` 标为 `main_thread_only`。换算:未缓存输入 = 新输入 + 写缓存,缓存输入 = 读缓存;主指标仍是未缓存输入 + 输出。另报 `cache_creation_input_tokens` 和 `cost_usd`(CLI 按标价算出的金额;订阅登录时只是用量的标价折算,不是实际扣费)。设了美元预算却拿不到 `cost_usd` 时停止(`cost_unknown`),免得预算失效。没有 `result` 事件(超时、崩溃)时,按消息 ID 去重累加助手消息用量,只作下限,并立即停止。`--max-turns`、`--max-budget-usd` 是单次上限,触发时状态为 `agent_error_*`,计入该组失败与成本。

**故障不算成绩。** 实测接口拒绝(例如 429、额度用尽)时 CLI 给出 `subtype: success` 加 `is_error: true`,执行中断时是 `error_during_execution`。接口报错里,认证、计费、额度、限流、过载和服务端错误(看重试事件的错误类别和结果文字)记为 `infrastructure_error`;其他接口报错(例如请求过长,可能由某组的行为造成)记为 `agent_api_error`,算该组失败,实验继续。执行中断也记为 `infrastructure_error`。验收后执行器自身的 git 或文件错误记为 `harness_failed`(用量照常保留)。`infrastructure_error`、`harness_failed` 与 `setup_failed` 立即停止实验,并且不进入 `failure_aware` 的胜负统计(单列 `skipped_infrastructure`),免得把订阅额度用尽算成某一组输了。接口重试的错误类别记在 `claude_session.api_errors`。

每次试验另记 `claude_session`(版本、模型、凭据来源、插件、工具、轮数、权限拒绝及原因)和赋格组的 `hooks`(钩子会话数、Stop 次数、拦截次数与字数、自动写入数、提示过的缺口数、钩子错误)。Claude Code 把赋格的拦截提示显示为 "Stop hook error",这是它对 `decision: block` 的标注,不是钩子出错;真正的钩子异常在 `hooks.errors`。

### 离线验证

不调用模型的部分:假 `claude` 端到端测试(`test_token_pilot.py`)覆盖插件参数、白名单环境与凭据选择、相同提示词、隐藏测试补丁与测试重置、模型自行提交、用量换算、接口故障与缺金额停止、缺 result 停止、轮数上限、插件缺失停止;另有一个会真正走 HTTP 的假 `claude` 覆盖自检的通过与失败。另用本地模拟的 Messages API 驱动真实 `claude` 2.1.295(Linux,装了 bubblewrap)跑通:三组试验、插件加载、SessionStart 提示、按工具归属记录写入、Stop 钩子补骨架并拦截一次、沙箱内测试可跑而网络、工作区外写入与禁区读取被拦、凭据从 Bash 环境清除、`modelUsage` 解析和定位折算都与真实事件流一致;去掉 bubblewrap 后 `on` 模式拒绝启动、`basic` 模式被自检判为沙箱未生效。这些只证明管线可用,不含任何节省结论。

### 运行

```bash
# 0. 安装 Claude Code(官方安装脚本,需要 macOS 13+),然后新开一个终端窗口确认版本
curl -fsSL https://claude.ai/install.sh | bash
claude --version

# 1. 零成本:下载并校验任务、本机 Claude Code 自检、保存计划(没装 Claude Code 时只跳过自检)
bash evals/run-claude-pilot.sh plan

# 2. 一次性:生成一年有效的令牌并导出(需要 Pro、Max、Team 或 Enterprise 订阅;令牌不要写进仓库或日志)
claude setup-token
export CLAUDE_CODE_OAUTH_TOKEN=<上一步输出>

# 3. A/A 噪声:index 组跑两遍,默认 4 个任务 × 2 次 = 16 次调用
bash evals/run-claude-pilot.sh aa

# 4. 按 A/A 的 power_hint 定 REPEATS,再跑 index 对 fugue
REPEATS=3 bash evals/run-claude-pilot.sh compare
```

默认 `claude-sonnet-5-5`、Claude 默认推理强度、每次 1500 秒、单次 3 美元上限、整轮 25 美元标价上限(每个完整块后检查,最多超出一个块)。可用 `MODEL`、`EFFORT`、`REPEATS`、`TASKS`、`DESIGN`、`COST_BUDGET`、`TRIAL_BUDGET`、`TIMEOUT`、`MAX_TURNS`、`SANDBOX`、`PILOT_CLAUDE`、`PILOT_OUTPUT`、`SOURCE_CACHE` 修改;自检失败时按提示处理(例如 `SANDBOX=basic` 重跑 `plan`),不要跳过;没有令牌时 `aa`/`compare` 直接退出,不做任何事。结果在 `~/fugue-pilot/claude-<阶段>-<时间>-<进程号>/`,含 `report.json` 与 `navigation.json`;私有输出包含工作副本、事件流和隔离配置目录里的对话记录,不要整目录上传。

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

不调用模型。可直接分析已有私有输出目录,包括 2026-09-30 预检保留在本地的 `events.jsonl`(旧标签 `baseline`/`fugue` 自动按 `workflow_effect` 配对)。没有 `report.json` 时只输出逐次结果。Claude Code 的 stream-json 会先折算成同样的条目:`Read` 记为读取,`Grep` 记为搜索,`Glob` 记为列目录,`Skill` 记为读取技能文档,编辑与写文件工具记为文件修改,子代理的工具调用一并计入。Claude 的编辑工具是原子的,报错的编辑(找不到原文、权限拒绝)没有写入,既不算修改也不让首次修改边界变得不确定。

命令分类是启发式的:展开 `bash -lc` 包装,按管道和 `&&` 拆分;先判修改(`apply_patch`、`sed -i`、`tee`、重定向写入、内联 Python 写文件、`file_change` 事件;写入临时目录或未展开的 shell 变量路径不算首次修改),再判运行测试(`pytest`、`python -m unittest`、直接运行 `test_*.py`),然后是 `cat`/`sed`/`head` 等读取和 `rg`/`grep` 搜索;读到 `test_*.py` 仍记为读取。以 Python 运行 `geb_*.py` 记为赋格流程,运行 `alltests.py` 也记为测试。支持 Codex `exec --json` 事件流(兼容 `type` 与旧 `item_type` 字段)和 Claude Code `-p --output-format stream-json --verbose`;其他代理需要另写适配。

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

- 单次调用前后没有共享对话,隔离 HOME/CODEX_HOME(Claude Code 为 HOME/CLAUDE_CONFIG_DIR),忽略用户 config,不继承原会话 ID;各组模型、推理强度、权限、任务提示词和验收相同。
- 源码 archive、skill 提交与内容、任务文件、验收文件和参考补丁都绑定 SHA-256;执行器自身也记录摘要。
- `changes.patch` 包含模型新建的文件,在复制隐藏验收文件之前生成。
- Codex 使用独立 CLI 的完整 `turn.completed` 用量,Claude Code 使用 `result.modelUsage`;输入包含缓存,总量为输入加输出,包含技能阅读、钩子提示、修改、文档维护、代理自测和最终回复。外部验收不消耗模型 token。
- 保留隔离会话日志,不使用 `--ephemeral`;超时时可单列已上报的 `partial_usage`,它不替代完整用量,也不进入节省统计。
- 程序限时终止整个进程组。失败和超时不删除,计入尝试数和实验成本。
- 自动通过不等于独立语义复核。结果等级为 `test_passed_pairs_only_pending_independent_semantic_review`,不写入日常账本的已复核节省。

私有输出包含工作副本和原始事件,不要整目录提交或上传。每次运行临时链接本地登录文件,调用结束即移除。公开时仅发布脱敏的 `report.json` 数值、测试方法和经检查的差异摘要。模型尚未完成时没有最终遥测,其消耗保持未知,不按 0 计入“省下”。

## 结论边界

这不是无文档项目的首次接入测试,不包含初始化成本,不能据此推断长期回本。内置四个任务集中在 metrics 模块,属于窄范围试点。即使 `index_effect` 为正,也只说明在该仓库、该模型、该任务集上观察到配对差值,不是普遍节省率。
