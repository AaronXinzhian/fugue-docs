#!/usr/bin/env bash
# [INPUT]: 依赖 (未检出外部依赖)
# [OUTPUT]: no-model verification and plans; A/A noise or index-vs-fugue Claude Code trials with private report.json/navigation.json
# [POS]: fugue-docs evaluation entry point for Claude Code pilots on the external docutils repository
# [PROTOCOL]: Keep token-pilot.md, FOLDER_INDEX.md and test_first_round.py in sync

set -euo pipefail
umask 077

stage="${1:-plan}"
if [[ "$#" -gt 1 || ( "$stage" != "plan" && "$stage" != "aa" && "$stage" != "compare" && "$stage" != "--help" ) ]]; then
    printf 'Usage: bash evals/run-claude-pilot.sh [plan|aa|compare]\n' >&2
    exit 2
fi
if [[ "$stage" == "--help" ]]; then
    printf '%s\n' \
        'plan     no model calls: fetch docutils once, verify the tasks in both workspaces, run the' \
        '         Claude Code self-test against a local mock API, save the compare plan' \
        'aa       A/A noise: the index arm twice per block (run this first)' \
        'compare  index vs fugue (Claude Code plugin with hooks); DESIGN=three-arm adds noindex' \
        'Needs CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY for aa/compare.' \
        'Settings: MODEL EFFORT REPEATS TASKS DESIGN COST_BUDGET TRIAL_BUDGET TIMEOUT MAX_TURNS SANDBOX' \
        '          PILOT_CLAUDE PILOT_OUTPUT PILOT_PYTHON SOURCE_CACHE' \
        'Defaults: claude-sonnet-5-5, Claude default effort, aa 2 / compare 3 repeats, all four tasks,' \
        '          two-arm, 25 USD per run, 3 USD per trial, 1500 s per trial, SANDBOX=on (on|basic|off)'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${PILOT_PYTHON:-python3}"
tasks_file="$script_dir/fixtures/docutils-pilot/tasks.json"
output="${PILOT_OUTPUT:-$HOME/fugue-pilot/claude-$stage-$(date +%Y%m%d-%H%M%S)-$$}"
task_spec="${TASKS:-figure-figalign language-fallback rfc-base-url source-date-epoch}"
if [[ -z "${task_spec//[[:space:]]/}" ]]; then
    printf 'TASKS must contain at least one task name.\n' >&2
    exit 2
fi
read -r -a task_list <<< "$task_spec"
for target in "$output" "$output.plan.json" "$output.preflight.json" "$output.selftest.json"; do
    if [[ -e "$target" || -L "$target" ]]; then
        printf 'Refusing to overwrite: %s\n' "$target" >&2
        exit 2
    fi
done
if [[ "$stage" != "plan" && -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    printf '%s\n' 'Trials run with a private Claude config directory, so your keychain login is not visible.' \
        'Run `claude setup-token` once, then: export CLAUDE_CODE_OAUTH_TOKEN=<token>' >&2
    exit 2
fi
mkdir -p "$(dirname "$output")"

if [[ "$stage" == "aa" ]]; then
    design=(--design aa --aa-arm index)
    repeats="${REPEATS:-2}"
else
    design=(--design "${DESIGN:-two-arm}")
    repeats="${REPEATS:-3}"
fi
args=(--agent claude --claude "${PILOT_CLAUDE:-claude}" --tasks-file "$tasks_file"
      --model "${MODEL:-claude-sonnet-5-5}" --repeats "$repeats" --timeout "${TIMEOUT:-1500}"
      --max-total-tokens 2000000000 --max-total-cost-usd "${COST_BUDGET:-25}"
      --max-budget-usd "${TRIAL_BUDGET:-3}" --claude-sandbox "${SANDBOX:-on}"
      --output "$output" "${design[@]}")
if [[ -n "${EFFORT:-}" ]]; then
    args+=(--effort "$EFFORT")
fi
if [[ -n "${MAX_TURNS:-}" ]]; then
    args+=(--max-turns "$MAX_TURNS")
fi
if [[ -n "${SOURCE_CACHE:-}" ]]; then
    args+=(--source-cache "$SOURCE_CACHE")
fi
args+=(--tasks "${task_list[@]}")

# 无模型校验先行:下载并缓存固定提交的 docutils,确认无索引与有索引两种工作区里
# 快照回归通过、验收先失败、参考补丁后全部通过(后出现的 --design 生效)
"$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --design three-arm --verify-tasks > "$output.preflight.json"
selftest_note=""
if [[ "$stage" == "plan" ]] && ! command -v "${PILOT_CLAUDE:-claude}" >/dev/null 2>&1; then
    # 还没装 Claude Code:任务校验与计划照常完成,自检留到安装之后
    selftest_note="skipped: Claude Code not installed (curl -fsSL https://claude.ai/install.sh | bash, then rerun plan)"
    printf 'Self-test %s\n' "$selftest_note" >&2
elif [[ "$stage" == "plan" ]]; then
    # 零成本自检:本地假接口驱动你机器上的 claude,确认沙箱、读取禁区、凭据清除、插件与钩子都生效(执行阶段还会再跑一次)
    if ! "$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --claude-selftest > "$output.selftest.json"; then
        printf 'Claude Code self-test failed; nothing was run. Details and hint: %s.selftest.json\n' "$output" >&2
        exit 1
    fi
fi
"$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" | tee "$output.plan.json"
if [[ "$stage" == "plan" ]]; then
    if [[ -n "$selftest_note" ]]; then
        printf 'No model calls. Verification: %s.preflight.json  Plan: %s.plan.json  Self-test %s\n' \
            "$output" "$output" "$selftest_note"
    else
        printf 'No model calls. Verification: %s.preflight.json  Self-test: %s.selftest.json  Plan: %s.plan.json\n' \
            "$output" "$output" "$output"
    fi
    exit 0
fi

if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i "$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --execute
else
    "$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --execute
fi
"$python" -B "$script_dir/analyze_navigation.py" "$output" --output "$output/navigation.json"
printf 'Private results: %s/report.json and %s/navigation.json\n' "$output" "$output"
