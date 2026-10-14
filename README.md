# Ordered agent task dispatch

Run a project's development tasks **in dependency order**, where each task is a
GitHub issue and each issue is picked up by a coding agent — without agents
"randomly" grabbing whatever issue they feel like.

The whole thing is driven by GitHub Actions and a small, dependency-free set of
pure-Python scripts. No server, no database: the dependency graph and the task
state both live in the issues themselves.

## How ordering is enforced

Two mechanisms working together:

1. **Dependencies declared in the issue body**, inside a fenced block:

   ````markdown
   ```agent-tasks
   depends-on: 1, 4
   serial: true
   ```
   ````

2. **A release gate expressed as labels**:

   | label | meaning |
   |---|---|
   | `agent:blocked` | has at least one unfinished dependency |
   | `agent:todo` | all dependencies closed — ready to be claimed |
   | `agent:claimed` | a worker has taken it |
   | `agent:done` | delivered / closed |

   Workers **only ever look at `agent:todo`**. A task whose dependencies are
   still open is simply not in that set, so it cannot be picked. Ordering is a
   property of the graph, not of agent discipline.

`orchestrator.py` computes the topological frontier (all deps closed) and flips
`blocked → todo`; it also flips `todo → blocked` if a dependency was reopened
(self-healing). `worker.py` then claims exactly one ready task and re-checks the
dependencies itself before starting (check-then-act), so a stale label can never
let an agent begin work prematurely.

```
   issue closed ──► orchestrator ──► release (blocked → todo)
                          ▲                     │
                          │                     ▼
                     worker finishes      worker claims (todo → claimed)
                          │                     │ re-verify deps
                          └───────────── opens PR ─┘
```

## Layout

```
tools/
  depgraph.py        pure graph logic: parse deps, compute frontier, detect cycles
  github_client.py   REST client (gh CLI or urllib), --dry-run aware
  orchestrator.py    release / re-block tasks  (the "dealer")
  worker.py          claim one ready task + re-verify deps
  dispatch_agent.py  hand the claimed brief to your coding agent
  seed_issues.py     create a batch of task issues from a plan file
  hello.py           demo greeting helper (not part of the pipeline)
tests/
  test_depgraph.py   13 tests over the ordering logic
  test_hello.py      11 tests over the demo greeting helper
.github/workflows/
  orchestrate.yml    issues closed/reopened/edited + cron → recompute frontier
  worker.yml         agent:todo labeled + cron → claim one task
  ci.yml             tests + demo self-check
examples/
  plan.demo.json         a 5-task dependency plan
  issues.demo.json       a snapshot showing release + self-heal
  issues.after_release.json
```

## Try it locally (no token, no writes)

```bash
# what the orchestrator would release / re-block right now
python tools/orchestrator.py --from-json examples/issues.demo.json --dry-run

# strict ordering: release at most one task
python tools/orchestrator.py --from-json examples/issues.demo.json --serial --dry-run

# a worker claiming the lowest ready task
python tools/worker.py --from-json examples/issues.after_release.json --dry-run \
  --on-claim 'dispatch-agent --task "$TASK_NUMBER"'

# preview creating issues from a plan
python tools/seed_issues.py --plan examples/plan.demo.json --dry-run

# full end-to-end demo against an in-memory GitHub stub (real HTTP, --apply)
bash examples/run_demo.sh

# unit + integration tests
python -m pytest tests/ -q
```

Each test module sets up its own `sys.path` — prepending `tools/` (and
`tests/`, where the stub is imported) — rather than relying on a shared
`conftest.py`, so a single module stays runnable on its own:

```bash
python -m pytest --noconftest tests/test_depgraph.py -q   # 13 passed
python -m pytest --noconftest tests/test_hello.py -q      # 11 passed
python -m pytest --noconftest tests/test_integration.py -q  # 8 passed
```

(Run a module *through pytest* to check this: executing a test file directly —
`python tests/test_hello.py` — defines the tests but never runs them, so its
exit 0 proves nothing.)

### Integration tests

`tests/fake_github.py` is a small in-memory GitHub REST stub. The integration
tests in `tests/test_integration.py` point the client at it via
`GITHUB_API_URL` and run the **real** scripts with `--apply`, so the actual
write paths (label add/remove, comments, assignment, issue creation) and the
state machine are verified over HTTP — nothing is mocked at the client level.
Covered scenarios: release + self-heal, idempotency, claim protocol, stale
dependency re-block, cascade release after a dependency closes, and seeding.

`examples/issues.demo.json` deliberately contains a task (#5) that was released
early while its dependency (#4) is still open, so you can see the self-heal path
(`reblock #5`) and the worker refusing to start on it.

## Wiring it to a real repository

1. Create the workflow labels (`agent:blocked`, `agent:todo`, `agent:claimed`,
   `agent:done`) — e.g. `gh label create agent:todo`.
2. Commit this repo. Both workflows need the default `GITHUB_TOKEN` with
   `issues: write` (already declared in the workflow `permissions`).
3. Seed tasks: `python tools/seed_issues.py --plan plan.json --repo owner/name --apply`
   (or just create issues by hand and paste the `agent-tasks` block into the body).
4. Point `worker.yml`'s `--on-claim` at your real dispatcher. The worker exports
   `TASK_NUMBER`, `TASK_TITLE`, `TASK_BRIEF`, `WORKER_ID`; set
   `OPENHANDS_DISPATCH_CMD` to the command that starts your agent and the brief
   is piped to it on stdin. See `tools/dispatch_agent.py`.

### Strict ordering vs. constrained parallelism

By default the orchestrator releases the **whole ready frontier** (tasks that
can safely run in parallel). For a strict one-at-a-time chain:
- run `orchestrate.yml` with the `serial` input, or pass `--serial`, and/or
- mark the chain with `serial: true` in the `agent-tasks` block, and
- set `concurrency: group: task-worker` (already set) so only one worker claims.

### `agent:done`

A task's dependencies are satisfied when its **issue is closed**. Close the task
(e.g. the PR says `Fixes #12`) and the orchestrator, triggered by the
`issues: closed` event, releases the next one automatically.

### Why the claim job is chained inside `orchestrate.yml`

Events a workflow creates with the default `GITHUB_TOKEN` do **not** trigger
other workflows (GitHub's anti-recursion rule). So a release performed by the
orchestrator's `labeled` write never woke the separate `worker.yml` — that
workflow only ran on its schedule. `orchestrate.yml` therefore runs the claim
in a second job (`needs: frontier`) inside the same run, which is not
suppressed. `worker.yml` remains the scheduled/manual fallback and still fires
on a *human* adding `agent:todo`, because human events are not suppressed.

### Token requirements (real repository)

- **`GITHUB_TOKEN` inside Actions** needs `contents: write` (the worker checks
  out and pushes branches), `issues: write` and `pull-requests: write` — set in
  each workflow's `permissions:`. This is what the workflows declare.
- **A personal token used from outside Actions** (e.g. to run the tools
  locally) needs repository permissions **Contents / Issues / Pull requests:
  Read and write**. Adding a repo to a fine-grained token is not enough on its
  own — the per-permission access must also be set to Read and write.

## Design notes / invariants

- **Fail closed.** An unknown or unparsed dependency number is treated as
  *unsatisfied*, so a typo never leaks a task into the frontier.
- **Idempotent.** Re-running the orchestrator on the resulting graph produces no
  further changes; the cron sweeps are safe to run repeatedly.
- **Cycle-safe.** `detect_cycles` runs first and the orchestrator refuses to
  release anything if the graph has a cycle (exit code 2).
- **Race-safe claims.** Workers set `agent:claimed`, drop `agent:todo`, assign
  the bot and post a timestamped claim comment; the `task-worker` concurrency
  group serialises runs so there is no lost update. Abandoned claims are
  recovered by the periodic worker sweep.
- **Safe by default.** Every entry point simulates unless `--apply` is passed;
  reads still run so the output is real, writes are recorded and skipped.
- **Never auto-merge.** Workers open PRs that link the issue; a human merges.
