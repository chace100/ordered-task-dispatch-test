#!/usr/bin/env bash
# End-to-end demo against an in-memory GitHub stub.
#
# Runs the real orchestrator/worker/seed over HTTP with --apply, so the whole
# state machine executes, but nothing leaves your machine. This is the same
# path the GitHub Actions workflows take, just pointed at the stub.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8812}"
API="http://127.0.0.1:${PORT}"

cd "$ROOT"

FAKE_SEED=examples/issues.demo.json python3 tests/fake_github.py "$PORT" >/tmp/fake_github.log 2>&1 &
FAKE_PID=$!
trap 'kill "$FAKE_PID" 2>/dev/null || true' EXIT

# Wait for the stub to accept connections.
for _ in $(seq 1 20); do
  if curl -sf "$API/repos/o/r/issues?state=all" >/dev/null 2>&1; then break; fi
  sleep 0.2
done

export GITHUB_API_URL="$API" GITHUB_TOKEN=dummy GITHUB_REPOSITORY=o/r
unset GH_TOKEN || true

show() {
  echo "--- $1 ---"
  curl -s "$API/repos/o/r/issues?state=all" \
    | python3 -c "import sys,json
for i in sorted(json.load(sys.stdin), key=lambda x: x['number']):
    print(f\"  #{i['number']:<2} {i['state']:<6} {[l['name'] for l in i['labels']]}\")"
}

show "initial"
echo "--- orchestrator: release the ready frontier ---"
python3 tools/orchestrator.py --repo o/r --apply | grep -vE '^  (POST|DELETE)'

echo "--- worker: claim the lowest ready task ---"
python3 tools/worker.py --repo o/r --apply --on-claim 'true' | grep -E 'picked|claiming'
show "after claim"

echo "--- close #2 (simulates its PR being merged) ---"
curl -s -X PATCH "$API/repos/o/r/issues/2" -d '{"state":"closed"}' -o /dev/null -w "  HTTP %{http_code}\n"

echo "--- orchestrator: cascade-release the dependent task ---"
python3 tools/orchestrator.py --repo o/r --apply | grep -vE '^  (POST|DELETE)'
show "final"
