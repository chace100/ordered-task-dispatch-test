#!/usr/bin/env python3
"""Orchestrator: recompute the dependency frontier and (un)block tasks.

Runs as a pure label transition over the dependency graph:

    blocked --(all deps closed)--> todo        (release)
    todo    --(a dep reopened)----> blocked     (self-heal)

Only the *label* is moved; claiming (todo -> claimed) is the worker's job.
Safe by default: it performs no writes unless ``--apply`` is passed. With
``--from-json`` it reads a local issue snapshot instead of the GitHub API,
which is how the demo and CI self-check run.

Examples:
    # show what would change, reading a local snapshot (no network, no writes)
    python tools/orchestrator.py --from-json examples/issues.demo.json --dry-run

    # real run against the repository of the current Actions workflow
    python tools/orchestrator.py --repo "$GITHUB_REPOSITORY" --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from depgraph import (  # noqa: E402
    BLOCKED_LABEL,
    TODO_LABEL,
    Issue,
    detect_cycles,
    parse_issue,
    release,
)
from github_client import GitHubClient, GitHubError  # noqa: E402


def load_snapshot(path: str) -> dict[int, Issue]:
    """Load a local issue snapshot: a JSON list of issue objects."""
    raw = json.loads(Path(path).read_text())
    issues = {}
    for item in raw:
        issue = parse_issue(
            int(item["number"]),
            item.get("title", ""),
            item.get("state", "open"),
            item.get("labels", []),
            item.get("body", ""),
        )
        issues[issue.number] = issue
    return issues


def collect_from_github(client: GitHubClient) -> dict[int, Issue]:
    return {i.number: i for i in client.list_issues(state="all")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument(
        "--from-json", help="read issues from a local snapshot instead of the API"
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="release at most one task per run (strict ordering)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write labels/comments (default is dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="force simulation (the default); overrides --apply if both are given",
    )
    parser.add_argument(
        "--no-comment", action="store_true", help="do not post explanatory comments"
    )
    args = parser.parse_args(argv)
    apply_writes = args.apply and not args.dry_run

    # -- gather graph ------------------------------------------------------
    if args.from_json:
        issues = load_snapshot(args.from_json)
        client = None
    else:
        if not args.repo:
            parser.error("--repo or GITHUB_REPOSITORY is required without --from-json")
        client = GitHubClient(args.repo, dry_run=not apply_writes)
        issues = collect_from_github(client)

    if not issues:
        print("No issues found.")
        return 0

    # -- self-checks -------------------------------------------------------
    cycles = detect_cycles(issues)
    if cycles:
        print("!! Dependency cycles detected, refusing to release anything:")
        for cycle in cycles:
            print("   " + " -> ".join(f"#{n}" for n in cycle))
        return 2

    # -- decide + apply ----------------------------------------------------
    to_release, to_block = release(issues, serial=args.serial)
    mode = "APPLY" if apply_writes else "DRY-RUN"
    print(f"[{mode}] {len(issues)} issues, release={len(to_release)} reblock={len(to_block)}")

    for issue in to_release:
        print(f"  release  #{issue.number}  {issue.title}")
        if client:
            client.set_workflow_label(issue, add=TODO_LABEL, remove=[BLOCKED_LABEL])
            if not args.no_comment:
                client.comment(
                    issue.number,
                    "🤖 Orchestrator: all dependencies are closed — "
                    "moving to `agent:todo`, ready to be claimed.",
                )
    for issue in to_block:
        deps = ", ".join(f"#{d}" for d in issue.depends_on)
        print(f"  reblock  #{issue.number}  (waiting on {deps})")
        if client:
            client.set_workflow_label(issue, add=BLOCKED_LABEL, remove=[TODO_LABEL])
            if not args.no_comment:
                client.comment(
                    issue.number,
                    f"🤖 Orchestrator: dependency reopened ({deps}); "
                    "moving back to `agent:blocked`.",
                )

    if client and client.dry_run and client.pending_writes:
        print(f"\n{len(client.pending_writes)} write(s) suppressed by dry-run:")
        for call in client.pending_writes:
            print(f"  {call}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubError as exc:
        print(f"GitHub error: {exc}", file=sys.stderr)
        raise SystemExit(1)
