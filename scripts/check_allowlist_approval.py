#!/usr/bin/env python3
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
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
#
"""Hold an allow-list PR at pending until at least one maintainer has approved it.

A PR is an allow-list PR when it touches any of ``ALLOWLIST_PATHS``.  Other
PRs pass immediately, so the check can be made required without blocking
unrelated changes.

A maintainer is a reviewer with write (or higher) permission on the
repository.  Only each reviewer's latest decisive review counts, so an
approval later followed by "changes requested" or a dismissal does not.

The verdict is a commit status on the PR's head commit, context ``approval``:
``pending`` while an allow-list PR waits for a maintainer, ``success`` once
approved (or for any other PR), and ``error`` when the PR cannot be judged.
A wait for approval is therefore yellow, not red.

The decision logic is kept in pure functions for unit testing; ``main``
fetches the PR files, reviews and reviewer permissions through ``gh api``.

Usage:
    # Print the verdict for one PR (exit 0 only when it is success):
    GH_TOKEN=... python3 scripts/check_allowlist_approval.py --repo OWNER/REPO --pr 123
    # Post the verdict for the open PR(s) whose head is SHA:
    GH_TOKEN=... python3 scripts/check_allowlist_approval.py --repo OWNER/REPO --head-sha SHA --post-status
    # Post the verdict for every open PR:
    GH_TOKEN=... python3 scripts/check_allowlist_approval.py --repo OWNER/REPO --all-open --post-status
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable

ALLOWLIST_PATHS = frozenset(
    {
        "actions.yml",
        "approved_patterns.yml",
        ".github/actions/for-dependabot-triggered-reviews/action.yml",
    }
)

# Legacy ``permission`` values returned by the collaborator permission API;
# ``maintain`` is reported as ``write`` there.
MAINTAINER_PERMISSIONS = frozenset({"admin", "maintain", "write"})

# Review states that set a reviewer's verdict.  COMMENTED and PENDING do not
# change it, matching how GitHub itself counts approvals.
DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})

# A GitHub API outage must not leave an approved PR red until someone re-runs
# it by hand, so transient failures are retried.  Every other failure, and one
# that outlasts the retries, still fails the check.
RETRY_DELAYS = (5, 15, 45)
TRANSIENT_ERROR = re.compile(
    r"HTTP (5\d\d|429)|timeout|connection (reset|refused)|EOF", re.IGNORECASE
)

# The commit-status context the "Required checks" ruleset in .asf.yaml requires.
STATUS_CONTEXT = "approval"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_allowlist_pr(files: Iterable[str]) -> bool:
    return any(f in ALLOWLIST_PATHS for f in files)


def approvers(reviews: Iterable[dict]) -> list[str]:
    """Logins whose latest decisive review is an approval.

    ``reviews`` must be in chronological order, as the API returns them.
    """
    latest: dict[str, str] = {}
    for review in reviews:
        if review["state"] in DECISIVE_STATES:
            latest[review["login"]] = review["state"]
    return sorted(login for login, state in latest.items() if state == "APPROVED")


def maintainer_approvers(
    reviews: Iterable[dict], permission_of: Callable[[str], str]
) -> list[str]:
    return [
        login
        for login in approvers(reviews)
        if permission_of(login) in MAINTAINER_PERMISSIONS
    ]


def verdict(
    listed_all_files: bool, allowlist_pr: bool, approved_by: list[str]
) -> tuple[str, str]:
    """Commit-status ``(state, description)`` for one PR."""
    if not listed_all_files:
        return "error", "Could not list every changed file; cannot tell if this is an allow-list PR."
    if not allowlist_pr:
        return "success", "Not an allow-list PR; no maintainer approval required."
    if approved_by:
        return "success", f"Approved by maintainer(s): {', '.join(approved_by)}"[:140]
    return "pending", "Waiting for approval from a maintainer (a reviewer with write access)."


# A commit status belongs to the commit, not to a PR, so when several open PRs
# share a head commit the strictest verdict must win; otherwise a second PR
# from the same commit against another base could overwrite a pending status.
STRICTNESS = {"success": 0, "pending": 1, "error": 2}


def strictest(verdicts: Iterable[tuple[str, str]]) -> tuple[str, str]:
    return max(verdicts, key=lambda v: STRICTNESS[v[0]])


def open_prs_with_head(prs: Iterable[dict], head_sha: str) -> list[int]:
    """Numbers of the open PRs, from ``{number, sha}`` entries, whose head is ``head_sha``."""
    return sorted(pr["number"] for pr in prs if pr["sha"] == head_sha)


def _gh_lines(*args: str) -> list[str]:
    cmd = ["gh", "api", *args]
    for delay in (*RETRY_DELAYS, None):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return [line for line in result.stdout.splitlines() if line]
        if delay is None or not TRANSIENT_ERROR.search(result.stderr):
            sys.stderr.write(result.stderr)
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        print(f"::warning::{' '.join(cmd)} failed; retrying in {delay}s")
        time.sleep(delay)
    raise AssertionError("unreachable")


def _permission_of(repo: str, login: str) -> str:
    try:
        lines = _gh_lines(
            f"repos/{repo}/collaborators/{login}/permission", "--jq", ".permission"
        )
    except subprocess.CalledProcessError:
        return "none"
    return lines[0] if lines else "none"


def evaluate(repo: str, pr: int) -> tuple[str, str]:
    """Fetch one PR's files and reviews and return its ``verdict``."""
    # A rename lists only the new name, so include the old one too.
    entries = _gh_lines(
        f"repos/{repo}/pulls/{pr}/files",
        "--paginate",
        "--jq",
        ".[] | [.filename, (.previous_filename // empty)] | @json",
    )
    files = [path for entry in entries for path in json.loads(entry)]
    # The files API stops at 3000 entries; fail closed if the list is short.
    changed = int(_gh_lines(f"repos/{repo}/pulls/{pr}", "--jq", ".changed_files")[0])
    if len(entries) < changed or not is_allowlist_pr(files):
        return verdict(len(entries) >= changed, False, [])

    reviews = [
        json.loads(line)
        for line in _gh_lines(
            f"repos/{repo}/pulls/{pr}/reviews",
            "--paginate",
            "--jq",
            ".[] | {login: .user.login, state: .state}",
        )
    ]
    approved_by = maintainer_approvers(reviews, lambda login: _permission_of(repo, login))
    return verdict(True, True, approved_by)


def _open_prs(repo: str) -> list[dict]:
    return [
        json.loads(line)
        for line in _gh_lines(
            f"repos/{repo}/pulls?state=open&per_page=100",
            "--paginate",
            "--jq",
            ".[] | {number, sha: .head.sha} | @json",
        )
    ]


def _post_status(repo: str, sha: str, state: str, description: str, target_url: str) -> None:
    args = [
        "-X", "POST", f"repos/{repo}/statuses/{sha}",
        "-f", f"state={state}",
        "-f", f"context={STATUS_CONTEXT}",
        "-f", f"description={description}",
    ]
    if target_url:
        args += ["-f", f"target_url={target_url}"]
    _gh_lines(*args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pr", type=int, help="pull request number")
    which.add_argument("--head-sha", help="judge the open PR(s) with this head commit")
    which.add_argument("--all-open", action="store_true", help="judge every open PR")
    parser.add_argument(
        "--post-status",
        action="store_true",
        help=f"post the verdict as the '{STATUS_CONTEXT}' commit status on the PR head",
    )
    parser.add_argument("--target-url", default="", help="link for the posted status")
    args = parser.parse_args(argv)

    if args.pr is not None:
        if args.post_status:
            parser.error("--post-status needs --head-sha or --all-open")
        state, description = evaluate(args.repo, args.pr)
        print(f"{state}: {description}")
        return 0 if state == "success" else 1

    if args.head_sha is not None and not SHA_RE.match(args.head_sha):
        print(f"::error::Not a commit SHA: {args.head_sha!r}")
        return 1
    prs = _open_prs(args.repo)
    shas = [args.head_sha] if args.head_sha is not None else sorted({pr["sha"] for pr in prs})
    if not any(open_prs_with_head(prs, sha) for sha in shas):
        print("No open PR to judge.")
        return 0

    failed = False
    for sha in shas:
        verdicts = []
        for number in open_prs_with_head(prs, sha):
            try:
                state, description = evaluate(args.repo, number)
            except (subprocess.CalledProcessError, ValueError, IndexError) as exc:
                failed = True
                state, description = "error", f"Could not evaluate the PR: {type(exc).__name__}"
            print(f"#{number} {sha[:12]}: {state}: {description}")
            verdicts.append((state, description))
        if verdicts and args.post_status:
            _post_status(args.repo, sha, *strictest(verdicts), args.target_url)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
