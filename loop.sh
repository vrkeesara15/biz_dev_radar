#!/usr/bin/env bash
# loop.sh — run Claude Code headless until all tasks are done or the budget is spent (SPEC §13.5).
# Run inside a container with no production credentials. Review PROGRESS.md twice a day.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
MODEL="${CLAUDE_MODEL:-}"           # e.g. CLAUDE_MODEL=opus to fall back when the default model is out of credit
MODEL_FLAG=()
[ -n "$MODEL" ] && MODEL_FLAG=(--model "$MODEL")
for i in $(seq 1 200); do
  remaining=$(jq '[.[] | select(.status=="todo")] | length' tasks.json)
  [ "$remaining" -eq 0 ] && echo "All tasks done" && break
  echo "iteration $i — $remaining tasks remaining"
  claude -p "Follow CLAUDE.md. Complete exactly one task from tasks.json, verify, commit, then stop." \
    --permission-mode acceptEdits --max-turns 60 "${MODEL_FLAG[@]}" \
    --verbose --output-format stream-json >> "logs/iter-$i.jsonl" || echo "iteration $i exited non-zero" >> PROGRESS.md
  make test || echo "iteration $i left tests red" >> PROGRESS.md
done
