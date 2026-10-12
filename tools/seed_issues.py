#!/usr/bin/env python3
"""Seed a batch of task issues from a plan file.

Takes a JSON list of tasks (number is assigned by GitHub, so tasks reference
each other by a local ``key``) and creates issues with the right workflow
label: a task with no unsatisfied dependencies starts as ``agent:todo``,
everything else as ``agent:blocked``.

Plan format:
    [
      {"key": "schema", "title": "Set up schema"},
      {"key": "auth", "title": "Auth service", "depends-on": ["schema"]},
      {"key": "login", "title": "Login endpoint", "depends-on": ["auth"]}
    ]

Dry-run by default: prints the issues and resolved dependencies without
touching GitHub.

    python tools/seed_issues.py --plan examples/plan.demo.json --dry-run
    python tools/seed_issues.py --plan plan.json --repo "$GITHUB_REPOSITORY" --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from depgraph import BLOCKED_LABEL, TODO_LABEL  # noqa: E402
from github_client import GitHubClient, GitHubError  # noqa: E402

BODY_TEMPLATE = """{description}

```agent-tasks
depends-on: {deps}
state: todo
```
"""


def render_body(description: str, dep_numbers: list[int]) -> str:
    return BODY_TEMPLATE.format(
        description=description or "",
        deps=", ".join(str(n) for n in dep_numbers),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    apply_writes = args.apply and not args.dry_run

    plan = json.loads(Path(args.plan).read_text())

    if args.apply and not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required with --apply")
    client = (
        GitHubClient(args.repo, dry_run=not apply_writes)
        if args.repo
        else None
    )

    key_to_number: dict[str, int] = {}
    mode = "APPLY" if apply_writes else "DRY-RUN"
    print(f"[{mode}] seeding {len(plan)} task(s)")

    for index, task in enumerate(plan, start=1):
        key = task["key"]
        dep_keys = task.get("depends-on", [])
        dep_numbers = [key_to_number[k] for k in dep_keys if k in key_to_number]
        # Unknown dep keys mean the plan lists a task out of order; keep it
        # blocked and warn rather than creating a dangling reference.
        missing = [k for k in dep_keys if k not in key_to_number]
        if missing:
            print(f"  ! {key}: depends on unknown/forward keys {missing}")
        label = TODO_LABEL if not dep_keys else BLOCKED_LABEL
        body = render_body(task.get("description", ""), dep_numbers)
        title = task["title"]

        if client:
            number = client.create_issue(title, body, [label]) or index
        else:
            number = index

        key_to_number[key] = number
        print(
            f"  #{number}  [{label}]  {title}"
            + (f"  (depends-on {dep_numbers})" if dep_numbers else "")
        )

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
