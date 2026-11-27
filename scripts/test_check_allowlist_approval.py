#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
"""Unit tests for ``check_allowlist_approval.py``."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "check_allowlist_approval", Path(__file__).with_name("check_allowlist_approval.py")
)
caa = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_allowlist_approval"] = caa
_spec.loader.exec_module(caa)


def _review(login, state):
    return {"login": login, "state": state}


def test_allowlist_pr_detected_for_each_allowlist_file():
    for path in caa.ALLOWLIST_PATHS:
        assert caa.is_allowlist_pr(["README.md", path])


def test_non_allowlist_pr():
    assert not caa.is_allowlist_pr(["README.md", "utils/actions.yml"])
    assert not caa.is_allowlist_pr([])


def test_approvers_uses_latest_decisive_review():
    reviews = [
        _review("alice", "APPROVED"),
        _review("bob", "APPROVED"),
        _review("bob", "CHANGES_REQUESTED"),
        _review("carol", "CHANGES_REQUESTED"),
        _review("carol", "APPROVED"),
    ]
    assert caa.approvers(reviews) == ["alice", "carol"]


def test_comment_does_not_override_approval():
    reviews = [_review("alice", "APPROVED"), _review("alice", "COMMENTED")]
    assert caa.approvers(reviews) == ["alice"]


def test_dismissed_approval_does_not_count():
    reviews = [_review("alice", "APPROVED"), _review("alice", "DISMISSED")]
    assert caa.approvers(reviews) == []


def test_maintainer_approvers_filters_by_permission():
    permissions = {"alice": "admin", "bob": "write", "carol": "read", "dave": "none"}
    reviews = [_review(login, "APPROVED") for login in permissions]
    assert caa.maintainer_approvers(reviews, permissions.__getitem__) == [
        "alice",
        "bob",
    ]


def test_no_maintainer_approval():
    reviews = [_review("carol", "APPROVED"), _review("alice", "COMMENTED")]
    permissions = {"alice": "admin", "carol": "read"}
    assert caa.maintainer_approvers(reviews, permissions.__getitem__) == []


def _fake_gh(monkeypatch, results):
    """Replay (returncode, stdout, stderr) tuples; return the delays slept."""
    pending = list(results)
    delays = []

    def run(cmd, **_kwargs):
        code, out, err = pending.pop(0)
        return subprocess.CompletedProcess(cmd, code, out, err)

    monkeypatch.setattr(caa.subprocess, "run", run)
    monkeypatch.setattr(caa.time, "sleep", delays.append)
    return delays


def test_gh_lines_retries_a_server_error(monkeypatch):
    delays = _fake_gh(
        monkeypatch,
        [
            (1, "", "gh: Internal Server Error (HTTP 500)\n"),
            (1, "", "gh: Bad Gateway (HTTP 502)\n"),
            (0, "1\n", ""),
        ],
    )
    assert caa._gh_lines("repos/o/r/pulls/1") == ["1"]
    assert delays == list(caa.RETRY_DELAYS[:2])


def test_gh_lines_retries_a_connection_error(monkeypatch):
    delays = _fake_gh(
        monkeypatch,
        [(1, "", "connection reset by peer\n"), (0, "1\n", "")],
    )
    assert caa._gh_lines("repos/o/r/pulls/1") == ["1"]
    assert len(delays) == 1


def test_gh_lines_does_not_retry_a_client_error(monkeypatch):
    delays = _fake_gh(monkeypatch, [(1, "", "gh: Not Found (HTTP 404)\n")])
    with pytest.raises(subprocess.CalledProcessError):
        caa._gh_lines("repos/o/r/collaborators/x/permission")
    assert delays == []


def test_gh_lines_fails_closed_and_reports_when_retries_run_out(monkeypatch, capsys):
    error = "gh: Internal Server Error (HTTP 500)\n"
    delays = _fake_gh(monkeypatch, [(1, "", error)] * (len(caa.RETRY_DELAYS) + 1))
    with pytest.raises(subprocess.CalledProcessError):
        caa._gh_lines("repos/o/r/pulls/1")
    assert delays == list(caa.RETRY_DELAYS)
    assert error in capsys.readouterr().err


def test_verdict_non_allowlist_pr_passes():
    assert caa.verdict(True, False, [])[0] == "success"


def test_verdict_unapproved_allowlist_pr_is_pending_not_failed():
    state, description = caa.verdict(True, True, [])
    assert state == "pending"
    assert "maintainer" in description


def test_verdict_approved_allowlist_pr_passes():
    state, description = caa.verdict(True, True, ["alice"])
    assert state == "success"
    assert "alice" in description


def test_verdict_incomplete_file_list_is_an_error():
    assert caa.verdict(False, False, [])[0] == "error"


def test_verdict_descriptions_fit_the_status_limit():
    many = [f"maintainer{i}" for i in range(40)]
    for args in [(True, True, many), (True, True, []), (False, False, []), (True, False, [])]:
        assert len(caa.verdict(*args)[1]) <= 140


def test_open_prs_with_head_matches_only_that_commit():
    prs = [
        {"number": 3, "sha": "a" * 40},
        {"number": 1, "sha": "a" * 40},
        {"number": 2, "sha": "b" * 40},
    ]
    assert caa.open_prs_with_head(prs, "a" * 40) == [1, 3]
    assert caa.open_prs_with_head(prs, "c" * 40) == []


def test_main_rejects_a_non_sha(capsys):
    assert caa.main(["--repo", "o/r", "--head-sha", "main"]) == 1
    assert "Not a commit SHA" in capsys.readouterr().out


def test_main_refuses_post_status_for_a_single_pr():
    with pytest.raises(SystemExit):
        caa.main(["--repo", "o/r", "--pr", "1", "--post-status"])


def _api_router(monkeypatch, files, reviews, permissions, prs):
    """Fake ``_gh_lines`` answering the endpoints ``main`` calls; returns posted statuses."""
    import json

    posted = []

    def gh_lines(*args):
        path = args[0] if args[0] != "-X" else args[2]
        if args[0] == "-X":
            fields = dict(a.split("=", 1) for a in args[3:] if "=" in a)
            posted.append((path.rsplit("/", 1)[1], fields["state"], fields["context"]))
            return []
        if path.startswith("repos/o/r/pulls?state=open"):
            return [json.dumps(pr) for pr in prs]
        if path.endswith("/files"):
            return [json.dumps([f]) for f in files]
        if path.endswith("/reviews"):
            return [json.dumps({"login": login, "state": s}) for login, s in reviews]
        if "/collaborators/" in path:
            return [permissions.get(path.split("/")[4], "none")]
        return [str(len(files))]  # changed_files

    monkeypatch.setattr(caa, "_gh_lines", gh_lines)
    return posted


def test_main_posts_pending_for_an_unapproved_allowlist_pr(monkeypatch):
    sha = "a" * 40
    posted = _api_router(
        monkeypatch,
        files=["actions.yml"],
        reviews=[("carol", "APPROVED")],
        permissions={"carol": "read"},
        prs=[{"number": 7, "sha": sha}, {"number": 8, "sha": "b" * 40}],
    )
    assert caa.main(["--repo", "o/r", "--head-sha", sha, "--post-status"]) == 0
    assert posted == [(sha, "pending", "approval")]


def test_main_posts_success_once_a_maintainer_approves(monkeypatch):
    sha = "a" * 40
    posted = _api_router(
        monkeypatch,
        files=["approved_patterns.yml"],
        reviews=[("alice", "APPROVED")],
        permissions={"alice": "write"},
        prs=[{"number": 7, "sha": sha}],
    )
    assert caa.main(["--repo", "o/r", "--head-sha", sha, "--post-status"]) == 0
    assert posted == [(sha, "success", "approval")]


def test_main_all_open_posts_for_every_pr(monkeypatch):
    posted = _api_router(
        monkeypatch,
        files=["README.md"],
        reviews=[],
        permissions={},
        prs=[{"number": 2, "sha": "b" * 40}, {"number": 1, "sha": "a" * 40}],
    )
    assert caa.main(["--repo", "o/r", "--all-open", "--post-status"]) == 0
    assert posted == [("a" * 40, "success", "approval"), ("b" * 40, "success", "approval")]


def test_main_single_pr_exit_code_follows_the_verdict(monkeypatch, capsys):
    _api_router(monkeypatch, ["actions.yml"], [], {}, [])
    assert caa.main(["--repo", "o/r", "--pr", "7"]) == 1
    assert capsys.readouterr().out.startswith("pending:")
