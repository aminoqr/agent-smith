#!/usr/bin/env bash
# Run N MBPP tasks through the full grader pipeline and report a pass rate.
#
#   scripts/run_mbpp_batch.sh 10 --model-name openai/gpt-oss-120b \
#       --provider-url https://api.groq.com/openai/v1
#
# For each task: moulinette dump -> run-agent (120 s hard kill) -> validate.
# Everything lands in the layout the subject asks for in VI.5:
#   evaluations/mbpp/<timestamp>/<task_id>/{task.json,solution.json,stdout.log,stderr.log}
# `evaluations/` is gitignored: these are runs, not source.
#
# No `set -e`: one failing task must not stop the batch. That is the point.
set -uo pipefail

COUNT="${1:-10}"
shift || true

MOULINETTE="../moulinette"
MODEL_ARGS=()
SEED_START=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model-name|--provider-url) MODEL_ARGS+=("$1" "$2"); shift 2 ;;
        --moulinette) MOULINETTE="$2"; shift 2 ;;
        --seed-start) SEED_START="$2"; shift 2 ;;
        --task-ids) IFS=',' read -r -a TASK_IDS <<< "$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [[ ! -d "$MOULINETTE" ]]; then
    echo "no moulinette at $MOULINETTE (use --moulinette PATH)" >&2
    exit 2
fi

STUDENT="$(pwd)"
MOULINETTE="$(cd "$MOULINETTE" && pwd)"
RUN_DIR="$STUDENT/evaluations/mbpp/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
SUMMARY="$RUN_DIR/summary.tsv"
printf 'task_id\tverdict\titerations\tinput_tokens\toutput_tokens\tseconds\n' > "$SUMMARY"

passed=0
submitted=0
attempted=0

for ((index = 0; index < COUNT; index++)); do
    seed=$((SEED_START + index))
    work="$RUN_DIR/pending-$seed"
    mkdir -p "$work"

    # 1/3 dump. A seed keeps the batch reproducible; --task-ids overrides it.
    if [[ -n "${TASK_IDS+x}" ]]; then
        [[ $index -ge ${#TASK_IDS[@]} ]] && break
        dump_args=(--task-id "${TASK_IDS[$index]}")
    else
        dump_args=(--seed "$seed")
    fi
    if ! (cd "$MOULINETTE" && uv run moulinette_eval dump mbpp "${dump_args[@]}" \
            --output "$work/task.json" >"$work/dump.log" 2>&1); then
        echo "dump failed for seed $seed, see $work/dump.log" >&2
        continue
    fi

    task_id="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['task_id'])" "$work/task.json")"
    dir="$RUN_DIR/$task_id"
    mkdir -p "$dir"
    mv "$work/task.json" "$dir/task.json"
    rm -rf "$work"
    attempted=$((attempted + 1))

    # 2/3 run the agent exactly the way the grader does: 120 s, process group.
    agent_cmd="uv run python -m agent_mbpp --task-file $dir/task.json --output $dir/solution.json ${MODEL_ARGS[*]-}"
    (cd "$STUDENT" && uv run --project "$MOULINETTE" moulinette_eval run-agent 120 "$agent_cmd") \
        >"$dir/stdout.log" 2>"$dir/stderr.log"

    # 3/3 validate (needs Docker: correctness runs the hidden tests in a container).
    # Without Docker we still score the agent's own claim: success=true means
    # final_answer() was called after the visible tests passed in the sandbox.
    # That is not a grader PASS (the first MBPP test is hidden), but it is the
    # honest number we can report until Docker is available.
    verdict="NO_SOLUTION"
    if [[ -f "$dir/solution.json" ]]; then
        (cd "$MOULINETTE" && uv run moulinette_eval validate mbpp "$dir/task.json" "$dir/solution.json") \
            >"$dir/validate.log" 2>&1 || true
        if grep -qE "Overall:? *(PASS|PASSED)" "$dir/validate.log"; then
            verdict="PASS"
            passed=$((passed + 1))
        elif grep -qiE "DockerException|Cannot connect to the Docker|docker\.sock|Error while fetching server API version" "$dir/validate.log"; then
            if python3 -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get('success') else 1)" \
                    "$dir/solution.json"; then
                verdict="SUBMITTED"
                submitted=$((submitted + 1))
            else
                verdict="FAIL"
            fi
        else
            verdict="FAIL"
        fi
    fi

    read -r iterations input_tokens output_tokens seconds <<<"$(
        python3 - "$dir/solution.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print(data["iterations"], data["total_input_tokens"],
          data["total_output_tokens"], round(data["total_time_seconds"], 1))
except Exception:
    print("- - - -")
PY
    )"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$task_id" "$verdict" "$iterations" "$input_tokens" "$output_tokens" "$seconds" >> "$SUMMARY"
    echo "task $task_id: $verdict (${iterations} iterations, ${input_tokens} in / ${output_tokens} out, ${seconds}s)"
done

echo
column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
echo
echo "grader PASS $passed · agent SUBMITTED $submitted · attempted $attempted -> $RUN_DIR"
echo "(SUBMITTED = final_answer after visible tests; needs Docker to become a real PASS)"
[[ $attempted -gt 0 && $((passed + submitted)) -eq $attempted ]] && exit 0
exit 1
