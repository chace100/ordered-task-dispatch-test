#!/usr/bin/env python3
"""A tiny in-memory GitHub REST stub for exercising the scripts end to end.

It implements just the endpoints the orchestrator/worker/seed call, so the
real code paths (urllib transport, payload shapes, label transitions) run
against HTTP instead of being mocked away. Point the client at it with
``GITHUB_API_URL=http://127.0.0.1:<port>``.

Standalone use:
    FAKE_SEED=examples/issues.demo.json FAKE_STATE_OUT=/tmp/state.json \
        python tests/fake_github.py 8799
"""

from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATE: dict[int, dict] = {}
NEXT_NUMBER = 1

_ISSUE_RE = re.compile(r"^/repos/[^/]+/[^/]+/issues(?:/(\d+))?(.*)$")


def _label_names(issue: dict) -> list[str]:
    return [lbl["name"] for lbl in issue["labels"]]


def _issue_json(issue: dict) -> dict:
    return {
        "number": issue["number"],
        "title": issue["title"],
        "body": issue["body"],
        "state": issue["state"],
        "labels": [{"name": n} for n in issue["labels"]],
    }


def load_seed(path: str) -> None:
    global NEXT_NUMBER
    raw = json.loads(open(path).read())
    for item in raw:
        labels = [lbl if isinstance(lbl, str) else lbl["name"] for lbl in item.get("labels", [])]
        STATE[item["number"]] = {
            "number": item["number"],
            "title": item.get("title", ""),
            "body": item.get("body", ""),
            "state": item.get("state", "open"),
            "labels": labels,
            "comments": [],
            "assignees": [],
        }
    NEXT_NUMBER = max(STATE, default=0) + 1


def dump_state(path: str) -> None:
    with open(path, "w") as fh:
        json.dump(list(STATE.values()), fh, indent=2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence per-request logging
        pass

    def _send(self, code: int, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def _persist(self):
        out = os.environ.get("FAKE_STATE_OUT")
        if out:
            dump_state(out)

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        match = _ISSUE_RE.match(parsed.path)
        if match and match.group(1) is None:
            query = parse_qs(parsed.query)
            state = query.get("state", ["open"])[0]
            labels = query.get("labels", [None])[0]
            wanted = set(labels.split(",")) if labels else None
            result = []
            for issue in STATE.values():
                if state != "all" and issue["state"] != state:
                    continue
                if wanted and not wanted.issubset(set(issue["labels"])):
                    continue
                result.append(_issue_json(issue))
            self._send(200, result)
            return
        self._send(404, {"message": "not found"})

    def do_POST(self):
        global NEXT_NUMBER
        match = _ISSUE_RE.match(urlparse(self.path).path)
        if not match:
            self._send(404, {"message": "not found"})
            return
        number, rest = match.group(1), match.group(2)
        payload = self._body()

        if number is None and not rest:  # create issue
            issue = {
                "number": NEXT_NUMBER,
                "title": payload.get("title", ""),
                "body": payload.get("body", ""),
                "state": "open",
                "labels": list(payload.get("labels", []) or []),
                "comments": [],
                "assignees": [],
            }
            STATE[NEXT_NUMBER] = issue
            NEXT_NUMBER += 1
            self._persist()
            self._send(201, _issue_json(issue))
            return

        issue = STATE.get(int(number or 0))
        if issue is None:
            self._send(404, {"message": "issue not found"})
            return

        if rest == "/labels":  # add labels
            for name in payload.get("labels", []):
                if name not in issue["labels"]:
                    issue["labels"].append(name)
            self._persist()
            self._send(200, [{"name": n} for n in issue["labels"]])
            return
        if rest == "/comments":
            issue["comments"].append({"body": payload.get("body", "")})
            self._persist()
            self._send(201, {"body": payload.get("body", "")})
            return
        if rest == "/assignees":
            for login in payload.get("assignees", []):
                if login not in issue["assignees"]:
                    issue["assignees"].append(login)
            self._persist()
            self._send(201, {"assignees": issue["assignees"]})
            return
        self._send(404, {"message": "not found"})

    def do_PATCH(self):
        match = _ISSUE_RE.match(urlparse(self.path).path)
        if not match:
            self._send(404, {"message": "not found"})
            return
        issue = STATE.get(int(match.group(1) or 0))
        if issue is None:
            self._send(404, {"message": "issue not found"})
            return
        payload = self._body()
        if "state" in payload:
            issue["state"] = payload["state"]
        if "body" in payload:
            issue["body"] = payload["body"]
        self._persist()
        self._send(200, _issue_json(issue))

    def do_DELETE(self):
        match = _ISSUE_RE.match(urlparse(self.path).path)
        if not match:
            self._send(404, {"message": "not found"})
            return
        number, rest = match.group(1), match.group(2)
        issue = STATE.get(int(number or 0))
        if issue is None:
            self._send(404, {"message": "issue not found"})
            return
        m = re.match(r"^/labels/(.+)$", rest)
        if m:
            from urllib.parse import unquote

            label = unquote(m.group(1))
            if label not in issue["labels"]:
                # Mirror GitHub: removing a label the issue does not carry 404s.
                self._send(404, {"message": "Label does not exist"})
                return
            issue["labels"] = [lbl for lbl in issue["labels"] if lbl != label]
            self._persist()
            self._send(200, [{"name": n} for n in issue["labels"]])
            return
        self._send(404, {"message": "not found"})


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    seed = os.environ.get("FAKE_SEED")
    if seed:
        load_seed(seed)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"fake github listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
