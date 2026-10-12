# AGENTS.md

## What this repo is
Ordered agent task dispatch: GitHub issues form a dependency graph, and a
GitHub Actions orchestrator gates each task's `agent:todo` label so coding
agents only pick up work whose dependencies are closed.

## Commands
- Tests: `python -m pytest tests/ -q` (unit + integration; no network needed)
- End-to-end demo: `bash examples/run_demo.sh` (real HTTP against the stub)
- Dry-run (safe, no writes): pass `--dry-run` or omit `--apply` on any tool.

## Conventions
- All entry points are safe by default: writes require `--apply`.
- The scheduling core (`tools/depgraph.py`) is pure and network-free; keep it
  that way so the ordering logic stays unit-testable.
- Tests exercise real code paths. `tests/fake_github.py` is an in-memory GitHub
  REST stub; point the client at it with `GITHUB_API_URL`.
- Local git identity: openhands <openhands@all-hands.dev>.

## Environment notes
- `GITHUB_PERSONAL_ACCESS_TOKEN` is a **read-only** fine-grained PAT in this
  sandbox. All writes return HTTP 403:
    * create repo            -> needs Administration: write (403)
    * push / create file     -> needs `contents=write, workflows=write` (403)
    * create issue / label   -> needs `issues=write` (403)
  Reads succeed (200) because the target repos are public.
- Adding the repo to a fine-grained token is not enough on its own: its
  **Repository permissions** for Contents / Issues / Pull requests / Workflows
  must each be set to **Read and write**, then saved.
- `GITHUB_TOKEN`/`GH_TOKEN` are not set here; the client falls back to
  `GITHUB_PERSONAL_ACCESS_TOKEN` only if exported as `GITHUB_TOKEN`.
- Re-verify write capability with:
  `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $GITHUB_PERSONAL_ACCESS_TOKEN" https://api.github.com/repos/OWNER/REPO/labels -d '{"name":"probe","color":"ffffff"}'`
  (200/201 = write works, 403 = still read-only).
