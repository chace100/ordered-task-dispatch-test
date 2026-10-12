#!/usr/bin/env python3
"""Push the working tree to GitHub using the Git Data REST API.

Used when the git transport (github.com:443) is unreachable but api.github.com
works. Creates blobs for the tracked files, assembles a tree, commits it, and
creates/updates the branch ref — the API equivalent of a single `git push`.

    GITHUB_TOKEN=... python tools/push_via_api.py --repo owner/name --branch main
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".github_ignore"}


def api(method: str, path: str, token: str, payload=None):
    url = f"{API}/{path.lstrip('/')}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    files = [line for line in out.splitlines() if line.strip()]
    return [
        f
        for f in files
        if not any(part in SKIP_DIRS for part in Path(f).parts)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", default="Initial commit")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    args = parser.parse_args()
    if not args.token:
        parser.error("token required (GITHUB_TOKEN)")

    repo, token, branch = args.repo, args.token, args.branch
    files = tracked_files()
    print(f"pushing {len(files)} files to {repo}#{branch} via Git Data API")

    # 1) blobs
    entries = []
    for path in files:
        content = Path(path).read_bytes()
        status, blob = api(
            "POST", f"repos/{repo}/git/blobs", token,
            {"content": content.decode("utf-8", "surrogateescape"),
             "encoding": "utf-8"},
        )
        if status not in (200, 201):
            print(f"  ! blob {path}: {status} {blob}")
            return 1
        entries.append({"path": path, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
        print(f"  blob {path}")

    # 2) existing head (if any) for parent + base_tree
    ref_status, ref = api(
        "GET", f"repos/{repo}/git/ref/heads/{branch}", token
    )
    parent_sha = ref["object"]["sha"] if ref_status == 200 else None
    base_tree = None
    if parent_sha:
        _, commit = api("GET", f"repos/{repo}/git/commits/{parent_sha}", token)
        base_tree = commit.get("tree", {}).get("sha")

    # 3) tree
    tree_payload = {"tree": entries}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    status, tree = api("POST", f"repos/{repo}/git/trees", token, tree_payload)
    if status not in (200, 201):
        print(f"  ! tree: {status} {tree}")
        return 1
    print(f"  tree {tree['sha']}")

    # 4) commit
    commit_payload = {"message": args.message, "tree": tree["sha"]}
    if parent_sha:
        commit_payload["parents"] = [parent_sha]
    status, commit = api("POST", f"repos/{repo}/git/commits", token, commit_payload)
    if status not in (200, 201):
        print(f"  ! commit: {status} {commit}")
        return 1
    print(f"  commit {commit['sha']}")

    # 5) ref create/update
    if parent_sha:
        status, res = api(
            "PATCH", f"repos/{repo}/git/refs/heads/{branch}", token,
            {"sha": commit["sha"], "force": False},
        )
    else:
        status, res = api(
            "POST", f"repos/{repo}/git/refs", token,
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    if status not in (200, 201):
        print(f"  ! ref: {status} {res}")
        return 1
    print(f"  ref refs/heads/{branch} -> {commit['sha'][:10]}")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
