from depgraph import (  # tools/ is on sys.path via tests/conftest.py
    BLOCKED_LABEL,
    TODO_LABEL,
    Issue,
    apply_release,
    detect_cycles,
    parse_dependency_block,
    parse_issue,
    ready_frontier,
    release,
)


def body(depends_on="", serial=None):
    lines = ["Some prose."]
    block = ["```agent-tasks"]
    if depends_on:
        block.append(f"depends-on: {depends_on}")
    if serial is not None:
        block.append(f"serial: {serial}")
    block.append("```")
    return "\n".join(lines + block)


def mk(number, *, state="open", labels=(), body_text=""):
    return parse_issue(number, f"task {number}", state, labels, body_text)


def graph(*issues):
    return {i.number: i for i in issues}


def test_parse_dependency_block_basic():
    meta = parse_dependency_block(body("12, #15", serial="true"))
    assert meta["depends-on"] == "12, #15"
    assert meta["serial"] == "true"


def test_parse_dependency_block_missing():
    assert parse_dependency_block("no block here") == {}
    assert parse_dependency_block("") == {}


def test_parse_issue_extracts_deps_and_ignores_self():
    issue = parse_issue(5, "t", "open", [BLOCKED_LABEL], body("5, 7"))
    assert issue.depends_on == (7,)
    assert issue.serial is False


def test_chain_releases_one_at_a_time():
    # 1 -> 2 -> 3 chained dependencies.
    g = graph(
        mk(1, labels=[TODO_LABEL], body_text=body()),
        mk(2, labels=[BLOCKED_LABEL], body_text=body("1")),
        mk(3, labels=[BLOCKED_LABEL], body_text=body("2")),
    )
    assert [i.number for i in ready_frontier(g)] == []
    assert [i.number for i in ready_frontier(g)] == []

    g[1] = mk(1, state="closed", labels=[TODO_LABEL], body_text=body())
    assert [i.number for i in ready_frontier(g)] == [2]

    g[2] = mk(2, state="closed", labels=[TODO_LABEL], body_text=body("1"))
    assert [i.number for i in ready_frontier(g)] == [3]


def test_diamond_dependency_releases_both_branches():
    # 1 -> {2, 3} -> 4
    g = graph(
        mk(1, state="closed", labels=[TODO_LABEL], body_text=body()),
        mk(2, labels=[BLOCKED_LABEL], body_text=body("1")),
        mk(3, labels=[BLOCKED_LABEL], body_text=body("1")),
        mk(4, labels=[BLOCKED_LABEL], body_text=body("2, 3")),
    )
    assert [i.number for i in ready_frontier(g)] == [2, 3]

    g[2] = mk(2, state="closed", labels=[TODO_LABEL], body_text=body("1"))
    assert [i.number for i in ready_frontier(g)] == [3]


def test_serial_mode_releases_single_issue():
    g = graph(
        mk(1, state="closed", labels=[TODO_LABEL], body_text=body()),
        mk(2, labels=[BLOCKED_LABEL], body_text=body("1")),
        mk(3, labels=[BLOCKED_LABEL], body_text=body("1")),
    )
    to_release, _ = release(g, serial=True)
    assert [i.number for i in to_release] == [2]


def test_unknown_dependency_fails_closed():
    g = graph(mk(2, labels=[BLOCKED_LABEL], body_text=body("99")))
    assert ready_frontier(g) == []


def test_avalanche_self_heal_reblocks_stale_todo():
    # 2 was flipped to todo while its dep 1 was later reopened.
    g = graph(
        mk(1, state="open", labels=[TODO_LABEL], body_text=body()),
        mk(2, state="open", labels=[TODO_LABEL], body_text=body("1")),
    )
    to_release, to_block = release(g)
    assert to_release == []
    assert [i.number for i in to_block] == [2]


def test_apply_release_is_idempotent():
    g = graph(
        mk(1, state="closed", labels=[TODO_LABEL], body_text=body()),
        mk(2, labels=[BLOCKED_LABEL], body_text=body("1")),
        mk(3, labels=[BLOCKED_LABEL], body_text=body("2")),
    )
    g1, released1, _ = apply_release(g)
    assert [i.number for i in released1] == [2]

    g2, released2, blocked2 = apply_release(g1)
    assert released2 == []
    assert blocked2 == []
    assert g2 == g1


def test_unmanaged_issue_is_never_scheduled():
    g = graph(mk(9, labels=["bug"], body_text=body("1")))
    assert ready_frontier(g) == []


def test_closed_issue_is_never_scheduled():
    g = graph(mk(9, state="closed", labels=[BLOCKED_LABEL], body_text=body()))
    assert ready_frontier(g) == []


def test_detect_cycles_finds_loop():
    g = graph(
        mk(1, labels=[BLOCKED_LABEL], body_text=body("2")),
        mk(2, labels=[BLOCKED_LABEL], body_text=body("1")),
    )
    cycles = detect_cycles(g)
    assert cycles, "expected a cycle to be detected"
    assert set(cycles[0][:-1]) == {1, 2}


def test_detect_cycles_clean_graph():
    g = graph(
        mk(1, state="closed", labels=[TODO_LABEL], body_text=body()),
        mk(2, labels=[BLOCKED_LABEL], body_text=body("1")),
    )
    assert detect_cycles(g) == []
