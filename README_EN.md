# fugue-docs

[简体中文](README.md) | **English**

> "The map IS the terrain. The terrain IS the map."
>
> Code is the machine phase; documentation is the semantic phase. The two phases must stay isomorphic — any change in one phase that is not reflected in the other means the task is incomplete.

A Claude Code **skill** that turns the *GEB Fractal Documentation Protocol* into an everyday way of working with AI: a three-level fractal index (L1 project / L2 folder / L3 file header) plus a mandatory update loop and machine-verifiable isomorphism — built to fight project entropy in the age of AI-assisted coding, where code grows messy and docs always lag behind.

## Origin & Credits

The protocol concept comes entirely from **Zhao Chunxiang (chunxiang)**'s "GEB Fractal Documentation System Protocol", inspired by Douglas Hofstadter's *Gödel, Escher, Bach*. The original official implementation (CLI + Claude Code plugin + VSCode extension) lives at [Claudate/project-multilevel-index](https://github.com/Claudate/project-multilevel-index) (MIT).

fugue-docs is an **independent implementation**: it uses no code from the original repository, re-imagines the protocol as a skill (methodology injection) rather than a plugin/CLI, and adds several extensions (see below). The name "fugue" honors Polyphony — one of the protocol's three core properties: code, indexes, and docs answer each other like voices in a fugue, keeping the project self-maintaining.

## What it does

| Situation | Behavior |
|-----------|----------|
| Entering an unfamiliar project | Reverse loop: read L1 → L2 → L3 before reading code |
| Project has no doc structure | Bottom-up initialization (actually read the code; no fabrication) |
| Any code added / changed / deleted | Forward loop: L3 file header → L2 folder index → L1 project index; the task is not "done" until docs are updated |
| Docs suspected stale | Run `geb_check.py` for a violation report |

## Install

Option 1 — plugin marketplace (recommended, two commands inside Claude Code):

```
/plugin marketplace add AaronXinzhian/fugue-docs
/plugin install fugue-docs@fugue-docs
```

Option 2 — manual install as a personal skill:

```bash
git clone https://github.com/AaronXinzhian/fugue-docs.git
cp -r fugue-docs ~/.claude/skills/fugue-docs
```

## Usage

Once installed there are **no commands to remember** — that is the point of the skill form factor:

- **Automatic**: whenever Claude creates / modifies / deletes / renames code in any project, the protocol applies automatically (code change → L3 → L2 → L1 loop). Requests like "initialize docs", "map out this project", or "the docs are out of sync" also trigger it.
- **Manual `/fugue-docs`**: not a built-in command — Claude Code auto-generates a slash command for every installed skill. Type `/fugue-docs` plus your request to invoke the protocol explicitly, e.g. `/fugue-docs index this project`.
- **CLI tools** (AI-independent, usable standalone):

| Command | Purpose |
|---------|---------|
| `python3 scripts/geb_check.py <project>` | Isomorphism check; `--json` for CI |
| `python3 scripts/geb_scaffold.py <project>` | Deterministic scaffolder (see below); `--dry-run` to preview |

## Components

```
fugue-docs/
├── SKILL.md                       # The protocol (doctrine, routing, loops, commandments)
├── references/templates.md        # L1/L2 templates + L3 header templates for 13 languages
├── scripts/geb_check.py           # Isomorphism checker (standalone, CI-friendly)
├── scripts/geb_scaffold.py        # Deterministic scaffolder (static analysis)
├── scripts/geb_stop_hook.py       # Claude Code Stop hook: no loop, no finish
├── scripts/git-pre-commit-hook.sh # git pre-commit hook: tool-agnostic hard constraint
├── .claude-plugin/                # marketplace distribution manifests
└── evals/evals.json               # Test cases & assertions (replayable with skill-creator)
```

### Isomorphism checker (standalone, zero dependencies)

```bash
python3 scripts/geb_check.py /path/to/project          # human-readable report
python3 scripts/geb_check.py /path/to/project --json   # for CI / scripts; non-zero exit on violations
```

Checks: L1 existence, L2 coverage, L3 tag completeness, and ledger reconciliation between indexes and actual files (missing entries + ghost entries).

### Deterministic scaffolder (fast initialization for large projects)

```bash
python3 scripts/geb_scaffold.py /path/to/project           # generate skeleton (idempotent; never overwrites)
python3 scripts/geb_scaffold.py /path/to/project --dry-run # preview only
```

Inspired by the original CLI's static analysis, with a different division of labor: **the machine only fills in what machines are good at** — `[INPUT]` (import analysis), `[OUTPUT]` (export analysis; AST for Python, pattern matching for other languages), the directory tree, and a draft dependency graph are generated in seconds. Semantic judgments — `[POS]`, module roles — are left as `TODO` placeholders for the AI to fill in after actually reading the code. Initialization of a large project drops from "deep-read every file" to "fill in the semantics": most of the tokens saved, and no machine-fabricated fake semantics.

### Hard-constraint mode (optional, recommended)

Turn the loop check into a Claude Code Stop hook — in any project whose root has a `PROJECT_INDEX.md` (i.e. it adopted the protocol), Claude **cannot finish its turn** while the two phases are out of sync; the violation list is fed back until the loop is completed. Projects that haven't adopted the protocol are never disturbed. Add to `~/.claude/settings.json`:

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

## Not using Claude Code? It still works

The protocol is tool-agnostic. Three components, in decreasing order of coupling:

1. **Methodology injection (any AI tool)**: drop the body of [SKILL.md](SKILL.md) into your system prompt / project rules — `.cursorrules` for Cursor, `AGENTS.md` for OpenAI Codex CLI, or your tool's equivalent. The underlying model (Claude/GPT/DeepSeek/Grok) doesn't matter.
2. **Isomorphism check (any environment)**: `geb_check.py` is zero-dependency Python 3; wire it into any CI.
3. **Hard constraint (any git repo)**: Claude Code users get the Stop hook above; everyone else can use the git hook — copy [scripts/git-pre-commit-hook.sh](scripts/git-pre-commit-hook.sh) to your project's `.git/hooks/pre-commit` and make it executable. Out-of-sync commits are rejected, no matter who (human or which AI) wrote the code.

```bash
cp ~/.claude/skills/fugue-docs/scripts/git-pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## Extensions over the original protocol

1. **Isomorphism became machine-verifiable**: `geb_check.py` turns "the two phases must be isomorphic" from a slogan into a scriptable assertion, CI-ready.
2. **Hard-constraint hooks**: the loop no longer relies on model discipline — the Stop hook makes it a precondition for finishing.
3. **Proportionality rules**: no headers for generated files / configs / vendored deps; tiny projects may skip L2 — documentation must cost less than the ambiguity it removes, or the protocol itself becomes entropy.
4. **Bottom-up initialization**: write L3 from actually reading code, summarize into L2, then L1 — no fabricated docs from filenames.
5. **Degraded verification**: when sandboxes forbid script execution, fall back to manual reconciliation following the checker's logic, and say so honestly.
6. **Loop report line**: every task ends with `GEB loop: L3 ✓ | L2 ✓ | L1 —`, sync status at a glance.
7. **Deterministic scaffolder**: machine-phase analysis (INPUT/OUTPUT) handled by static analysis in seconds; semantic phase (POS/roles) left as TODOs for the AI — each phase served by what does it best.

## Field test

3 scenarios × with/without the skill (independent Claude Code subagents, script-graded assertions): 16/16 with the skill; 15/16 baseline — the gap appears in "initialize an undocumented project" (baselines don't write machine-readable L3 headers). A fun side-finding: once a project has the GEB structure, even an AI *without* the skill maintains the docs by following the `[PROTOCOL]` self-reference lines — **the protocol sustains itself once established**, exactly as the self-reference property intends.

## License

[MIT](LICENSE). The protocol concept belongs to Zhao Chunxiang; *Gödel, Escher, Bach* belongs to Douglas Hofstadter; the code and text in this repository are independent work.
