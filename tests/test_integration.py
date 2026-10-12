"""End-to-end tests: run the real scripts against an in-memory GitHub stub.

These exercise the actual HTTP write paths (label add/remove, comments,
assignment, issue creation) rather than mocking the client, so payload shapes
and the state machine are verified the way they run in CI.
"""

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import fake_github  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def fake_github_server():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), fake_github.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def reset_state():
    fake_github.STATE.clear()
    fake_github.NEXT_NUMBER = 1
    yield
    fake_github.STATE.clear()


def run(script: str, *args, api_url: str):
    env = {
        **os.environ,
        "GITHUB_API_URL": api_url,
        "GITHUB_TOKEN": "dummy",
        "GITHUB_REPOSITORY": "testowner/testrepo",
    }
    env.pop("GH_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    return proc


def labels(number: int) -> list[str]:
    return list(fake_github.STATE[number]["labels"])


def test_orchestrator_apply_releases_and_reblocks(fake_github_server):
    fake_github.load_seed(str(ROOT / "examples" / "issues.demo.json"))

    proc = run(
        "orchestrator.py", "--repo", "testowner/testrepo", "--apply",
        api_url=fake_github_server,
    )
    assert proc.returncode == 0, proc.stderr
    assert "release=2" in proc.stdout and "reblock=1" in proc.stdout

    # #1 was closed -> #2 and #4 released; #5 was stale -> reblocked.
    assert "agent:todo" in labels(2)
    assert "agent:blocked" not in labels(2)
    assert "agent:todo" in labels(4)
    assert "agent:blocked" not in labels(4)
    assert "agent:blocked" in labels(5)
    assert "agent:todo" not in labels(5)
    # Explanatory comments were posted.
    assert fake_github.STATE[2]["comments"]
    assert any("agent:blocked" in c["body"] for c in fake_github.STATE[5]["comments"])


def test_orchestrator_is_idempotent_over_http(fake_github_server):
    fake_github.load_seed(str(ROOT / "examples" / "issues.demo.json"))
    run("orchestrator.py", "--repo", "testowner/testrepo", "--apply",
        api_url=fake_github_server)
    first = {n: labels(n) for n in sorted(fake_github.STATE)}

    proc = run("orchestrator.py", "--repo", "testowner/testrepo", "--apply",
               api_url=fake_github_server)
    assert proc.returncode == 0, proc.stderr
    assert "release=0" in proc.stdout and "reblock=0" in proc.stdout
    second = {n: labels(n) for n in sorted(fake_github.STATE)}
    assert first == second


def test_worker_claims_ready_task_over_http(fake_github_server):
    fake_github.load_seed(str(ROOT / "examples" / "issues.after_release.json"))

    proc = run("worker.py", "--repo", "testowner/testrepo", "--apply",
               "--on-claim", "true", api_url=fake_github_server)
    assert proc.returncode == 0, proc.stderr
    assert "picked #2" in proc.stdout

    assert "agent:claimed" in labels(2)
    assert "agent:todo" not in labels(2)
    assert fake_github.STATE[2]["comments"]
    assert "claimed" in fake_github.STATE[2]["comments"][0]["body"]
    assert fake_github.STATE[2]["assignees"]

    # #4 is also on the ready frontier, so a second worker claims it too.
    proc2 = run("worker.py", "--repo", "testowner/testrepo", "--apply",
                "--on-claim", "true", api_url=fake_github_server)
    assert "picked #4" in proc2.stdout
    assert "agent:claimed" in labels(4)

    # Now nothing claimable remains.
    proc3 = run("worker.py", "--repo", "testowner/testrepo", "--apply",
                api_url=fake_github_server)
    assert "No claimable task" in proc3.stdout


def test_worker_reverifies_and_reblocks_stale_dependency(fake_github_server):
    fake_github.load_seed(str(ROOT / "examples" / "issues.after_release.json"))
    # Make #2's dependency (#1) open again, simulating a stale release.
    fake_github.STATE[1]["state"] = "open"

    proc = run("worker.py", "--repo", "testowner/testrepo", "--issue", "2",
               "--apply", api_url=fake_github_server)
    assert proc.returncode == 0, proc.stderr
    assert "dependency not satisfied" in proc.stdout
    assert "agent:blocked" in labels(2)
    assert "agent:todo" not in labels(2)


def test_cascade_release_after_dependency_closes(fake_github_server):
    fake_github.load_seed(str(ROOT / "examples" / "issues.demo.json"))

    # 1) release the ready frontier, 2) a worker claims #2.
    run("orchestrator.py", "--repo", "testowner/testrepo", "--apply",
        api_url=fake_github_server)
    run("worker.py", "--repo", "testowner/testrepo", "--apply", "--on-claim", "true",
        api_url=fake_github_server)
    assert "agent:claimed" in labels(2)
    assert "agent:blocked" in labels(3)

    # 3) the dependency (#2) gets closed (e.g. its PR was merged).
    fake_github.STATE[2]["state"] = "closed"

    # 4) the next orchestrator run releases the dependent task.
    proc = run("orchestrator.py", "--repo", "testowner/testrepo", "--apply",
               api_url=fake_github_server)
    assert "release=1" in proc.stdout
    assert "agent:todo" in labels(3)


def test_seed_creates_issues_with_correct_labels(fake_github_server):
    proc = run("seed_issues.py", "--plan", str(ROOT / "examples" / "plan.demo.json"),
               "--repo", "testowner/testrepo", "--apply", api_url=fake_github_server)
    assert proc.returncode == 0, proc.stderr
    assert "seeding 5 task(s)" in proc.stdout

    created = list(fake_github.STATE.values())
    assert len(created) == 5
    by_title = {i["title"]: i for i in created}

    assert by_title["Set up database schema"]["labels"] == ["agent:todo"]
    # Everything with a dependency starts blocked.
    assert by_title["Add request logging"]["labels"] == ["agent:blocked"]
    assert by_title["Implement auth service"]["labels"] == ["agent:blocked"]

    # The dependency block resolves key -> real issue number.
    body = by_title["Wire login endpoint"]["body"]
    assert "agent-tasks" in body and "depends-on:" in body
