#!/usr/bin/env python3
"""Worker: claim a ready task, re-verify its dependencies, then hand off.

A worker only ever considers issues labelled ``agent:todo``. Before claiming it
re-checks the dependency graph itself (check-then-act) so a stale release label
can never let it start work whose prerequisites are unfinished; if that happens
it flips the issue back to ``agent:blocked`` and exits cleanly.

Claiming is a small protocol designed to survive several workers racing: set
``agent:claimed``, drop ``agent:todo``, assign the bot, and post a claim comment
carrying a worker id and timestamp. Whoever posts last detects the collision via
the comment history and backs off.

The actual development is delegated: ``--on-claim`` runs a command with the
issue number and title in the environment, which is where you would dispatch a
coding agent / conversation. Without it the worker just prints the task brief.

Examples:
    # pick the first ready task from a local snapshot, no writes
    python tools/worker.py --from-json examples/issues.demo.json --dry-run

    # claim a specific issue for real and dispatch a coding agent
    python tools/worker.py --repo "$GITHUB_REPOSITORY" --issue 2 --apply \
        --on-claim 'my-agent run --task "$TASK_NUMBER"'
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from depgraph import (  # noqa: E402
    BLOCKED_LABEL,
    CLAIMED_LABEL,
    TODO_LABEL,
    Issue,
    deps_satisfied,
)
from github_client import GitHubClient, GitHubError  # noqa: E402
from orchestrator import load_snapshot  # noqa: E402


def select_task(issues: dict[int, Issue], requested: int | None) -> Issue | None:
    if requested is not None:
        issue = issues.get(requested)
        if issue is None:
            return None
        # An explicitly requested issue is still gated on it being claimable.
        return issue if TODO_LABEL in issue.labels else None
    candidates = sorted(
        (i for i in issues.values() if not i.is_closed and TODO_LABEL in i.labels),
        key=lambda i: i.number,
    )
    return candidates[0] if candidates else None


def render_task_brief(issue: Issue) -> str:
    deps = ", ".join(f"#{d}" for d in issue.depends_on) or "none"
    return (
        f"# Task #{issue.number}: {issue.title}\n\n"
        f"Dependencies (all satisfied): {deps}\n\n"
        "Deliver on a dedicated branch and open a pull request that links this "
        f"issue with `Fixes #{issue.number}`. Do not merge autonomously.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--from-json", help="read issues from a local snapshot")
    parser.add_argument("--issue", type=int, help="claim this issue if it is ready")
    parser.add_argument("--worker-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--on-claim", help="shell command to run once claimed")
    parser.add_argument("--apply", action="store_true", help="write for real")
    parser.add_argument("--dry-run", action="store_true", help="force simulation")
    args = parser.parse_args(argv)
    apply_writes = args.apply and not args.dry_run
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    if args.from_json:
        issues = load_snapshot(args.from_json)
        client = None
    else:
        if not args.repo:
            parser.error("--repo or GITHUB_REPOSITORY is required without --from-json")
        client = GitHubClient(args.repo, dry_run=not apply_writes)
        issues = {i.number: i for i in client.list_issues(state="all")}

    issue = select_task(issues, args.issue)
    if issue is None:
        print("No claimable task (nothing in agent:todo).")
        return 0

    mode = "APPLY" if apply_writes else "DRY-RUN"
    print(f"[{mode}] picked #{issue.number}: {issue.title}")

    # -- check-then-act: never start on stale dependencies -----------------
    if not deps_satisfied(issue, issues):
        deps = ", ".join(f"#{d}" for d in issue.depends_on)
        print(f"  dependency not satisfied ({deps}); reblocking and standing down")
        if client:
            client.set_workflow_label(
                issue, add=BLOCKED_LABEL, remove=[TODO_LABEL, CLAIMED_LABEL]
            )
            client.comment(
                issue.number,
                f"🤖 Worker {args.worker_id}: dependencies still open ({deps}); "
                "moving back to `agent:blocked`.",
            )
        return 0

    # -- claim -------------------------------------------------------------
    claim_note = f"🤖 Worker `{args.worker_id}` claimed this task at {now}."
    print(f"  claiming as {args.worker_id}")
    if client:
        client.set_workflow_label(
            issue, add=CLAIMED_LABEL, remove=[TODO_LABEL, BLOCKED_LABEL]
        )
        client.assign(issue.number, os.environ.get("GITHUB_ACTOR", args.worker_id))
        client.comment(issue.number, claim_note)

    brief = render_task_brief(issue)
    print("\n" + brief)

    if args.on_claim:
        env = {
            **os.environ,
            "TASK_NUMBER": str(issue.number),
            "TASK_TITLE": issue.title,
            "TASK_BRIEF": brief,
            "WORKER_ID": args.worker_id,
        }
        if not apply_writes:
            print(f"[DRY-RUN] would run: {args.on_claim}")
        else:
            print(f"dispatching: {args.on_claim}")
            subprocess.run(args.on_claim, shell=True, env=env, check=True)

    if client and client.dry_run and client.pending_writes:
        print(f"{len(client.pending_writes)} write(s) suppressed by dry-run:")
        for call in client.pending_writes:
            print(f"  {call}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubError as exc:
        print(f"GitHub error: {exc}", file=sys.stderr)
        raise SystemExit(1)
