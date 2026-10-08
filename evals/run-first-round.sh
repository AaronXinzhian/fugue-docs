#!/usr/bin/env bash
# [INPUT]: 依赖 (未检出外部依赖)
# [OUTPUT]: no-model task verification and plan; optional private report.json/navigation.json
# [POS]: fugue-docs evaluation entry point for a bounded first paired block
# [PROTOCOL]: Keep token-pilot.md, FOLDER_INDEX.md and test_first_round.py in sync

set -euo pipefail
umask 077

mode="${1:---plan}"
if [[ "$#" -gt 1 || ( "$mode" != "--plan" && "$mode" != "--execute" && "$mode" != "--help" ) ]]; then
    printf 'Usage: bash evals/run-first-round.sh [--plan|--execute]\n' >&2
    exit 2
fi
if [[ "$mode" == "--help" ]]; then
    printf '%s\n' 'Default: no model calls; verify tasks and save the plan.' \
        'Settings: MODEL EFFORT BUDGET TIMEOUT REPEATS TASKS DESIGN PILOT_CODEX PILOT_OUTPUT PILOT_PYTHON' \
        'Defaults: gpt-6.1-sol xhigh 500000 600 1 session-fallback three-arm'
    exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${PILOT_PYTHON:-python3}"
output="${PILOT_OUTPUT:-$HOME/fugue-pilot/first-round-$(date +%Y%m%d-%H%M%S)-$$}"
task_spec="${TASKS:-session-fallback}"
if [[ -z "${task_spec//[[:space:]]/}" ]]; then
    printf 'TASKS must contain at least one task name.\n' >&2
    exit 2
fi
read -r -a task_list <<< "$task_spec"
for target in "$output" "$output.plan.json" "$output.preflight.json"; do
    if [[ -e "$target" || -L "$target" ]]; then
        printf 'Refusing to overwrite: %s\n' "$target" >&2
        exit 2
    fi
done
mkdir -p "$(dirname "$output")"
args=(--model "${MODEL:-gpt-6.1-sol}" --effort "${EFFORT:-xhigh}"
      --codex "${PILOT_CODEX:-codex}"
      --max-total-tokens "${BUDGET:-500000}" --timeout "${TIMEOUT:-600}"
      --repeats "${REPEATS:-1}" --design "${DESIGN:-three-arm}"
      --output "$output" --tasks "${task_list[@]}")

"$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --verify-tasks > "$output.preflight.json"
"$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" | tee "$output.plan.json"
if [[ "$mode" == "--plan" ]]; then
    printf 'No model calls. Plan: %s.plan.json\n' "$output"
    exit 0
fi

if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i "$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --execute
else
    "$python" -B "$script_dir/run_token_pilot.py" "${args[@]}" --execute
fi
"$python" -B "$script_dir/analyze_navigation.py" "$output" --output "$output/navigation.json"
printf 'Private results: %s/report.json and %s/navigation.json\n' "$output" "$output"
