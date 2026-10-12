"""Thin GitHub client used by the orchestrator and worker.

Talks to the REST API through the ``gh`` CLI when available (no extra
dependency, inherits the Actions token automatically) and falls back to plain
``urllib`` with a token from the environment. The client also supports a
``--dry-run`` mode where reads still hit the API but writes are recorded and
skipped, so the whole flow can be exercised safely.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from depgraph import Issue, parse_issue

API_ROOT = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repo: str, token: str | None = None, dry_run: bool = False):
        if "/" not in repo:
            raise ValueError(f"repo must be 'owner/name', got {repo!r}")
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get(
            "GH_TOKEN"
        )
        self.dry_run = dry_run
        # GITHUB_API_URL is the standard Actions variable and also lets tests
        # point at a local stub server.
        self.api_root = os.environ.get("GITHUB_API_URL", API_ROOT).rstrip("/")
        self._gh = shutil.which("gh")
        self._pending: list[str] = []

    # -- transport ---------------------------------------------------------

    def _api(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        tolerate: tuple[int, ...] = (),
    ):
        # gh CLI only knows how to talk to api.github.com, so a custom API root
        # (tests, GitHub Enterprise) must go through urllib.
        if self._gh and self.token and self.api_root == API_ROOT:
            return self._api_gh(method, path, payload, tolerate)
        return self._api_urllib(method, path, payload, tolerate)

    def _api_gh(
        self, method: str, path: str, payload: dict | None, tolerate: tuple[int, ...] = ()
    ):
        cmd = ["gh", "api", "-X", method, path.lstrip("/")]
        if payload is not None:
            cmd += ["--input", "-"]
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            env={**os.environ, "GH_TOKEN": self.token or ""},
        )
        if proc.returncode != 0:
            match = re.search(r"HTTP (\d{3})", proc.stderr)
            code = int(match.group(1)) if match else None
            if code in tolerate:
                return None
            raise GitHubError(f"gh api {method} {path} failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def _api_urllib(
        self, method: str, path: str, payload: dict | None, tolerate: tuple[int, ...] = ()
    ):
        url = path if path.startswith("http") else f"{self.api_root}/{path.lstrip('/')}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
                return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as exc:
            if exc.code in tolerate:
                return None
            raise GitHubError(
                f"{method} {url} -> {exc.code}: "
                f"{exc.read().decode(errors='replace')}"
            ) from exc

    # -- writes (dry-run aware) --------------------------------------------

    def _write(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        tolerate: tuple[int, ...] = (),
    ) -> bool:
        if self.dry_run:
            self._pending.append(f"{method} {path} {json.dumps(payload or {})}")
            return False
        self._api(method, path, payload, tolerate)
        return True

    # -- high level --------------------------------------------------------

    def list_issues(self, state: str = "all", labels: str | None = None) -> list[Issue]:
        issues: list[Issue] = []
        page = 1
        while True:
            path = f"repos/{self.repo}/issues?state={state}&per_page=100&page={page}"
            if labels:
                path += f"&labels={labels}"
            batch = self._api("GET", path) or []
            if not batch:
                break
            for raw in batch:
                # Skip pull requests: the issues endpoint returns them too.
                if "pull_request" in raw:
                    continue
                issues.append(
                    parse_issue(
                        raw["number"],
                        raw.get("title", ""),
                        raw.get("state", "open"),
                        [lbl["name"] for lbl in raw.get("labels", [])],
                        raw.get("body") or "",
                    )
                )
            if len(batch) < 100:
                break
            page += 1
        return issues

    def set_workflow_label(self, issue: Issue, add: str, remove: list[str]) -> bool:
        label_path = f"repos/{self.repo}/issues/{issue.number}/labels"
        # Only touch labels the issue actually carries: GitHub 404s on removing
        # an absent label, and a stale snapshot can make that happen routinely.
        present = set(issue.labels)
        if add and add not in present:
            self._write("POST", label_path, {"labels": [add]})
        for lbl in remove:
            if lbl == add or lbl not in present:
                continue
            self._write("DELETE", f"{label_path}/{urllib.parse.quote(lbl, safe='')}")
        return not self.dry_run

    def comment(self, number: int, body: str) -> bool:
        return self._write(
            "POST", f"repos/{self.repo}/issues/{number}/comments", {"body": body}
        )

    def add_labels(self, number: int, labels: list[str]) -> bool:
        return self._write(
            "POST", f"repos/{self.repo}/issues/{number}/labels", {"labels": labels}
        )

    def remove_label(self, number: int, label: str) -> bool:
        return self._write(
            "DELETE",
            f"repos/{self.repo}/issues/{number}/labels/"
            f"{urllib.parse.quote(label, safe='')}",
            tolerate=(404,),
        )

    def create_issue(self, title: str, body: str, labels: list[str]) -> int | None:
        """Create an issue; returns its number, or None under dry-run."""
        if self.dry_run:
            self._write(
                "POST",
                f"repos/{self.repo}/issues",
                {"title": title, "body": body, "labels": labels},
            )
            return None
        created = self._api(
            "POST",
            f"repos/{self.repo}/issues",
            {"title": title, "body": body, "labels": labels},
        )
        return created["number"] if created else None

    def assign(self, number: int, login: str) -> bool:
        return self._write(
            "POST",
            f"repos/{self.repo}/issues/{number}/assignees",
            {"assignees": [login]},
        )

    @property
    def pending_writes(self) -> list[str]:
        return list(self._pending)
