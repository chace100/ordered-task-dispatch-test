#!/usr/bin/env python3
"""Hand a claimed task to a coding agent.

This is the seam between "a task was claimed" and "an agent starts working".
It reads the brief the worker exported and dispatches whatever runner you use:

  * OPENHANDS_DISPATCH_CMD  - a shell command template. Placeholders
    {number}, {title} and the TASK_* env vars are available, so the brief is
    piped in on stdin. Example:
      OPENHANDS_DISPATCH_CMD='python -m openhands.cli run'
  * If unset, it prints the brief and exits 0 so the workflow stays green
    while you wire up your runner.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys


def main() -> int:
    number = os.environ.get("TASK_NUMBER", "?")
    title = os.environ.get("TASK_TITLE", "")
    brief = os.environ.get("TASK_BRIEF", "")

    cmd_template = os.environ.get("OPENHANDS_DISPATCH_CMD")
    if not cmd_template:
        print(
            "OPENHANDS_DISPATCH_CMD is not set; nothing dispatched.\n"
            "Set it to your agent runner command to start the work.\n"
            f"--- task brief for #{number}: {title} ---\n{brief}",
            file=sys.stderr,
        )
        return 0

    cmd = cmd_template.format(number=number, title=shlex.quote(title))
    print(f"dispatching task #{number} via: {cmd}")
    subprocess.run(cmd, shell=True, input=brief, text=True, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
