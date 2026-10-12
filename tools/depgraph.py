"""Dependency-graph logic for label-driven, ordered agent task dispatch.

This module is intentionally free of any GitHub/network dependency so the
scheduling correctness (release gating, cycle detection, self-heal) can be
reasoned about and unit-tested as pure functions.

Task state is expressed with labels:

    agent:blocked   -> has at least one unfinished dependency
    agent:todo      -> every dependency is closed, ready for a worker to claim
    agent:claimed   -> a worker has taken it (set by worker.py, not here)
    agent:done      -> delivered / closed

Dependencies are declared in the issue body inside a fenced code block:

    ```agent-tasks
    depends-on: 12, 15
    state: todo
    serial: true
    ```
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

TODO_LABEL = "agent:todo"
BLOCKED_LABEL = "agent:blocked"
CLAIMED_LABEL = "agent:claimed"
DONE_LABEL = "agent:done"
AGENT_LABELS = (TODO_LABEL, BLOCKED_LABEL, CLAIMED_LABEL, DONE_LABEL)

_BLOCK_RE = re.compile(
    r"```agent-tasks[ \t]*\n(?P<body>.*?)```", re.DOTALL | re.IGNORECASE
)
_DEP_RE = re.compile(r"#?(\d+)")


@dataclass(frozen=True)
class Issue:
    number: int
    title: str = ""
    state: str = "open"  # "open" | "closed"
    labels: tuple[str, ...] = ()
    depends_on: tuple[int, ...] = ()
    serial: bool = False
    # Extra machine-readable metadata parsed from the block (for forward-compat).
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def is_closed(self) -> bool:
        return self.state.lower() == "closed"

    @property
    def is_managed(self) -> bool:
        """True when the issue carries one of the agent:* workflow labels."""
        return any(lbl in self.labels for lbl in AGENT_LABELS)

    def with_label(self, label: str) -> "Issue":
        """Return a copy whose labels are exactly this workflow label + extras."""
        extras = tuple(lbl for lbl in self.labels if lbl not in AGENT_LABELS)
        return replace(self, labels=extras + (label,))


def parse_dependency_block(body: str) -> dict[str, str]:
    """Parse the first ```agent-tasks fenced block into a key/value mapping.

    Keys are lowercased, values are stripped. Later duplicate keys win.
    Missing/empty body yields an empty mapping.
    """
    match = _BLOCK_RE.search(body or "")
    if not match:
        return {}
    meta: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()
    return meta


def parse_issue(number: int, title: str, state: str, labels, body: str) -> Issue:
    """Build an Issue from raw GitHub field values, extracting the dep block."""
    meta = parse_dependency_block(body)
    deps = tuple(
        int(n) for n in _DEP_RE.findall(meta.get("depends-on", "")) if int(n) != number
    )
    serial = meta.get("serial", "").lower() in {"1", "true", "yes", "on"}
    return Issue(
        number=number,
        title=title,
        state=state,
        labels=tuple(labels),
        depends_on=deps,
        serial=serial,
        meta=meta,
    )


def deps_satisfied(issue: Issue, issues: dict[int, Issue]) -> bool:
    for dep in issue.depends_on:
        upstream = issues.get(dep)
        # Unknown dep numbers are treated as unsatisfied: fail closed, never
        # leak a task into the frontier on a typo or an unparsed reference.
        if upstream is None or not upstream.is_closed:
            return False
    return True


def detect_cycles(issues: dict[int, Issue]) -> list[list[int]]:
    """Return dependency cycles among managed issues (a graph self-check)."""
    managed = {n: i for n, i in issues.items() if i.is_managed}
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[int, int] = {n: WHITE for n in managed}
    cycles: list[list[int]] = []
    stack: list[int] = []

    def visit(node: int) -> None:
        color[node] = GREY
        stack.append(node)
        for dep in managed[node].depends_on:
            if dep not in managed:
                continue
            if color[dep] == GREY:
                cycles.append(stack[stack.index(dep) :] + [dep])
            elif color[dep] == WHITE:
                visit(dep)
        stack.pop()
        color[node] = BLACK

    for n in list(managed):
        if color[n] == WHITE:
            visit(n)
    return cycles


def ready_frontier(issues: dict[int, Issue]) -> list[Issue]:
    """Open, managed, still-blocked issues whose deps are all satisfied."""
    result = [
        i
        for i in issues.values()
        if not i.is_closed
        and i.is_managed
        and BLOCKED_LABEL in i.labels
        and TODO_LABEL not in i.labels
        and deps_satisfied(i, issues)
    ]
    return sorted(result, key=lambda i: i.number)


def release(
    issues: dict[int, Issue], serial: bool = False
) -> tuple[list[Issue], list[Issue]]:
    """Compute the release decision.

    Returns (to_release, to_block), the issues to flip blocked->todo and
    todo->blocked respectively. Idempotent by construction: recomputing on the
    resulting graph yields no further changes.
    """
    to_release = ready_frontier(issues)
    if serial and len(to_release) > 1:
        to_release = to_release[:1]

    to_block = [
        i
        for i in issues.values()
        if not i.is_closed
        and i.is_managed
        and TODO_LABEL in i.labels
        and not deps_satisfied(i, issues)
    ]
    return to_release, sorted(to_block, key=lambda i: i.number)


def apply_release(
    issues: dict[int, Issue], serial: bool = False
) -> tuple[dict[int, Issue], list[Issue], list[Issue]]:
    """Pure state transition: return (new_graph, released, blocked)."""
    to_release, to_block = release(issues, serial=serial)
    new_graph = dict(issues)
    for issue in to_release:
        new_graph[issue.number] = issue.with_label(TODO_LABEL)
    for issue in to_block:
        new_graph[issue.number] = issue.with_label(BLOCKED_LABEL)
    return new_graph, to_release, to_block
