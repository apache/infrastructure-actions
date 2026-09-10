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
   repos) and count check runs, which map one-to-one onto workflow jobs. A repo with
   more open PRs than the sample size cannot be covered that way, so those — and only
   those — are re-counted exactly over REST.

Progress and diagnostics go to stderr — a live progress bar per phase on a terminal, one
line per step when the output is piped — so stdout stays clean for `--json`.

Usage:
    uv run utils/actions-queue-status.py
    uv run utils/actions-queue-status.py --csv /tmp/asf-ci.csv
    uv run utils/actions-queue-status.py --repos-file utils/apache-actions-repos.txt
    uv run utils/actions-queue-status.py --save-repos utils/apache-actions-repos.txt
    uv run utils/actions-queue-status.py --verbose
"""

import argparse
import collections
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

import requests
from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_REST_URL = "https://api.github.com"

# Run states that still hold — or are still waiting for — a runner. "waiting" and
# "action_required" are approval gates rather than capacity waits, and are counted
# separately so the two are not confused.
ACTIVE_RUN_STATES = {"queued", "in_progress", "waiting", "pending", "requested", "action_required"}
APPROVAL_RUN_STATES = {"waiting", "action_required", "requested", "pending"}

# GitHub answers a query it considers too expensive with a bare 502 rather than a typed
# error, and enforces the points budget per minute as well as per hour — so a burst
# trips it even when the hourly balance looks healthy. Both are worth waiting out.
RETRYABLE = ("502", "503", "504", "rate limit", "RATE_LIMIT", "secondary rate", "timeout")

# Points held back so a sweep never leaves the caller's hourly budget at zero.
BUDGET_FLOOR = 200

# Above this the points balance is shown green, between this and BUDGET_FLOOR yellow, and
# below the floor red. Cosmetic, but an org sweep runs long enough that a glanceable
# signal beats reading the number.
POINTS_HEALTHY = 2000

# Repos per discovery page. Each node costs a tree lookup and an open-PR count, and at
# 100 the query times out server-side often enough to end a sweep: two consecutive full
# runs died on HTTP 502 -- after 200 and 300 repos -- with all four retries exhausted.
# The same paging at 50 walked the whole org without a single retry.
DISCOVERY_PAGE_SIZE = 50

REPO_PAGE_QUERY = """
query($org: String!, $after: String) {
  organization(login: $org) {
    repositories(first: %(page)d, after: $after, orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        isArchived
        isDisabled
        defaultBranchRef { name }
        pullRequests(states: OPEN) { totalCount }
        workflows: object(expression: "HEAD:.github/workflows") {
          ... on Tree { entries { name } }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
""" % {"page": DISCOVERY_PAGE_SIZE}

STATUS_FRAGMENTS = """
fragment CI on Repository {
  nameWithOwner
  defaultBranchRef { target { ... on Commit { ...Suites } } }
  pullRequests(states: OPEN, first: %(prs)d, orderBy: {field: UPDATED_AT, direction: DESC}) {
    totalCount
    nodes { commits(last: 1) { nodes { commit { ...Suites } } } }
  }
}
fragment Suites on Commit {
  checkSuites(first: %(suites)d) {
    totalCount
    nodes {
      status
      workflowRun { workflow { name } }
      queued:  checkRuns(first: 1, filterBy: {status: QUEUED})      { totalCount }
      running: checkRuns(first: 1, filterBy: {status: IN_PROGRESS}) { totalCount }
    }
  }
}
"""

CSV_HEADER = [
    "repo",
    "queued_jobs",
    "running_jobs",
    "open_prs",
    "runs_awaiting_approval",
    "source",
    "workflows",
]

PMC_CSV_HEADER = [
    "pmc",
    "queued_jobs",
    "running_jobs",
    "repos",
    "repos_total",
    "open_prs",
    "runs_awaiting_approval",
    "source",
    "repositories",
]


class Tracker:
    """Progress handle for one phase of the sweep.

    Backed by a live `rich` bar when the reporter draws them, and by a line every
    `log_every` steps when it does not — so a redirected run still shows movement without
    a screenful of escape sequences.
    """

    def __init__(
        self,
        reporter: "Reporter",
        progress: Progress | None,
        task_id: int | None,
        description: str,
        total: int | None,
        log_every: int,
    ):
        self.reporter = reporter
        self.progress = progress
        self.task_id = task_id
        self.description = description
        self.total = total
        self.log_every = max(1, log_every)
        self.completed = 0
        self._lock = threading.Lock()

    def advance(self, step: int = 1, detail: str = "") -> None:
        """Move the bar on by `step`, annotating it with `detail` (points left, counts)."""
        with self._lock:
            self.completed += step
            completed = self.completed
        if self.progress is not None:
            self.progress.update(self.task_id, advance=step, detail=detail)
            return
        if completed % self.log_every and completed != self.total:
            return
        of_total = f"/{self.total}" if self.total else ""
        self.reporter.log(f"  {self.description} {completed}{of_total}  {detail}", "dim")


class Reporter:
    """Single owner of everything the sweep prints: colour, progress bars and counters.

    Bars are drawn only when stderr is a terminal and the run is neither quiet nor
    `--no-color`; everything else degrades to plain lines. All output goes to stderr, so
    `--json` on stdout stays machine-readable whatever the verbosity.
    """

    def __init__(self, console: Console, verbose: bool = False, quiet: bool = False, bars: bool = True):
        self.console = console
        # --quiet wins over --verbose: asking for silence and detail at once means silence.
        self.verbose = verbose and not quiet
        self.quiet = quiet
        self.use_bars = bars and console.is_terminal and not quiet
        self.counters: collections.Counter = collections.Counter()
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._points = lambda: None
        self._phase_index = 0
        self._phases_planned = 0

    def track_points(self, client: "GraphQLClient") -> None:
        """Let the reporter read the live points balance for its progress annotations."""
        self._points = lambda: client.remaining

    def plan_phases(self, phases: int) -> None:
        """Record how many phases this run intends, so banners can read `Phase 2/3`."""
        self._phases_planned = phases

    def count(self, name: str, amount: int = 1) -> None:
        """Bump a run counter from any worker thread."""
        with self._lock:
            self.counters[name] += amount

    def log(self, message: str, style: str = "") -> None:
        """Print a normal diagnostic line, unless the run is quiet."""
        if not self.quiet:
            self.console.print(message, style=style or None, highlight=False)

    def detail(self, message: str) -> None:
        """Print a per-batch / per-retry / per-repo line — only under --verbose."""
        if self.verbose:
            self.console.print(f"    {message}", style="dim", highlight=False)

    def warn(self, message: str) -> None:
        if not self.quiet:
            self.console.print(f"  ! {message}", style="yellow", highlight=False)

    def error(self, message: str) -> None:
        """Print a failure. Loud enough to survive even --quiet, since the run is ending."""
        self.console.print(f"  x {message}", style="bold red", highlight=False)

    def success(self, message: str) -> None:
        self.log(f"  {message}", "green")

    def points_markup(self) -> str:
        """Render the points balance as a traffic light: green plenty, red nearly spent."""
        remaining = self._points()
        if remaining is None:
            return "[dim]? pts[/]"
        if remaining > POINTS_HEALTHY:
            style = "green"
        elif remaining > BUDGET_FLOOR:
            style = "yellow"
        else:
            style = "bold red"
        return f"[{style}]{remaining} pts[/]"

    def _build_progress(self) -> Progress:
        return Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]{task.description}[/]"),
            BarColumn(complete_style="cyan", finished_style="green"),
            MofNCompleteColumn(),
            TaskProgressColumn(show_speed=True),
            TextColumn("{task.fields[detail]}"),
            TimeElapsedColumn(),
            TextColumn("[dim]eta[/]"),
            TimeRemainingColumn(),
            console=self.console,
        )

    @contextmanager
    def phase(self, title: str, subtitle: str = "") -> Iterator[None]:
        """Frame one phase: a banner going in, elapsed time and points spent coming out."""
        self._phase_index += 1
        label = f"Phase {self._phase_index}/{self._phases_planned}" if self._phases_planned else "Phase"
        started = time.monotonic()
        before = self._points()
        if not self.quiet:
            banner = f"\n[bold cyan]▸ {label}  {title}[/]"
            self.console.print(banner + (f"  [dim]{subtitle}[/]" if subtitle else ""), highlight=False)
        yield
        after = self._points()
        spent = f", {before - after} points" if before is not None and after is not None else ""
        self.success(f"{title} finished in {time.monotonic() - started:,.1f}s{spent}")

    @contextmanager
    def tracker(self, description: str, total: int | None, log_every: int = 1) -> Iterator[Tracker]:
        """Yield a progress handle for one phase — a live bar, or periodic lines."""
        if not self.use_bars:
            yield Tracker(self, None, None, description, total, log_every)
            return
        progress = self._build_progress()
        with progress:
            task_id = progress.add_task(description, total=total, detail="")
            yield Tracker(self, progress, task_id, description, total, log_every)

    def summary(self, client: "GraphQLClient") -> None:
        """Print the end-of-run accounting: work done, what went wrong, points spent."""
        if self.quiet:
            return
        counters = self.counters
        rows = [
            ("GraphQL queries", counters["graphql_queries"], "cyan"),
            ("GraphQL retries", counters["graphql_retries"], "yellow"),
            ("GraphQL give-ups", counters["graphql_failures"], "red"),
            ("Batches split", counters["batch_splits"], "yellow"),
            ("Repos skipped", counters["repos_skipped"], "yellow"),
            ("REST requests", counters["rest_requests"], "cyan"),
            ("REST retries", counters["rest_retries"], "yellow"),
            ("REST give-ups", counters["rest_failures"], "red"),
            ("Repos re-counted over REST", counters["repos_recounted"], "cyan"),
        ]
        table = Table(
            title="Run diagnostics",
            title_justify="left",
            title_style="bold cyan",
            box=None,
            show_header=False,
            pad_edge=False,
        )
        table.add_column(style="dim")
        table.add_column(justify="right")
        for label, value, style in rows:
            # A zero for a failure counter is the good outcome, so leave it dim rather
            # than painting it with the warning colour the non-zero case earns.
            table.add_row(label, f"[{style if value else 'dim'}]{value}[/]")
        spent = client.points_spent
        table.add_row("GraphQL points spent", f"[cyan]{spent}[/]" if spent is not None else "[dim]?[/]")
        table.add_row("GraphQL points left", self.points_markup())
        table.add_row("Wall time", f"[cyan]{time.monotonic() - self.started:,.1f}s[/]")
        self.console.print(table)


class BudgetExhausted(RuntimeError):
    """Raised when the GraphQL points budget runs too low to keep querying safely."""


class GraphQLClient:
    """Minimal GraphQL client over the `gh` CLI, or `requests` when `--no-gh` is given.

    Tracks the points balance reported by each query. The in-query `rateLimit` block is
    the only trustworthy source: the REST `/rate_limit` endpoint keeps reporting a full
    GraphQL budget while the API is actively rejecting queries as rate limited.
    """

    def __init__(
        self,
        token: str | None = None,
        use_requests: bool = False,
        reporter: Reporter | None = None,
    ):
        self.token = token
        self.use_requests = use_requests
        self.reporter = reporter
        self._lock = threading.Lock()
        self._remaining: int | None = None
        self._first_remaining: int | None = None
        if use_requests and not token:
            raise SystemExit("--no-gh requires --github-token, GH_TOKEN or GITHUB_TOKEN")
        if not use_requests and not shutil.which("gh"):
            raise SystemExit("gh CLI not found — install it, or use --no-gh with a token")

    @property
    def remaining(self) -> int | None:
        """Return the points balance reported by the most recent successful query."""
        with self._lock:
            return self._remaining

    @property
    def points_spent(self) -> int | None:
        """Points burned since the first query that reported a balance. None if unknown."""
        with self._lock:
            if self._first_remaining is None or self._remaining is None:
                return None
            return self._first_remaining - self._remaining

    def _note_budget(self, payload: dict) -> None:
        limit = (payload.get("data") or {}).get("rateLimit")
        if limit:
            with self._lock:
                self._remaining = limit["remaining"]
                if self._first_remaining is None:
                    self._first_remaining = limit["remaining"]

    def _count(self, name: str, amount: int = 1) -> None:
        if self.reporter:
            self.reporter.count(name, amount)

    def _detail(self, message: str) -> None:
        if self.reporter:
            self.reporter.detail(message)

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
            self._count("graphql_queries")
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
                self._detail(f"[yellow]query failed, not retryable:[/] {escape(error)}")
                break
            if attempt == attempts - 1:
                # Nothing follows the last attempt, so sleeping its backoff would only
                # delay the give-up by minutes.
                break
            # 4s, 16s, 64s. The per-minute points cap needs real time to drain; a tight
            # retry only burns more of the budget it is waiting on.
            delay = 4 ** (attempt + 1)
            self._count("graphql_retries")
            self._detail(
                f"[yellow]query attempt {attempt + 1}/{attempts} failed, retrying in "
                f"{delay}s:[/] {escape(error)}"
            )
            time.sleep(delay)
        self._count("graphql_failures")
        return {"__error__": error}

    def rest(self, path: str, attempts: int = 3) -> dict | None:
        """GET a REST endpoint, retrying transient failures. None when it cannot be read."""
        for attempt in range(attempts):
            self._count("rest_requests")
            if self.use_requests:
                response = requests.get(
                    f"{GITHUB_REST_URL}/{path}",
                    headers={"Authorization": f"bearer {self.token}", "Accept": "application/json"},
                    timeout=60,
                )
                if response.status_code == 200:
                    return response.json()
                error = f"HTTP {response.status_code}"
            else:
                result = subprocess.run(
                    ["gh", "api", "--method", "GET", path], capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    try:
                        return json.loads(result.stdout)
                    except json.JSONDecodeError:
                        self._count("rest_failures")
                        self._detail(f"[yellow]REST {escape(path)} returned unparseable JSON[/]")
                        return None
                error = (result.stderr or "")[:150]
            if not any(marker in error for marker in RETRYABLE):
                self._count("rest_failures")
                self._detail(f"[yellow]REST {escape(path)} failed:[/] {escape(error)}")
                return None
            if attempt == attempts - 1:
                break  # As above: no attempt follows, so the backoff would buy nothing.
            delay = 5 * (attempt + 1)
            self._count("rest_retries")
            self._detail(
                f"[yellow]REST {escape(path)} attempt {attempt + 1}/{attempts} failed, "
                f"retrying in {delay}s:[/] {escape(error)}"
            )
            time.sleep(delay)
        self._count("rest_failures")
        return None

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


def report_pr_distribution(reporter: Reporter, open_pr_counts: list[int]) -> None:
    """Show how many repos each candidate --prs value would cover outright.

    The knee of this curve is what --prs should be set to: below it, GraphQL cannot cover
    the repo and REST re-counts it anyway; above it, the sweep pays node cost for PRs that
    almost no repository has.
    """
    if not open_pr_counts or reporter.quiet:
        return
    total = len(open_pr_counts)
    reporter.log("  Open-PR distribution (repos GraphQL could cover outright):", "cyan")
    table = Table(box=None, pad_edge=False)
    table.add_column("--prs", style="dim", justify="right")
    table.add_column("covered", justify="right")
    table.add_column("share", justify="right")
    for threshold in (1, 3, 5, 10, 25, 50):
        covered = sum(1 for count in open_pr_counts if count <= threshold)
        share = covered / total
        # Green once a setting covers most of the org, so the knee of the curve stands out.
        style = "green" if share >= 0.9 else "yellow" if share >= 0.5 else "dim"
        table.add_row(str(threshold), f"{covered} / {total}", f"[{style}]{share:.0%}[/]")
    reporter.console.print(Padding(table, (0, 0, 0, 2), expand=False))


def discover_repos(client: GraphQLClient, reporter: Reporter, org: str, include_archived: bool) -> list[str]:
    """Return every non-archived repo in the org that defines workflow files."""
    repos: list[str] = []
    open_pr_counts: list[int] = []
    scanned = 0
    skipped = 0
    cursor = None
    page_number = 0
    with reporter.tracker("Discovery", total=None) as tracker:
        while True:
            variables = {"org": org}
            if cursor:
                variables["after"] = cursor
            payload = client.query(REPO_PAGE_QUERY, variables)
            if "__error__" in payload:
                # Partial discovery silently under-reports the org, which is worse than no
                # answer at all — fail loudly and say how far the paging got.
                reporter.error(f"discovery failed after {scanned} repos (cursor {cursor})")
                raise SystemExit(
                    f"discovery failed after {scanned} repos (cursor {cursor}): {payload['__error__']}"
                )
            page = payload["data"]["organization"]["repositories"]
            page_number += 1
            for node in page["nodes"]:
                scanned += 1
                if node["isDisabled"] or (node["isArchived"] and not include_archived):
                    skipped += 1
                    continue
                if not node.get("defaultBranchRef"):
                    skipped += 1
                    continue
                if has_workflows(node):
                    repos.append(node["name"])
                    open_pr_counts.append((node.get("pullRequests") or {}).get("totalCount", 0))
            tracker.advance(
                len(page["nodes"]),
                detail=f"[cyan]{len(repos)}[/] with workflows · {reporter.points_markup()}",
            )
            reporter.detail(f"page {page_number}: {len(page['nodes'])} repos, cursor {cursor or 'start'}")
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
    reporter.log(
        f"  {scanned} repos scanned, [bold]{len(repos)}[/] use GitHub Actions "
        f"([dim]{skipped} archived, disabled or empty[/])",
        "cyan",
    )
    report_pr_distribution(reporter, open_pr_counts)
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
    max_suites_on_a_commit = 0
    workflows: dict[str, int] = {}
    for commit in collect_commits(repo_node):
        suite_page = commit.get("checkSuites") or {}
        max_suites_on_a_commit = max(max_suites_on_a_commit, suite_page.get("totalCount", 0))
        for suite in suite_page.get("nodes", []):
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
        "open_prs": (repo_node.get("pullRequests") or {}).get("totalCount", 0),
        "max_suites_on_a_commit": max_suites_on_a_commit,
        "source": "graphql",
        "workflows": workflows,
    }


def run_batch(
    client: GraphQLClient, reporter: Reporter, org: str, names: list[str], prs: int, suites: int
) -> list[dict]:
    """Query one batch, halving it on failure so one heavy repo cannot sink the rest.

    Halving is why the budget has to be checked here and not only by the caller: a batch
    that splits all the way down issues far more queries than the plan accounted for.
    """
    client.check_budget()
    payload = client.query(build_status_query(org, names, prs, suites))
    if "__error__" in payload or not payload.get("data"):
        error = escape(str(payload.get("__error__")))
        if len(names) == 1:
            reporter.count("repos_skipped")
            reporter.warn(f"skipped [bold]{names[0]}[/]: {error}")
            return []
        middle = len(names) // 2
        reporter.count("batch_splits")
        reporter.detail(f"[yellow]batch of {len(names)} failed, splitting in two:[/] {error}")
        return run_batch(client, reporter, org, names[:middle], prs, suites) + run_batch(
            client, reporter, org, names[middle:], prs, suites
        )
    return [
        summarize_repo(node) for key, node in payload["data"].items() if node and key != "rateLimit"
    ]


def recount_over_rest(client: GraphQLClient, reporter: Reporter, org: str, row: dict) -> dict:
    """Re-count one repo exactly over REST, for repos the PR window cannot cover.

    GraphQL caps `pullRequests(first:)` at 100 and the sweep samples far fewer, so a repo
    with more open PRs than the sample size is necessarily under-counted. REST has no
    org-wide equivalent, but per repo it is exact: list the runs that are still active and
    count their jobs.
    """
    name = row["repo"].split("/", 1)[1]
    runs = client.rest(f"repos/{org}/{name}/actions/runs?per_page=100")
    if not runs:
        reporter.warn(f"REST re-count of [bold]{escape(name)}[/] failed — keeping the GraphQL sample")
        return row  # Keep the GraphQL sample rather than reporting a repo as idle.
    active = [run for run in runs.get("workflow_runs", []) if run.get("status") in ACTIVE_RUN_STATES]
    queued = running = awaiting_approval = 0
    workflows: dict[str, int] = {}
    for run in active:
        if run["status"] in APPROVAL_RUN_STATES:
            awaiting_approval += 1
        jobs = client.rest(f"repos/{org}/{name}/actions/runs/{run['id']}/jobs?per_page=100&filter=latest")
        if not jobs:
            continue
        for job in jobs.get("jobs", []):
            if job["status"] not in {"queued", "in_progress"}:
                continue
            if job["status"] == "in_progress":
                running += 1
            else:
                queued += 1
            name_of_run = run.get("name") or "?"
            workflows[name_of_run] = workflows.get(name_of_run, 0) + 1
    reporter.count("repos_recounted")
    reporter.detail(
        f"{escape(name)}: {len(active)} active runs → [cyan]{running}[/] running, "
        f"[cyan]{queued}[/] queued"
        + (f", [yellow]{awaiting_approval}[/] awaiting approval" if awaiting_approval else "")
        + f" (GraphQL sampled {row['running_jobs']} running, {row['queued_jobs']} queued)"
    )
    return {
        **row,
        "queued_jobs": queued,
        "running_jobs": running,
        "runs_awaiting_approval": awaiting_approval,
        "source": "rest",
        "workflows": workflows,
    }


def pmc_of(repo: str) -> str:
    """Return the PMC a repository name belongs to: the text before the first hyphen.

    The same rule actions-audit.py's --pmc filter uses, so the two scripts agree on what
    `spark` covers. It is a naming convention rather than authoritative ownership -- an
    `incubator-` repo groups under `incubator`, not under the podling's eventual PMC --
    but every ASF repo is named this way, and it needs no network call to apply.
    """
    return repo.split("/")[-1].split("-", 1)[0]


def group_by_pmc(rows: list[dict], population: list[str] | None = None) -> list[dict]:
    """Aggregate per-repo summaries into one row per PMC.

    `population` is every repo the sweep covered, active or not. It is what makes the
    active count mean something: four busy repos is a different picture for a PMC of
    four than for a PMC of forty-seven. Without it a PMC's total is just its active
    repos, which is what a caller with no wider list can honestly say.
    """
    totals: dict[str, int] = collections.Counter(pmc_of(name) for name in population or [])
    groups: dict[str, dict] = {}
    for row in rows:
        name = row["repo"].split("/")[-1]
        group = groups.setdefault(
            pmc_of(name),
            {
                "pmc": pmc_of(name),
                "queued_jobs": 0,
                "running_jobs": 0,
                "open_prs": 0,
                "runs_awaiting_approval": 0,
                "repos": [],
                "sources": set(),
            },
        )
        group["queued_jobs"] += row["queued_jobs"]
        group["running_jobs"] += row["running_jobs"]
        group["open_prs"] += row.get("open_prs", 0) or 0
        group["runs_awaiting_approval"] += row.get("runs_awaiting_approval", 0) or 0
        group["repos"].append(name)
        group["sources"].add(row.get("source", "graphql"))
    for group in groups.values():
        group["repos"].sort()
        # A repo with jobs is by definition part of its PMC's estate, so the count can
        # never be smaller than what was found active -- even if the population somehow
        # did not list it.
        group["repos_total"] = max(totals.get(group["pmc"], 0), len(group["repos"]))
        # A PMC counted partly each way is neither: say so rather than pick a winner.
        group["source"] = group["sources"].pop() if len(group["sources"]) == 1 else "mixed"
        del group["sources"]
    return list(groups.values())


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
        row.get("open_prs", 0),
        row.get("runs_awaiting_approval", ""),
        row.get("source", "graphql"),
        workflows,
    ]


def pmc_csv_row(row: dict) -> list:
    """Flatten one PMC summary into CSV cells."""
    return [
        row["pmc"],
        row["queued_jobs"],
        row["running_jobs"],
        len(row["repos"]),
        row["repos_total"],
        row["open_prs"],
        row["runs_awaiting_approval"],
        row["source"],
        "; ".join(row["repos"]),
    ]


ASF_HEADER = """\
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
"""


def write_repos_file(path: str, org: str, repos: list[str]) -> None:
    """Write a discovered repo list, with the header that lets it be committed.

    The list is worth keeping under version control -- discovery is the slowest and
    most rate-limit-hungry phase of a sweep -- so the file carries the ASF header RAT
    expects and records when it was generated, since it goes stale as repos appear,
    are archived, or adopt Actions.

    Names are sorted rather than left in discovery's push order, which reshuffles on
    every run: a refresh should diff as the repos that joined and left, nothing else.
    """
    with open(path, "w") as handle:
        handle.write(ASF_HEADER)
        handle.write(
            f"#\n"
            f"# Repositories in the {org} organisation that define GitHub Actions workflows.\n"
            f"# {len(repos)} repos, discovered {time.strftime('%Y-%m-%d', time.gmtime())} (UTC).\n"
            f"#\n"
            f"# Generated by:  uv run utils/actions-queue-status.py --save-repos <path>\n"
            f"# Pass it back with --repos-file to skip discovery. Refresh it periodically --\n"
            f"# a stale list silently omits repos that have since adopted Actions.\n"
            f"#\n"
        )
        handle.write("\n".join(sorted(repos)) + "\n")


def write_csv(reporter: Reporter, path: str, rows: list[dict], totals: dict, by_pmc: bool = False) -> None:
    """Write one ordering of the snapshot as CSV."""
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(PMC_CSV_HEADER if by_pmc else CSV_HEADER)
        for row in rows:
            writer.writerow(pmc_csv_row(row) if by_pmc else csv_row(row))
        total_row = ["TOTAL", totals["queued_jobs"], totals["running_jobs"]]
        if by_pmc:
            total_row += [
                totals["repos_active"],
                totals["repos_with_actions"],
                "",
                "",
                "",
                f"pmcs={totals['pmcs_active']}",
            ]
        else:
            total_row += ["", "", "", f"repos={totals['repos_active']}"]
        writer.writerow(total_row)
    reporter.success(f"Wrote {path} ({len(rows)} rows)")


def render_table(
    reporter: Reporter, title: str, rows: list[dict], top: int, totals: dict, by_pmc: bool = False
) -> None:
    """Print one ordering of the snapshot as a rich table."""
    table = Table(title=title, title_justify="left", title_style="bold")
    table.add_column("PMC" if by_pmc else "Repository", style="bold")
    table.add_column("Queued", justify="right", style="yellow")
    table.add_column("Running", justify="right", style="green")
    if by_pmc:
        # Which API counted a row is a per-repo fact that a PMC of several repos can only
        # blur, so the grouped table spends the column on the repo count instead. The CSV
        # still carries it, as "mixed" where the repos disagree.
        table.add_column("Repos", justify="right")
        table.add_column("Repositories", style="dim")
    else:
        # The source column says how the row was counted: an exact REST re-count reads
        # differently from a GraphQL sample, so it is worth colouring the two apart.
        table.add_column("Source")
        table.add_column("Workflows", style="dim")
    for row in rows[:top]:
        if by_pmc:
            table.add_row(
                row["pmc"],
                str(row["queued_jobs"]),
                str(row["running_jobs"]),
                f"{len(row['repos'])} / {row['repos_total']}",
                escape(", ".join(row["repos"])[:50]),
            )
            continue
        workflows = ", ".join(sorted(row["workflows"])) or "-"
        source = row.get("source", "graphql")
        table.add_row(
            row["repo"],
            str(row["queued_jobs"]),
            str(row["running_jobs"]),
            f"[cyan]{source}[/]" if source == "rest" else f"[dim]{source}[/]",
            escape(workflows[:50]),
        )
    # The org-wide total is printed once above the tables, which has scrolled away by the
    # time a long table has been read — so each table closes with it. When the table is
    # truncated the visible rows are subtotalled too, since what --top leaves out is the
    # question the total otherwise raises.
    table.add_section()
    if len(rows) > top:
        table.add_row(
            f"[dim]shown (top {top})[/]",
            f"[dim]{sum(row['queued_jobs'] for row in rows[:top])}[/]",
            f"[dim]{sum(row['running_jobs'] for row in rows[:top])}[/]",
            "",
            "",
        )
    active = totals["pmcs_active"] if by_pmc else totals["repos_active"]
    unit = "PMC" if by_pmc else "repo"
    table.add_row(
        "[bold]TOTAL[/]",
        f"[bold]{totals['queued_jobs']}[/]",
        f"[bold]{totals['running_jobs']}[/]",
        f"[bold]{totals['repos_active']} / {totals['repos_with_actions']}[/]" if by_pmc else "",
        f"[dim]{active} {unit}{'' if active == 1 else 's'}[/]",
    )
    if len(rows) > top:
        table.caption = (
            f"showing top {top} of {len(rows)} active {unit}s — raise --top for more"
        )
        table.caption_justify = "left"
    reporter.console.print(table)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report repos with GitHub Actions jobs queued or running right now.",
        epilog=(
            "Repos the GraphQL sample cannot cover — more open PRs than --prs, or more "
            "check suites on a commit than --suites — are re-counted exactly over REST, so "
            "the totals are not a sample. Remaining caveat: neither API attributes a job to a "
            "runner, so a job held by a concurrency group cannot be told apart from one "
            "waiting for capacity — runs blocked on approval are reported separately in "
            "runs_awaiting_approval. Use the REST runner endpoints (admin:org) for true "
            "runner state."
        ),
    )
    parser.add_argument("--org", default="apache", help="organisation to sweep (default: apache)")
    parser.add_argument("--batch-size", type=int, default=20, help="repos per GraphQL query")
    # GraphQL's node cost climbs sharply with this: measured over the ~1250 apache repos
    # with workflows, a sweep costs ~250 points at 3, ~2700 at 5 and ~21000 at 10, against
    # a 5000/hour budget. Raising it only buys coverage of quiet repos, and those cost one
    # cheap REST call each — so the low value is both cheaper and no less accurate.
    parser.add_argument("--prs", type=int, default=3, help="open PRs sampled per repo")
    parser.add_argument("--suites", type=int, default=5, help="check suites read per commit")
    # Three is about the most this survives: GitHub enforces a per-minute points cap as
    # well as the hourly one, and six workers tripped it partway through an org sweep.
    parser.add_argument("--workers", type=int, default=3, help="batched queries in flight")
    parser.add_argument("--top", type=int, default=25, help="rows shown per table")
    parser.add_argument(
        "--by-pmc",
        action="store_true",
        help="group rows by PMC: the repo name's prefix before the first hyphen",
    )
    parser.add_argument("--include-archived", action="store_true", help="include archived repos")
    parser.add_argument("--repos-file", help="skip discovery; newline-separated repo names")
    parser.add_argument("--save-repos", help="write the discovered repo list here")
    parser.add_argument("--csv", metavar="PATH", help="write both orderings as CSV next to PATH")
    parser.add_argument("--json", action="store_true", help="print JSON instead of tables")
    parser.add_argument("--github-token", help="GitHub token (default: GH_TOKEN / GITHUB_TOKEN)")
    parser.add_argument("--no-gh", action="store_true", help="use requests instead of the gh CLI")
    parser.add_argument(
        "--no-rest-fallback",
        action="store_true",
        help="skip the exact REST re-count for repos the GraphQL sample could not cover",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="add per-batch, per-retry and per-repo diagnostics to the progress output",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress progress and diagnostics; print only the result (overrides --verbose)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable colour and progress bars (NO_COLOR in the environment does the same)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # rich drops colour on NO_COLOR by itself, but not the bars — and a bar redrawing in
    # monochrome is exactly what someone setting NO_COLOR is trying to avoid.
    plain = args.no_color or bool(os.environ.get("NO_COLOR"))
    console = Console(stderr=True, no_color=plain)
    reporter = Reporter(console, verbose=args.verbose, quiet=args.quiet, bars=not plain)
    # Discovery is skipped when a repo list is supplied, and the REST pass only runs when
    # the GraphQL sample turned out partial — so the phase count reflects this run's plan.
    reporter.plan_phases((0 if args.repos_file else 1) + 1 + (0 if args.no_rest_fallback else 1))
    client = GraphQLClient(token=resolve_token(args), use_requests=args.no_gh, reporter=reporter)
    reporter.track_points(client)
    reporter.log(
        f"Sweeping [bold]{args.org}[/] via {'requests' if args.no_gh else 'the gh CLI'} "
        f"([dim]--prs {args.prs} --suites {args.suites} --batch-size {args.batch_size} "
        f"--workers {args.workers}[/])",
        "cyan",
    )

    if args.repos_file:
        with open(args.repos_file) as handle:
            repos = [
                line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")
            ]
        reporter.log(f"Loaded [bold]{len(repos)}[/] repos from {escape(args.repos_file)}", "cyan")
    else:
        with reporter.phase("Discovery", f"org {args.org}, reading .github/workflows per repo"):
            repos = discover_repos(client, reporter, args.org, args.include_archived)
        if args.save_repos:
            write_repos_file(args.save_repos, args.org, repos)
            reporter.success(f"Wrote {args.save_repos} ({len(repos)} repos)")

    batches = [repos[index : index + args.batch_size] for index in range(0, len(repos), args.batch_size)]

    results: list[dict] = []
    truncated = False
    with reporter.phase("Status", f"{len(batches)} queries of up to {args.batch_size} repos"):
        with reporter.tracker("Status", total=len(batches), log_every=10) as tracker:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [
                    pool.submit(run_batch, client, reporter, args.org, batch, args.prs, args.suites)
                    for batch in batches
                ]
                for done, future in enumerate(futures, start=1):
                    try:
                        results.extend(future.result())
                    except BudgetExhausted as exhausted:
                        truncated = True
                        for pending in futures:
                            pending.cancel()
                        reporter.warn(f"{exhausted} — reporting {done - 1} completed batches")
                        break
                    tracker.advance(detail=reporter.points_markup())

    # The GraphQL pass covered a repo exhaustively only if *both* of its sampling limits
    # held: no more open PRs than --prs, and no commit carrying more check suites than
    # --suites. Checking only the first is not enough — a repo with a single open PR can
    # still have a dozen suites on its head commit, and reading five of them under-counts
    # it badly. Everything else is re-counted over REST, which is exact per repo.
    incomplete = [
        row
        for row in results
        if row.get("open_prs", 0) > args.prs or row.get("max_suites_on_a_commit", 0) > args.suites
    ]
    if incomplete and not args.no_rest_fallback:
        subtitle = (
            f"{len(incomplete)} repos with more than {args.prs} open PRs or more than "
            f"{args.suites} check suites on a commit"
        )
        with reporter.phase("REST re-count", subtitle):
            exact_by_repo = {}
            with reporter.tracker("REST", total=len(incomplete), log_every=10) as tracker:

                def recount(item: dict) -> dict:
                    row = recount_over_rest(client, reporter, args.org, item)
                    tracker.advance(detail=f"[bold]{escape(row['repo'])}[/]")
                    return row

                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    for row in pool.map(recount, incomplete):
                        exact_by_repo[row["repo"]] = row
            results = [exact_by_repo.get(row["repo"], row) for row in results]
    elif incomplete:
        reporter.warn(
            f"{len(incomplete)} repos are only sampled (--no-rest-fallback): counts are a lower bound"
        )
    elif not args.no_rest_fallback:
        # Says why the planned REST phase never appeared: the sample covered everything.
        reporter.log("  No REST re-count needed — the GraphQL sample covered every repo", "green")

    active = [row for row in results if row["queued_jobs"] or row["running_jobs"]]
    grouped = group_by_pmc(active, repos)
    if args.by_pmc:
        key = "pmc"
        rows = grouped
    else:
        key = "repo"
        rows = active
    by_running = sorted(rows, key=lambda row: (-row["running_jobs"], -row["queued_jobs"], row[key]))
    by_queued = sorted(rows, key=lambda row: (-row["queued_jobs"], -row["running_jobs"], row[key]))
    totals = {
        "org": args.org,
        "repos_with_actions": len(results),
        "repos_active": len(active),
        "pmcs_active": len(grouped),
        "queued_jobs": sum(row["queued_jobs"] for row in results),
        "running_jobs": sum(row["running_jobs"] for row in results),
        "truncated": truncated,
    }

    if args.json:
        reporter.summary(client)
        print(json.dumps({"totals": totals, "by_running": by_running, "by_queued": by_queued}, indent=2))
        return 0

    reporter.summary(client)
    console.print(
        f"\n[bold]{args.org}[/]: [green]{totals['running_jobs']}[/] jobs running, "
        f"[yellow]{totals['queued_jobs']}[/] queued across {totals['repos_active']} of "
        f"{totals['repos_with_actions']} repos with Actions"
        + (f" in {totals['pmcs_active']} PMCs" if args.by_pmc else ""),
        highlight=False,
    )
    if truncated:
        console.print("[bold yellow]Counts are partial: the run stopped on the points budget.[/]")
    render_table(reporter, "Sorted by RUNNING jobs", by_running, args.top, totals, args.by_pmc)
    render_table(reporter, "Sorted by QUEUED jobs", by_queued, args.top, totals, args.by_pmc)

    if args.csv:
        write_csv(reporter, csv_path(args.csv, "by-running"), by_running, totals, args.by_pmc)
        write_csv(reporter, csv_path(args.csv, "by-queued"), by_queued, totals, args.by_pmc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
