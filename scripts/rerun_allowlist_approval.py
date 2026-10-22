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
"""Re-run a PR's failed ``approval`` check once a maintainer has approved it.

The ``approval`` job runs on ``pull_request`` and again on each review.  The
review-triggered run passes once a maintainer approves, but the run from the
``pull_request`` event keeps its earlier failure, and the ``Required checks``
ruleset keeps blocking the merge on it.  Re-running that failed run makes it
evaluate the PR again, now with the approval in place.

Only the latest ``pull_request`` run for the head commit is considered, and
only if it finished with a failure: a passing, running or queued run already
reflects the current state, or soon will.

The decision logic is a pure function for unit testing; ``main`` lists the
runs and requests the re-run through ``gh api``.

Usage:
    GH_TOKEN=... python3 scripts/rerun_allowlist_approval.py --repo OWNER/REPO --head-sha SHA
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable

WORKFLOW_FILE = "allowlist_approval.yml"

# Conclusions a re-run can turn green.  ``cancelled`` covers a run cut short
# by the workflow's own concurrency group.
RERUNNABLE = frozenset({"failure", "cancelled", "timed_out"})

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def run_to_rerun(runs: Iterable[dict]) -> int | None:
    """Id of the latest ``pull_request`` run if it needs a re-run, else None.

    ``runs`` are workflow runs for one head commit, in any order.
    """
    pr_runs = [r for r in runs if r["event"] == "pull_request"]
    if not pr_runs:
        return None
    latest = max(pr_runs, key=lambda r: (r["created_at"], r["id"]))
    if latest["status"] == "completed" and latest["conclusion"] in RERUNNABLE:
        return latest["id"]
    return None


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", "api", *args], check=True, capture_output=True, text=True
    ).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--head-sha", required=True, help="PR head commit")
    args = parser.parse_args(argv)
    if not SHA_RE.match(args.head_sha):
        print(f"::error::Not a commit SHA: {args.head_sha!r}")
        return 1

    out = _gh(
        f"repos/{args.repo}/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?head_sha={args.head_sha}&per_page=100",
        "--jq",
        ".workflow_runs[] | {id, event, status, conclusion, created_at} | @json",
    )
    runs = [json.loads(line) for line in out.splitlines() if line]
    run_id = run_to_rerun(runs)
    if run_id is None:
        print("No failed pull_request run of the approval check to re-run.")
        return 0
    _gh("-X", "POST", f"repos/{args.repo}/actions/runs/{run_id}/rerun-failed-jobs")
    print(f"Re-running the approval check from run {run_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
