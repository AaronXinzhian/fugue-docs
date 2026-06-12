# fugue-docs — 赋格文档

**简体中文** | [English](README_EN.md)

> "The map IS the terrain. The terrain IS the map."
>
> 代码是机器相,文档是语义相,两相必须同构;任一相变化必须在另一相显现,否则视为未完成。

一个 Claude Code **skill**,把「GEB 分形文档协议」变成 AI 编程的日常工作方式:三级分形索引(L1 项目 / L2 文件夹 / L3 文件头)+ 强制回环检查 + 机器可验证的同构性,用来对抗 AI 辅助开发时代的项目熵增——代码越写越乱、文档永远滞后。

## 思想来源与致谢

本项目的协议思想完全来自 **赵纯想(chunxiang)** 提出的「GEB 分形文档系统协议」,其灵感源于侯世达《哥德尔、埃舍尔、巴赫》。原版官方实现(CLI + Claude Code 插件 + VSCode 扩展)见 [Claudate/project-multilevel-index](https://github.com/Claudate/project-multilevel-index)(MIT)。

fugue-docs 是该协议的**独立实现**:未使用原仓库任何代码,以 skill(方法论注入)而非插件/CLI 的形态重写,并做了若干扩展(见下)。取名「赋格」,因为复调性(Polyphony)正是协议三大特性之一——代码、索引、文档三个声部相互呼应,如赋格曲般自我维护。

## 它做什么

| 场景 | 行为 |
|------|------|
| 进入陌生项目 | 逆向回环:先读 L1 → L2 → L3,再读代码 |
| 项目没有文档结构 | 自底向上初始化(真读代码,禁止编造) |
| 任何代码增删改 | 正向回环:L3 文件头 → L2 文件夹索引 → L1 项目索引,更新完才算完成 |
| 怀疑文档过期 | 跑 `geb_check.py`,违规清单一目了然 |

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
| `python3 scripts/geb_scaffold.py <项目目录>` | 确定性脚手架(见下),`--dry-run` 预览 |

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
└── evals/evals.json               # 测试用例与断言(用 skill-creator 评测框架可复跑)
```

### 同构性检查器(独立可用,零依赖)

```bash
python3 scripts/geb_check.py /path/to/project          # 人类可读报告
python3 scripts/geb_check.py /path/to/project --json   # 供 CI / 脚本使用,违规时退出码非 0
```

检查:L1 存在性、L2 覆盖率、L3 标签齐全度、索引清单与实际文件对账(缺漏条目 + 幽灵条目)。

### 确定性脚手架(大项目初始化提速)

```bash
python3 scripts/geb_scaffold.py /path/to/project           # 生成骨架(幂等,绝不覆盖已有内容)
python3 scripts/geb_scaffold.py /path/to/project --dry-run # 只预览
```

借鉴原版 CLI 的静态分析思路,但分工不同:**机器只填机器擅长的**——`[INPUT]`(import 分析)、`[OUTPUT]`(导出分析,Python 走 AST,其余语言走模式匹配)、目录树、依赖图初稿由脚本秒级生成;`[POS]`、模块定位等语义判断一律留 `TODO(语义)` 占位,由 AI 真读代码后补全。大项目初始化从"逐文件精读"降为"补语义",省一大半 token,且不产生机器编造的假语义。

### 硬约束模式(可选,推荐)

把回环检查变成 Claude Code 的 Stop 钩子——只要项目根目录有 `PROJECT_INDEX.md`(即已采用协议),Claude 在两相不同构时**无法结束任务**,违规清单会被回灌迫使它完成回环;未采用协议的项目零打扰。加入 `~/.claude/settings.json`:

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

## 不用 Claude Code?照样能用

协议本身与工具无关,三个组件按依赖强度递减:

1. **方法论注入(任何 AI 工具)**:把 [SKILL.md](SKILL.md) 的正文作为系统提示词 / 项目规则注入——Cursor 放 `.cursorrules`,OpenAI Codex CLI 放 `AGENTS.md`,其他工具放各自的规则文件。模型相(Claude/GPT/DeepSeek/Grok)不影响协议本身。
2. **同构检查(任何环境)**:`geb_check.py` 零依赖,只要有 Python 3 就能跑,可挂任意 CI。
3. **硬约束(任何 git 仓库)**:Claude Code 用户用上面的 Stop 钩子;其他工具的用户用 git 提交钩——把 [scripts/git-pre-commit-hook.sh](scripts/git-pre-commit-hook.sh) 复制为项目的 `.git/hooks/pre-commit` 并加执行权限,两相不同构时提交直接被拒,无论代码是谁(人或哪家 AI)写的。

```bash
cp ~/.claude/skills/fugue-docs/scripts/git-pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## 相对原版协议的扩展

1. **同构从口号变成机器验证**:`geb_check.py` 让"两相同构"可被脚本断言,可挂 CI。
2. **硬约束钩子**:回环检查不再依赖模型自觉,Stop 钩子使其成为收工的必要条件。
3. **度的把握**:明确不给生成文件/配置/vendored 依赖加文档、极小项目豁免 L2——文档的成本必须低于它消除的歧义,否则协议自身制造熵。
4. **自底向上初始化**:先真读代码写 L3,再汇总成 L2,最后 L1,杜绝凭文件名编造假文档。
5. **降级验证**:沙箱禁止执行脚本时按检查器逻辑人工对账并如实声明。
6. **回环汇报行**:每次任务结束附 `GEB 回环:L3 ✓ | L2 ✓ | L1 —`,同步情况一眼可见。
7. **确定性脚手架**:机器相分析(INPUT/OUTPUT)交给静态分析秒出,语义相(POS/职责)留 TODO 给 AI——两相分工,各司其职。

## 实测

3 个场景 × 有/无 skill 对照(Claude Code 子代理独立执行,断言脚本自动评分):带 skill 16/16 全过;基线 15/16——差异点在"无文档项目初始化"(基线不写机器可读的 L3 文件头)。有意思的副发现:项目一旦建立了 GEB 结构,连不带 skill 的 AI 都会照着 `[PROTOCOL]` 自指声明维护文档——**协议建立后即可自我维持**,这正是自指性的设计意图。

## License

[MIT](LICENSE)。协议思想归属赵纯想,《哥德尔、埃舍尔、巴赫》归属侯世达,本仓库代码与文本为独立创作。
