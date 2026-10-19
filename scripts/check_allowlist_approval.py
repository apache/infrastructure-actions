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
"""Fail an allow-list PR until at least one maintainer has approved it.

A PR is an allow-list PR when it touches any of ``ALLOWLIST_PATHS``.  Other
PRs pass immediately, so the check can be made required without blocking
unrelated changes.

A maintainer is a reviewer with write (or higher) permission on the
repository.  Only each reviewer's latest decisive review counts, so an
approval later followed by "changes requested" or a dismissal does not.

A maintainer can waive the approval by adding ``OVERRIDE_LABEL`` -- for an
allow-list PR they authored themselves, which GitHub does not let them
approve.  The label counts only while it is on the PR and whoever last added
it has write access: triagers can add labels too.

The decision logic is kept in pure functions for unit testing; ``main``
fetches the PR files, reviews and reviewer permissions through ``gh api``.

Usage:
    GH_TOKEN=... python3 scripts/check_allowlist_approval.py --repo OWNER/REPO --pr 123
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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

OVERRIDE_LABEL = "allowlist-approval-override"

# Review states that set a reviewer's verdict.  COMMENTED and PENDING do not
# change it, matching how GitHub itself counts approvals.
DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


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


def override_by(
    labels: Iterable[str],
    label_events: Iterable[dict],
    permission_of: Callable[[str], str],
) -> str | None:
    """The maintainer whose override label waives the approval, or None.

    ``label_events`` must be in chronological order, as the API returns them.
    A label with no visible ``labeled`` event counts for nothing: who added it
    is unknown, and unknown must not be more permissive than a refusal.
    """
    if OVERRIDE_LABEL not in labels:
        return None
    added = [
        e["login"]
        for e in label_events
        if e["event"] == "labeled" and e["label"] == OVERRIDE_LABEL
    ]
    if not added:
        return None
    return added[-1] if permission_of(added[-1]) in MAINTAINER_PERMISSIONS else None


def _gh_lines(*args: str) -> list[str]:
    out = subprocess.run(
        ["gh", "api", *args], check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def _permission_of(repo: str, login: str) -> str:
    try:
        lines = _gh_lines(
            f"repos/{repo}/collaborators/{login}/permission", "--jq", ".permission"
        )
    except subprocess.CalledProcessError:
        return "none"
    return lines[0] if lines else "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    args = parser.parse_args(argv)

    # A rename lists only the new name, so include the old one too.
    entries = _gh_lines(
        f"repos/{args.repo}/pulls/{args.pr}/files",
        "--paginate",
        "--jq",
        ".[] | [.filename, (.previous_filename // empty)] | @json",
    )
    files = [path for entry in entries for path in json.loads(entry)]
    # The files API stops at 3000 entries; fail closed if the list is short.
    changed = int(_gh_lines(f"repos/{args.repo}/pulls/{args.pr}", "--jq", ".changed_files")[0])
    if len(entries) < changed:
        print(
            f"::error::Only {len(entries)} of {changed} changed files could be listed; "
            "cannot tell whether this is an allow-list PR."
        )
        return 1
    if not is_allowlist_pr(files):
        print("Not an allow-list PR; no maintainer approval required.")
        return 0

    reviews = [
        json.loads(line)
        for line in _gh_lines(
            f"repos/{args.repo}/pulls/{args.pr}/reviews",
            "--paginate",
            "--jq",
            ".[] | {login: .user.login, state: .state}",
        )
    ]
    approved_by = maintainer_approvers(
        reviews, lambda login: _permission_of(args.repo, login)
    )
    if approved_by:
        print(f"Allow-list PR approved by maintainer(s): {', '.join(approved_by)}")
        return 0

    labels = _gh_lines(f"repos/{args.repo}/issues/{args.pr}", "--jq", ".labels[].name")
    label_events = [
        json.loads(line)
        for line in _gh_lines(
            f"repos/{args.repo}/issues/{args.pr}/events",
            "--paginate",
            "--jq",
            '.[] | select(.event == "labeled" or .event == "unlabeled")'
            " | {event, label: .label.name, login: .actor.login}",
        )
    ]
    waived_by = override_by(labels, label_events, lambda login: _permission_of(args.repo, login))
    if waived_by:
        print(f"Maintainer approval waived by {waived_by} with the {OVERRIDE_LABEL} label.")
        return 0
    print(
        "::error::Allow-list PR has no approval from a maintainer "
        "(a reviewer with write access to the repository). A maintainer can "
        f"waive it with the {OVERRIDE_LABEL} label."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
