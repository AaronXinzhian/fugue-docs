<div align="center">

<img src="assets/logo.png" alt="fugue-docs logo" width="280">

# fugue-docs — 赋格文档

**简体中文** | [English](README_EN.md) | [日本語](README_JA.md)

> "The map IS the terrain. The terrain IS the map."

</div>

> 代码是机器相,文档是语义相,两相必须同构;任一相变化必须在另一相显现,否则视为未完成。

一个 Claude Code **skill**,把「GEB 分形文档协议」变成 AI 编程的日常工作方式:三级分形索引(L1 项目 / L2 文件夹 / L3 文件头)+ 强制回环检查 + 机器可验证的同构性,用来对抗 AI 辅助开发时代的项目熵增——代码越写越乱、文档永远滞后。

## 思想来源与致谢

本项目的协议思想完全来自 **赵纯想(chunxiang)** 提出的「GEB 分形文档系统协议」,其灵感源于侯世达《哥德尔、埃舍尔、巴赫》。原版官方实现(CLI + Claude Code 插件 + VSCode 扩展)见 [Claudate/project-multilevel-index](https://github.com/Claudate/project-multilevel-index)(MIT)。

fugue-docs 是该协议的**独立实现**:未使用原仓库任何代码,以 skill(方法论注入)而非插件/CLI 的形态重写。取名「赋格」,因为复调性(Polyphony)正是协议三大特性之一——代码、索引、文档三个声部相互呼应,如赋格曲般自我维护。

## 核心能力

### 工作方式(自动应用,无需命令)

| 场景 | 行为 |
|------|------|
| 进入陌生项目 | 逆向回环:先读 L1 → L2 → L3,再读代码 |
| 项目没有文档结构 | 脚手架生成骨架 + 自底向上补语义(真读代码,禁止编造) |
| 任何代码增删改 | 正向回环:L3 文件头 → L2 文件夹索引 → L1 项目索引,更新完才算完成 |
| 怀疑文档过期 | 跑 `geb_check.py`,违规清单一目了然 |

### 六个设计要点

1. **同构性是可验证的,不是口号**:`geb_check.py` 做四类检查——L1 存在性、L2 覆盖率、L3 标签齐全度、索引清单与实际文件对账(缺漏条目 + 幽灵条目),退出码非 0 即两相不同构,可直接挂 CI。
2. **回环是硬约束,不靠模型自觉**:三层闸门按需启用——Claude Code 的 Stop 钩子(收工前拦)、git pre-commit 钩(入库前拦)、CI(合并前拦)。详见下文"硬约束模式"。
3. **两相分工的脚手架**:机器相分析(依赖/导出)交给静态分析秒级生成,语义相(定位/职责)留 `TODO(语义)` 给 AI 真读代码后补全——机器绝不假装理解语义。
4. **度的把握,防止协议自身制造熵**:生成文件、配置、vendored 依赖不加头;极小项目豁免 L2;文档的成本必须低于它消除的歧义。
5. **自底向上初始化**:L3 来自真读代码,L2 是 L3 的汇总,L1 是 L2 的汇总——每一层都有事实依据,杜绝凭文件名编造的假文档(假文档比没有文档更糟)。
6. **透明可审计**:每次任务结束附一行 `GEB 回环:L3 ✓ | L2 ✓ | L1 —`;沙箱禁止执行脚本时按检查器逻辑人工对账并如实声明。

## 安装

方式一,插件市场(推荐,在 Claude Code 里两行命令):

```
/plugin marketplace add AaronXinzhian/fugue-docs
/plugin install fugue-docs@fugue-docs
```

方式二,手动安装为个人 skill:

```bash
git clone https://github.com/AaronXinzhian/fugue-docs.git
cp -r fugue-docs ~/.claude/skills/fugue-docs
```

## 使用方法

装好后**无需记任何命令**——这是 skill 形态与命令行工具的本质区别:

- **自动触发**:在任何项目里让 Claude 新增/修改/删除/重命名代码,它会自动执行协议(改完代码即回环更新 L3→L2→L1);要求"初始化文档"、"梳理项目结构"、"文档和代码对不上了"等也会自动触发。
- **手动调用 `/fugue-docs`**:这不是系统内置命令——Claude Code 会给每个已安装的 skill 自动生成同名斜杠命令。想明确指定走协议时输入 `/fugue-docs` 加上你的要求即可,例如 `/fugue-docs 给这个项目建索引`。
- **命令行工具**(不依赖 AI,可单独使用):

| 命令 | 作用 |
|------|------|
| `python3 scripts/geb_check.py <项目目录>` | 同构性检查,`--json` 供 CI 使用 |
| `python3 scripts/geb_scaffold.py <项目目录>` | 确定性脚手架,`--dry-run` 预览 |

## 组件

```
fugue-docs/
├── SKILL.md                       # 协议本体(教义、场景路由、回环流程、戒律)
├── references/templates.md        # L1/L2 模板 + 13 种语言的 L3 文件头模板
├── scripts/geb_check.py           # 同构性检查器(可独立用于任何项目/CI)
├── scripts/geb_scaffold.py        # 确定性脚手架(静态分析生成骨架)
├── scripts/geb_stop_hook.py       # Claude Code Stop 钩子:回环不完成不许收工
├── scripts/git-pre-commit-hook.sh # git 提交钩:跨工具硬约束
├── .claude-plugin/                # 插件市场分发清单
└── evals/evals.json               # 测试用例与断言(可复跑)
```

### 确定性脚手架(大项目初始化提速)

```bash
python3 scripts/geb_scaffold.py /path/to/project           # 生成骨架(幂等,绝不覆盖已有内容)
python3 scripts/geb_scaffold.py /path/to/project --dry-run # 只预览
```

`[INPUT]` 由 import 分析得出,`[OUTPUT]` 由导出分析得出(Python 走 AST 精确解析,JS/TS/Go/Rust/Java 等走模式匹配),目录树与 Mermaid 依赖图初稿同步生成;`[POS]`、模块定位等语义处留 `TODO(语义)` 占位。大项目初始化从"逐文件精读"降为"补语义",省一大半 token,且不产生机器编造的假语义。

### 硬约束模式(可选,推荐)

**Claude Code 用户**:把回环检查注册为 Stop 钩子——只要项目根目录有 `PROJECT_INDEX.md`(即已采用协议),Claude 在两相不同构时**无法结束任务**,违规清单会被回灌迫使它完成回环;未采用协议的项目零打扰。加入 `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$HOME/.claude/skills/fugue-docs/scripts/geb_stop_hook.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

**其他工具用户**:协议与工具无关——

1. **方法论注入(任何 AI 工具)**:把 [SKILL.md](SKILL.md) 正文作为系统提示词 / 项目规则注入(Cursor 放 `.cursorrules`,OpenAI Codex CLI 放 `AGENTS.md`)。模型相(Claude/GPT/DeepSeek/Grok)不影响协议本身。
2. **同构检查(任何环境)**:`geb_check.py` 零依赖,可挂任意 CI。
3. **硬约束(任何 git 仓库)**:复制 [scripts/git-pre-commit-hook.sh](scripts/git-pre-commit-hook.sh) 为项目的 `.git/hooks/pre-commit` 并加执行权限,两相不同构时提交直接被拒,无论代码是人写的还是哪家 AI 写的:

```bash
cp ~/.claude/skills/fugue-docs/scripts/git-pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## 实测数据

评测方法:3 个真实场景 × 有/无 skill 对照,每边由独立的 Claude Code 子代理执行(互不知晓对方存在),16 条断言全部由脚本自动判定而非人工印象;评测用例与评分器随仓库分发,可自行复跑。

| 场景 | 带 skill | 基线 | 耗时(带 / 无) | tokens(带 / 无) |
|------|---------|------|---------------|------------------|
| 旧项目文档体系初始化 | **5/5** | 4/5 | **222s** / 367s | 37.9k / 35.7k |
| 新增功能后的回环维护 | **5/5** | 5/5 | 140s / 139s | 24.4k / 21.8k |
| 删除重构后清理幽灵引用 | **6/6** | 6/6 | 127s / 110s | 24.9k / 21.0k |

三个值得展开的发现:

**① 差异点在"从零建立结构",而且带 skill 反而快 40%。** 基线 AI 也能写出不错的文档(入口文档、目录说明、甚至自己发明检查脚本),但不会产生 L3 机器可读头注释——而这层结构化语义正是让下一个 AI 会话"秒懂"项目的关键。带 skill 的初始化耗时 222s 对基线 367s:协议给了明确的流程,AI 不必每次现场发明一套文档体系。

**② 协议建立后可自我维持——自指性的设计被实测验证。** 在已有 GEB 结构的项目上,连**不带 skill** 的基线 AI 都会照着文档里的 `[PROTOCOL]` 自指声明更新索引(后两个场景基线全过正因如此)。换句话说:skill 负责把结构正确建立起来,结构自己负责活下去;skill 同时兜底"无线索"场景并提供硬约束保证。

**③ 日常维护税极低。** 在已有结构的项目上,带 skill 仅多消耗约 3k tokens(读协议本体的固定开销)、耗时基本持平;配合脚手架,大项目初始化的 token 成本进一步从"全文精读"降为"补语义"。对换来的"任何 AI/新人进项目即刻看懂结构"而言,这笔税很划算。

## License

[MIT](LICENSE)。协议思想归属赵纯想,《哥德尔、埃舍尔、巴赫》归属侯世达,本仓库代码与文本为独立创作。
