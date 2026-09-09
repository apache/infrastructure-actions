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
# dependencies = [
#     "requests>=2.31",
#     "rich>=13.0",
# ]
# ///

"""
Report which ASF repositories currently have GitHub Actions jobs queued or running.

Answers "who is using the runners right now, and who is waiting" without org-admin
rights: the self-hosted runner endpoints need `admin:org`, but check-run state is
readable by anyone who can read the repo.

Two phases, both GraphQL:

1. Discovery — page through the org's repositories and read the `.github/workflows`
   tree directly, so a repo counts as using Actions only when it really has workflow
   files on its default branch.
2. Status — batch the surviving repos into aliased queries (one request covers many
   repos) and count check runs, which map one-to-one onto workflow jobs.

Usage:
    uv run utils/actions-queue-status.py
    uv run utils/actions-queue-status.py --csv /tmp/asf-ci.csv
    uv run utils/actions-queue-status.py --save-repos /tmp/repos.txt
    uv run utils/actions-queue-status.py --repos-file /tmp/repos.txt --top 40
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# GitHub answers a query it considers too expensive with a bare 502 rather than a typed
# error, and enforces the points budget per minute as well as per hour — so a burst
# trips it even when the hourly balance looks healthy. Both are worth waiting out.
RETRYABLE = ("502", "503", "504", "rate limit", "RATE_LIMIT", "secondary rate", "timeout")

# Points held back so a sweep never leaves the caller's hourly budget at zero.
BUDGET_FLOOR = 200

REPO_PAGE_QUERY = """
query($org: String!, $after: String) {
  organization(login: $org) {
    repositories(first: 100, after: $after, orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        isArchived
        isDisabled
        defaultBranchRef { name }
        workflows: object(expression: "HEAD:.github/workflows") {
          ... on Tree { entries { name } }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""

STATUS_FRAGMENTS = """
fragment CI on Repository {
  nameWithOwner
  defaultBranchRef { target { ... on Commit { ...Suites } } }
  pullRequests(states: OPEN, first: %(prs)d, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes { commits(last: 1) { nodes { commit { ...Suites } } } }
  }
}
fragment Suites on Commit {
  checkSuites(first: %(suites)d) {
    nodes {
      status
      workflowRun { workflow { name } }
      queued:  checkRuns(first: 1, filterBy: {status: QUEUED})      { totalCount }
      running: checkRuns(first: 1, filterBy: {status: IN_PROGRESS}) { totalCount }
    }
  }
}
"""

CSV_HEADER = ["repo", "queued_jobs", "running_jobs", "suites_queued", "suites_in_progress", "workflows"]


class BudgetExhausted(RuntimeError):
    """Raised when the GraphQL points budget runs too low to keep querying safely."""


class GraphQLClient:
    """Minimal GraphQL client over the `gh` CLI, or `requests` when `--no-gh` is given.

    Tracks the points balance reported by each query. The in-query `rateLimit` block is
    the only trustworthy source: the REST `/rate_limit` endpoint keeps reporting a full
    GraphQL budget while the API is actively rejecting queries as rate limited.
    """

    def __init__(self, token: str | None = None, use_requests: bool = False):
        self.token = token
        self.use_requests = use_requests
        self._lock = threading.Lock()
        self._remaining: int | None = None
        if use_requests and not token:
            raise SystemExit("--no-gh requires --github-token, GH_TOKEN or GITHUB_TOKEN")
        if not use_requests and not shutil.which("gh"):
            raise SystemExit("gh CLI not found — install it, or use --no-gh with a token")

    @property
    def remaining(self) -> int | None:
        """Return the points balance reported by the most recent successful query."""
        with self._lock:
            return self._remaining

    def _note_budget(self, payload: dict) -> None:
        limit = (payload.get("data") or {}).get("rateLimit")
        if limit:
            with self._lock:
                self._remaining = limit["remaining"]

    def _call_gh(self, query: str, variables: dict) -> tuple[dict | None, str]:
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            cmd.extend(["-f", f"{key}={value}"])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None, (result.stderr or result.stdout).strip()[:200]
        try:
            return json.loads(result.stdout), ""
        except json.JSONDecodeError:
            return None, result.stdout.strip()[:200]

    def _call_requests(self, query: str, variables: dict) -> tuple[dict | None, str]:
        response = requests.post(
            GITHUB_GRAPHQL_URL,
            headers={"Authorization": f"bearer {self.token}", "Accept": "application/json"},
            json={"query": query, "variables": variables},
            timeout=60,
        )
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:150]}"
        return response.json(), ""

    def query(self, query: str, variables: dict | None = None, attempts: int = 4) -> dict:
        """Run a query, retrying transient failures, and return the payload.

        On give-up the returned dict carries `__error__` instead of raising: callers
        decide whether a failed repo is fatal or merely skipped.
        """
        variables = variables or {}
        error = "no attempt made"
        for attempt in range(attempts):
            payload, error = (
                self._call_requests(query, variables)
                if self.use_requests
                else self._call_gh(query, variables)
            )
            if payload is not None and not payload.get("errors"):
                self._note_budget(payload)
                return payload
            if payload is not None and payload.get("errors"):
                error = json.dumps(payload["errors"])[:200]
            if not any(marker in error for marker in RETRYABLE):
                break
            # 4s, 16s, 64s. The per-minute points cap needs real time to drain; a tight
            # retry only burns more of the budget it is waiting on.
            time.sleep(4 ** (attempt + 1))
        return {"__error__": error}

    def check_budget(self) -> None:
        """Stop the sweep before it drains the caller's hourly GraphQL allowance."""
        remaining = self.remaining
        if remaining is not None and remaining < BUDGET_FLOOR:
            raise BudgetExhausted(f"stopping with {remaining} GraphQL points left")


def resolve_token(args: argparse.Namespace) -> str | None:
    """Resolve the token: --github-token, then GH_TOKEN, then GITHUB_TOKEN."""
    if args.github_token:
        return args.github_token
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def has_workflows(node: dict) -> bool:
    """Report whether the repo's default branch carries at least one workflow file."""
    tree = node.get("workflows")
    if not tree:
        return False
    return any(entry["name"].endswith((".yml", ".yaml")) for entry in tree.get("entries", []))


def discover_repos(client: GraphQLClient, org: str, include_archived: bool) -> list[str]:
    """Return every non-archived repo in the org that defines workflow files."""
    repos: list[str] = []
    scanned = 0
    cursor = None
    while True:
        variables = {"org": org}
        if cursor:
            variables["after"] = cursor
        payload = client.query(REPO_PAGE_QUERY, variables)
        if "__error__" in payload:
            # Partial discovery silently under-reports the org, which is worse than no
            # answer at all — fail loudly and say how far the paging got.
            raise SystemExit(
                f"discovery failed after {scanned} repos (cursor {cursor}): {payload['__error__']}"
            )
        page = payload["data"]["organization"]["repositories"]
        for node in page["nodes"]:
            scanned += 1
            if node["isDisabled"] or (node["isArchived"] and not include_archived):
                continue
            if not node.get("defaultBranchRef"):
                continue
            if has_workflows(node):
                repos.append(node["name"])
        console.print(
            f"[dim]  scanned {scanned} repos, {len(repos)} with workflows "
            f"(points left {client.remaining})[/]"
        )
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    console.print(f"[cyan]Discovery: {scanned} repos scanned, {len(repos)} use GitHub Actions[/]")
    return repos


def build_status_query(org: str, names: list[str], prs: int, suites: int) -> str:
    """Build one aliased query covering every repo in the batch."""
    aliases = "\n".join(
        f'  r{index}: repository(owner: "{org}", name: "{name}") {{ ...CI }}'
        for index, name in enumerate(names)
    )
    budget = "  rateLimit { cost remaining resetAt }"
    fragments = STATUS_FRAGMENTS % {"prs": prs, "suites": suites}
    return f"query {{\n{aliases}\n{budget}\n}}\n{fragments}"


def collect_commits(repo_node: dict) -> list[dict]:
    """Flatten a repo's default-branch head and open-PR heads into a commit list."""
    commits = []
    head = (repo_node.get("defaultBranchRef") or {}).get("target")
    if head:
        commits.append(head)
    for pull_request in (repo_node.get("pullRequests") or {}).get("nodes", []):
        for entry in (pull_request.get("commits") or {}).get("nodes", []):
            if entry.get("commit"):
                commits.append(entry["commit"])
    return commits


def summarize_repo(repo_node: dict) -> dict:
    """Reduce one repo's check suites to queued/running job counts."""
    queued = running = suites_queued = suites_running = 0
    workflows: dict[str, int] = {}
    for commit in collect_commits(repo_node):
        for suite in (commit.get("checkSuites") or {}).get("nodes", []):
            suite_queued = suite["queued"]["totalCount"]
            suite_running = suite["running"]["totalCount"]
            queued += suite_queued
            running += suite_running
            if suite["status"] == "QUEUED":
                suites_queued += 1
            elif suite["status"] == "IN_PROGRESS":
                suites_running += 1
            run = suite.get("workflowRun")
            if run and (suite_queued + suite_running):
                name = run["workflow"]["name"]
                workflows[name] = workflows.get(name, 0) + suite_queued + suite_running
    return {
        "repo": repo_node["nameWithOwner"],
        "queued_jobs": queued,
        "running_jobs": running,
        "suites_queued": suites_queued,
        "suites_in_progress": suites_running,
        "workflows": workflows,
    }


def run_batch(client: GraphQLClient, org: str, names: list[str], prs: int, suites: int) -> list[dict]:
    """Query one batch, halving it on failure so one heavy repo cannot sink the rest.

    Halving is why the budget has to be checked here and not only by the caller: a batch
    that splits all the way down issues far more queries than the plan accounted for.
    """
    client.check_budget()
    payload = client.query(build_status_query(org, names, prs, suites))
    if "__error__" in payload or not payload.get("data"):
        if len(names) == 1:
            console.print(f"[yellow]  skipped {names[0]}: {payload.get('__error__')}[/]")
            return []
        middle = len(names) // 2
        return run_batch(client, org, names[:middle], prs, suites) + run_batch(
            client, org, names[middle:], prs, suites
        )
    return [
        summarize_repo(node) for key, node in payload["data"].items() if node and key != "rateLimit"
    ]


def csv_path(base: str, ordering: str) -> str:
    """Derive the per-ordering CSV filename from the --csv argument."""
    stem, dot, extension = base.rpartition(".")
    return f"{stem}-{ordering}{dot}{extension}" if dot else f"{base}-{ordering}"


def csv_row(row: dict) -> list:
    """Flatten one repo summary into CSV cells."""
    workflows = "; ".join(f"{name}={count}" for name, count in sorted(row["workflows"].items()))
    return [
        row["repo"],
        row["queued_jobs"],
        row["running_jobs"],
        row["suites_queued"],
        row["suites_in_progress"],
        workflows,
    ]


def write_csv(path: str, rows: list[dict], totals: dict) -> None:
    """Write one ordering of the snapshot as CSV."""
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow(csv_row(row))
        writer.writerow(
            ["TOTAL", totals["queued_jobs"], totals["running_jobs"], "", "", f"repos={totals['repos_active']}"]
        )
    console.print(f"[green]Wrote {path}[/]")


def render_table(title: str, rows: list[dict], top: int) -> None:
    """Print one ordering of the snapshot as a rich table."""
    table = Table(title=title, title_justify="left")
    table.add_column("Repository")
    table.add_column("Queued", justify="right")
    table.add_column("Running", justify="right")
    table.add_column("Workflows")
    for row in rows[:top]:
        workflows = ", ".join(sorted(row["workflows"])) or "-"
        table.add_row(
            row["repo"],
            str(row["queued_jobs"]),
            str(row["running_jobs"]),
            workflows[:60],
        )
    console.print(table)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report repos with GitHub Actions jobs queued or running right now.",
        epilog=(
            "Caveats: --prs samples the most recently updated open PRs rather than every "
            "one, so a very busy repo is under-counted; and GraphQL exposes no runner "
            "identity, so a job held by a concurrency group looks like one waiting for "
            "capacity. Use the REST runner endpoints (admin:org) for true runner state."
        ),
    )
    parser.add_argument("--org", default="apache", help="organisation to sweep (default: apache)")
    parser.add_argument("--batch-size", type=int, default=20, help="repos per GraphQL query")
    parser.add_argument("--prs", type=int, default=3, help="open PRs sampled per repo")
    parser.add_argument("--suites", type=int, default=5, help="check suites read per commit")
    # Three is about the most this survives: GitHub enforces a per-minute points cap as
    # well as the hourly one, and six workers tripped it partway through an org sweep.
    parser.add_argument("--workers", type=int, default=3, help="batched queries in flight")
    parser.add_argument("--top", type=int, default=25, help="rows shown per table")
    parser.add_argument("--include-archived", action="store_true", help="include archived repos")
    parser.add_argument("--repos-file", help="skip discovery; newline-separated repo names")
    parser.add_argument("--save-repos", help="write the discovered repo list here")
    parser.add_argument("--csv", metavar="PATH", help="write both orderings as CSV next to PATH")
    parser.add_argument("--json", action="store_true", help="print JSON instead of tables")
    parser.add_argument("--github-token", help="GitHub token (default: GH_TOKEN / GITHUB_TOKEN)")
    parser.add_argument("--no-gh", action="store_true", help="use requests instead of the gh CLI")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = GraphQLClient(token=resolve_token(args), use_requests=args.no_gh)

    if args.repos_file:
        with open(args.repos_file) as handle:
            repos = [line.strip() for line in handle if line.strip()]
        console.print(f"[cyan]Loaded {len(repos)} repos from {args.repos_file}[/]")
    else:
        repos = discover_repos(client, args.org, args.include_archived)
        if args.save_repos:
            with open(args.save_repos, "w") as handle:
                handle.write("\n".join(repos) + "\n")
            console.print(f"[green]Wrote {args.save_repos}[/]")

    batches = [repos[index : index + args.batch_size] for index in range(0, len(repos), args.batch_size)]
    console.print(f"[cyan]Status: {len(batches)} queries of up to {args.batch_size} repos[/]")

    results: list[dict] = []
    truncated = False
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(run_batch, client, args.org, batch, args.prs, args.suites) for batch in batches
        ]
        for done, future in enumerate(futures, start=1):
            try:
                results.extend(future.result())
            except BudgetExhausted as exhausted:
                truncated = True
                for pending in futures:
                    pending.cancel()
                console.print(f"[yellow]{exhausted} — reporting {done - 1} completed batches[/]")
                break
            if done % 10 == 0:
                console.print(f"[dim]  {done}/{len(batches)} batches (points left {client.remaining})[/]")

    active = [row for row in results if row["queued_jobs"] or row["running_jobs"]]
    by_running = sorted(active, key=lambda row: (-row["running_jobs"], -row["queued_jobs"], row["repo"]))
    by_queued = sorted(active, key=lambda row: (-row["queued_jobs"], -row["running_jobs"], row["repo"]))
    totals = {
        "org": args.org,
        "repos_with_actions": len(results),
        "repos_active": len(active),
        "queued_jobs": sum(row["queued_jobs"] for row in results),
        "running_jobs": sum(row["running_jobs"] for row in results),
        "truncated": truncated,
    }

    if args.json:
        print(json.dumps({"totals": totals, "by_running": by_running, "by_queued": by_queued}, indent=2))
        return 0

    console.print(
        f"\n[bold]{args.org}[/]: {totals['running_jobs']} jobs running, "
        f"{totals['queued_jobs']} queued across {totals['repos_active']} of "
        f"{totals['repos_with_actions']} repos with Actions"
    )
    if truncated:
        console.print("[yellow]Counts are partial: the run stopped on the points budget.[/]")
    render_table("Sorted by RUNNING jobs", by_running, args.top)
    render_table("Sorted by QUEUED jobs", by_queued, args.top)

    if args.csv:
        write_csv(csv_path(args.csv, "by-running"), by_running, totals)
        write_csv(csv_path(args.csv, "by-queued"), by_queued, totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
