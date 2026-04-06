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
#     "jsbeautifier>=1.15",
#     "requests>=2.31",
#     "rich>=13.0",
# ]
# ///

"""
Verify that compiled JavaScript in a GitHub Action matches a local rebuild.

Checks out the action at a given commit hash inside an isolated Docker container,
rebuilds it, and diffs the published compiled JS against the locally built output.

Usage:
    uv run verify-action-build.py dorny/test-reporter@df6247429542221bc30d46a036ee47af1102c451

Security review checklist:
    https://github.com/apache/infrastructure-actions#security-review-checklist
"""

import argparse
import difflib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

import jsbeautifier
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_is_ci = os.environ.get("CI") is not None
_ci_console_options = {"force_interactive": False, "width": 200} if _is_ci else {}
console = Console(stderr=True, force_terminal=_is_ci, **_ci_console_options)
output = Console(force_terminal=_is_ci, **_ci_console_options)

def link(url: str, text: str) -> str:
    """Return Rich-markup hyperlink, falling back to plain text in CI."""
    if _is_ci:
        return text
    return f"[link={url}]{text}[/link]"


class UserQuit(Exception):
    """Raised when user enters 'q' to quit."""


def ask_confirm(prompt: str, default: bool = True) -> bool:
    """Ask a y/n/q confirmation. Returns True/False, raises UserQuit on 'q'."""
    suffix = " [Y/n/q]" if default else " [y/N/q]"
    try:
        answer = console.input(f"{prompt}{suffix} ").strip().lower()
    except EOFError:
        raise UserQuit
    if answer == "q":
        raise UserQuit
    if not answer:
        return default
    return answer in ("y", "yes")

# Path to the actions.yml file relative to the script
ACTIONS_YML = Path(__file__).resolve().parent.parent / "actions.yml"

GITHUB_API = "https://api.github.com"
SECURITY_CHECKLIST_URL = "https://github.com/apache/infrastructure-actions#security-review-checklist"


def _detect_repo() -> str:
    """Detect the GitHub repo from the git remote origin URL."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=ACTIONS_YML.parent,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        # Handle SSH (git@github.com:org/repo.git) and HTTPS (https://github.com/org/repo.git)
        match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        if match:
            return match.group(1)
    return "apache/infrastructure-actions"


class GitHubClient:
    """Abstraction over GitHub API — uses either gh CLI or requests with a token."""

    def __init__(self, token: str | None = None, repo: str | None = None):
        self.repo = repo or _detect_repo()
        self.token = token
        self._use_requests = token is not None

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _gh_api(self, endpoint: str) -> dict | list | None:
        """Call gh api and return parsed JSON, or None on failure."""
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None

    def _get(self, endpoint: str) -> dict | list | None:
        """GET from GitHub API using requests or gh CLI."""
        if self._use_requests:
            resp = requests.get(f"{GITHUB_API}/{endpoint}", headers=self._headers())
            if resp.ok:
                return resp.json()
            return None
        return self._gh_api(endpoint)

    def get_commit_pulls(self, owner: str, repo: str, commit_sha: str) -> list[dict]:
        """Get PRs associated with a commit."""
        data = self._get(f"repos/{owner}/{repo}/commits/{commit_sha}/pulls")
        return data if isinstance(data, list) else []

    def compare_commits(self, owner: str, repo: str, base: str, head: str) -> list[dict]:
        """Get commits between two refs."""
        data = self._get(f"repos/{owner}/{repo}/compare/{base}...{head}")
        if isinstance(data, dict):
            return data.get("commits", [])
        return []

    def get_pr_diff(self, pr_number: int) -> str | None:
        """Get the diff for a PR."""
        if self._use_requests:
            resp = requests.get(
                f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}",
                headers={**self._headers(), "Accept": "application/vnd.github.v3.diff"},
            )
            return resp.text if resp.ok else None
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True, text=True,
        )
        return result.stdout if result.returncode == 0 else None

    def get_authenticated_user(self) -> str:
        """Get the login of the authenticated user."""
        if self._use_requests:
            resp = requests.get(f"{GITHUB_API}/user", headers=self._headers())
            if resp.ok:
                return resp.json().get("login", "unknown")
            return "unknown"
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    def list_open_prs(self, author: str = "app/dependabot") -> list[dict]:
        """List open PRs by author with status check info."""
        if self._use_requests:
            prs = []
            page = 1
            while True:
                resp = requests.get(
                    f"{GITHUB_API}/repos/{self.repo}/pulls",
                    headers=self._headers(),
                    params={"state": "open", "per_page": 50, "page": page},
                )
                if not resp.ok:
                    break
                batch = resp.json()
                if not batch:
                    break
                for pr in batch:
                    pr_login = pr.get("user", {}).get("login", "")
                    if author.startswith("app/"):
                        expected = author.split("/", 1)[1] + "[bot]"
                        if pr_login != expected:
                            continue
                    elif pr_login != author:
                        continue
                    prs.append({
                        "number": pr["number"],
                        "title": pr["title"],
                        "headRefName": pr["head"]["ref"],
                        "url": pr["html_url"],
                        "reviewDecision": self._get_review_decision(pr["number"]),
                        "statusCheckRollup": self._get_status_checks(pr["head"]["sha"]),
                    })
                page += 1
            return prs
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--author", author,
                "--state", "open",
                "--json", "number,title,headRefName,url,reviewDecision,statusCheckRollup",
                "--limit", "50",
            ],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return []

    def _get_review_decision(self, pr_number: int) -> str | None:
        """Get the review decision for a PR via GraphQL."""
        resp = requests.post(
            f"{GITHUB_API}/graphql",
            headers=self._headers(),
            json={
                "query": """query($owner:String!, $repo:String!, $number:Int!) {
                    repository(owner:$owner, name:$repo) {
                        pullRequest(number:$number) { reviewDecision }
                    }
                }""",
                "variables": {
                    "owner": self.repo.split("/")[0],
                    "repo": self.repo.split("/")[1],
                    "number": pr_number,
                },
            },
        )
        if resp.ok:
            data = resp.json()
            return (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewDecision")
            )
        return None

    def _get_status_checks(self, sha: str) -> list[dict]:
        """Get combined status checks for a commit SHA."""
        data = self._get(f"repos/{self.repo}/commits/{sha}/check-runs")
        if isinstance(data, dict):
            return [
                {
                    "name": cr.get("name"),
                    "conclusion": (cr.get("conclusion") or "").upper(),
                    "status": (cr.get("status") or "").upper(),
                }
                for cr in data.get("check_runs", [])
            ]
        return []

    def approve_pr(self, pr_number: int, comment: str) -> bool:
        """Approve a PR with a review comment."""
        if self._use_requests:
            resp = requests.post(
                f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}/reviews",
                headers=self._headers(),
                json={"body": comment, "event": "APPROVE"},
            )
            return resp.ok
        result = subprocess.run(
            ["gh", "pr", "review", str(pr_number), "--approve", "--body", comment],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def merge_pr(self, pr_number: int) -> tuple[bool, str]:
        """Merge a PR and delete the branch. Returns (success, error_msg)."""
        if self._use_requests:
            resp = requests.put(
                f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}/merge",
                headers=self._headers(),
                json={"merge_method": "merge"},
            )
            if not resp.ok:
                return False, resp.text
            # Delete the head branch
            pr_data = self._get(f"repos/{self.repo}/pulls/{pr_number}")
            if isinstance(pr_data, dict):
                branch = pr_data.get("head", {}).get("ref")
                if branch:
                    requests.delete(
                        f"{GITHUB_API}/repos/{self.repo}/git/refs/heads/{branch}",
                        headers=self._headers(),
                    )
            return True, ""
        result = subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--merge", "--delete-branch"],
            capture_output=True, text=True,
        )
        return result.returncode == 0, result.stderr.strip()


def parse_action_ref(ref: str) -> tuple[str, str, str, str]:
    """Parse org/repo[/sub_path]@hash into (org, repo, sub_path, hash).

    sub_path is empty string for top-level actions (e.g. ``dorny/test-reporter@abc``),
    or a relative path for monorepo sub-actions (e.g. ``gradle/actions/setup-gradle@abc``
    yields sub_path="setup-gradle").
    """
    if "@" not in ref:
        console.print(f"[red]Error:[/red] invalid format '{ref}', expected org/repo@hash")
        sys.exit(1)
    action_path, commit_hash = ref.rsplit("@", 1)
    parts = action_path.split("/")
    if len(parts) < 2:
        console.print(f"[red]Error:[/red] invalid action path '{action_path}', expected org/repo")
        sys.exit(1)
    org, repo = parts[0], parts[1]
    sub_path = "/".join(parts[2:])  # empty string when there's no sub-path
    return org, repo, sub_path, commit_hash


def run(cmd: list[str], status: str | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run a command, failing on error."""
    return subprocess.run(cmd, check=True, **kwargs)


def beautify_js(content: str) -> str:
    """Reformat JavaScript for readable diffing."""
    opts = jsbeautifier.default_options()
    opts.indent_size = 2
    opts.wrap_line_length = 120
    result = jsbeautifier.beautify(content, opts)
    # Normalize whitespace: strip trailing spaces and collapse multiple blank lines
    lines = [line.rstrip() for line in result.splitlines()]
    return "\n".join(lines) + "\n"


def find_approved_versions(org: str, repo: str) -> list[dict]:
    """Find previously approved versions of an action in actions.yml.

    Returns a list of dicts with keys: hash, tag, expires_at, keep.
    """
    if not ACTIONS_YML.exists():
        return []

    content = ACTIONS_YML.read_text()
    lines = content.splitlines()

    action_key = f"{org}/{repo}:"
    approved = []
    in_action = False
    current_hash = None

    for line in lines:
        stripped = line.strip()

        # Top-level key (not indented)
        if line and not line[0].isspace() and not line.startswith("#"):
            in_action = stripped == action_key
            current_hash = None
            continue

        if not in_action:
            continue

        # Hash line (indented once) — look for a hex string
        if line.startswith("  ") and not line.startswith("    "):
            key = stripped.rstrip(":")
            # Check if it looks like a commit hash (40 hex chars, possibly quoted)
            clean_key = key.strip("'\"")
            if re.match(r"^[0-9a-f]{40}$", clean_key):
                current_hash = clean_key
                approved.append({"hash": current_hash})
            else:
                current_hash = None
            continue

        # Properties (indented twice)
        if current_hash and line.startswith("    "):
            if stripped.startswith("tag:"):
                approved[-1]["tag"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("expires_at:"):
                approved[-1]["expires_at"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("keep:"):
                approved[-1]["keep"] = stripped.split(":", 1)[1].strip()

    return approved


def find_approval_info(action_hash: str, gh: GitHubClient | None = None) -> dict | None:
    """Find who approved a hash and when, by searching git history and PRs."""
    # Find the commit that added this hash to actions.yml
    result = subprocess.run(
        ["git", "log", "--all", "--format=%H|%aI|%an|%s", f"-S{action_hash}", "--", "actions.yml"],
        capture_output=True,
        text=True,
        cwd=ACTIONS_YML.parent,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    # Take the most recent commit that mentions this hash
    first_line = result.stdout.strip().splitlines()[0]
    commit_hash, date, author, subject = first_line.split("|", 3)

    info = {
        "commit": commit_hash,
        "date": date,
        "author": author,
        "subject": subject,
    }

    if gh is None:
        return info

    # Try to find the PR that merged this commit
    owner, repo_name = gh.repo.split("/", 1)
    pulls = gh.get_commit_pulls(owner, repo_name, commit_hash)
    if pulls:
        pr_info = pulls[0]
        if pr_info.get("number"):
            info["pr_number"] = pr_info["number"]
            info["pr_title"] = pr_info.get("title", "")
            info["merged_by"] = (pr_info.get("merged_by") or {}).get("login", "")
            info["merged_at"] = pr_info.get("merged_at", "")

    return info


def show_approved_versions(
    org: str, repo: str, new_hash: str, approved: list[dict],
    gh: GitHubClient | None = None, ci_mode: bool = False,
) -> str | None:
    """Display approved versions and ask if user wants to diff against one.

    Returns the selected approved hash, or None.
    """
    console.print()
    console.rule(f"[bold]Previously Approved Versions of {org}/{repo}[/bold]")

    table = Table(show_header=True, border_style="blue")
    table.add_column("Tag", style="cyan")
    table.add_column("Commit Hash")
    table.add_column("Approved By", style="green")
    table.add_column("Approved On")
    table.add_column("Via PR")

    for entry in approved:
        if entry["hash"] == new_hash:
            continue

        approval = find_approval_info(entry["hash"], gh=gh)

        tag = entry.get("tag", "")
        hash_link = link(f"https://github.com/{org}/{repo}/commit/{entry['hash']}", entry['hash'][:12])

        approved_by = ""
        approved_on = ""
        pr_link = ""

        if approval:
            approved_by = approval.get("merged_by") or approval.get("author", "")
            approved_on = (approval.get("merged_at") or approval.get("date", ""))[:10]
            if "pr_number" in approval:
                pr_num = approval["pr_number"]
                pr_link = link(f"https://github.com/apache/infrastructure-actions/pull/{pr_num}", f"#{pr_num}")

        table.add_row(tag, hash_link, approved_by, approved_on, pr_link)

    console.print(table)

    # Filter to versions other than the one being checked
    other_versions = [v for v in approved if v["hash"] != new_hash]
    if not other_versions:
        return None

    if ci_mode:
        # Auto-select the newest (last) approved version
        selected = other_versions[-1]
        console.print(
            f"  Auto-selected approved version: [cyan]{selected.get('tag', '')}[/cyan] "
            f"({selected['hash'][:12]})"
        )
        return selected["hash"]

    try:
        if not ask_confirm(
            "\nWould you like to see the diff between an approved version and the one being checked?",
        ):
            return None
    except UserQuit:
        return None

    # If there's only one other version, use it directly
    if len(other_versions) == 1:
        selected = other_versions[0]
        console.print(
            f"  Using approved version: [cyan]{selected.get('tag', '')}[/cyan] "
            f"({selected['hash'][:12]})"
        )
        return selected["hash"]

    # Let user pick, default to newest (last in list)
    default_idx = len(other_versions)
    console.print("\nSelect a version to compare against:")
    for i, v in enumerate(other_versions, 1):
        tag = v.get("tag", "unknown")
        marker = " [bold cyan](default)[/bold cyan]" if i == default_idx else ""
        console.print(f"  [bold]{i}[/bold]. {tag} ({v['hash'][:12]}){marker}")

    while True:
        try:
            choice = console.input(f"\nEnter number [{default_idx}], or 'q' to skip: ").strip()
            if choice.lower() == "q":
                return None
            if not choice:
                return other_versions[default_idx - 1]["hash"]
            idx = int(choice) - 1
            if 0 <= idx < len(other_versions):
                return other_versions[idx]["hash"]
        except (ValueError, EOFError):
            return None
        console.print("[red]Invalid choice, try again[/red]")


def show_commits_between(
    org: str, repo: str, old_hash: str, new_hash: str,
    gh: GitHubClient | None = None,
) -> None:
    """Show the list of commits between two hashes using GitHub compare API."""
    console.print()
    compare_url = f"https://github.com/{org}/{repo}/compare/{old_hash[:12]}...{new_hash[:12]}?file-filters%5B%5D=%21dist"
    console.rule("[bold]Commits Between Versions[/bold]")

    raw_commits = gh.compare_commits(org, repo, old_hash, new_hash) if gh else []
    if not raw_commits and not gh:
        # Fallback: should not happen if gh is always provided, but kept for safety
        console.print(f"  [yellow]Could not fetch commits. View on GitHub:[/yellow]")
        console.print(f"  {link(compare_url, compare_url)}")
        return

    commits = [
        {
            "sha": c.get("sha", ""),
            "message": (c.get("commit", {}).get("message", "") or "").split("\n")[0],
            "author": c.get("commit", {}).get("author", {}).get("name", ""),
            "date": c.get("commit", {}).get("author", {}).get("date", ""),
        }
        for c in raw_commits
    ]

    if not commits:
        console.print(f"  [dim]No commits found between these versions[/dim]")
        return

    table = Table(show_header=True, border_style="blue")
    table.add_column("Commit", min_width=14)
    table.add_column("Author", style="green")
    table.add_column("Date")
    table.add_column("Message", max_width=60)

    for c in commits:
        sha = c.get("sha", "")
        commit_link = link(f"https://github.com/{org}/{repo}/commit/{sha}", sha[:12])
        author = c.get("author", "")
        date = c.get("date", "")[:10]
        message = c.get("message", "")
        table.add_row(commit_link, author, date, message)

    console.print(table)
    console.print(f"\n  Full comparison (dist/ excluded): {link(compare_url, compare_url)}")
    console.print(f"  [dim]{len(commits)} commit(s) between versions — dist/ is generated, source changes shown separately below[/dim]")


def diff_approved_vs_new(
    org: str, repo: str, approved_hash: str, new_hash: str, work_dir: Path,
    ci_mode: bool = False,
) -> None:
    """Diff source files between an approved version and the new version."""
    console.print()
    console.rule("[bold]Diff: Approved vs New (source changes)[/bold]")

    approved_dir = work_dir / "approved-src"
    new_dir = work_dir / "new-src"
    approved_dir.mkdir(exist_ok=True)
    new_dir.mkdir(exist_ok=True)

    repo_url = f"https://github.com/{org}/{repo}.git"

    # Directories to exclude from source comparison — these contain
    # generated/vendored code, not the actual source
    # __tests__ and __mocks__ are test fixtures, not runtime code
    excluded_dirs = {"dist", "node_modules", ".git", ".github", "__tests__", "__mocks__"}
    # Lock files to exclude — these are generated by package managers
    lock_files = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
        "shrinkwrap.json", "npm-shrinkwrap.json",
    }
    # Source file extensions to compare
    source_extensions = {".js", ".ts", ".mjs", ".cjs", ".mts", ".cts", ".json", ".yml", ".yaml"}

    with console.status("[bold blue]Fetching source from both versions...[/bold blue]"):
        clone_dir = work_dir / "repo-clone"
        run(
            ["git", "clone", "--no-checkout", repo_url, str(clone_dir)],
            capture_output=True,
        )

        # Track which excluded dirs were found so we can report them
        skipped_dirs: set[str] = set()

        for label, commit, out_dir in [
            ("approved", approved_hash, approved_dir),
            ("new", new_hash, new_dir),
        ]:
            run(
                ["git", "checkout", commit],
                capture_output=True,
                cwd=clone_dir,
            )
            # Copy source files, excluding generated directories
            for f in clone_dir.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(clone_dir)
                # Skip excluded directories
                matched = [part for part in rel.parts if part in excluded_dirs]
                if matched:
                    skipped_dirs.update(matched)
                    continue
                if rel.suffix in source_extensions:
                    dest = out_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

    console.print("  [green]✓[/green] Fetched source from both versions")

    # Categorize skipped dirs for reporting
    test_dirs = {"__tests__", "__mocks__"}
    skipped_test_dirs = sorted(skipped_dirs & test_dirs)

    # Collect source files
    approved_files = set()
    new_files = set()
    for f in approved_dir.rglob("*"):
        if f.is_file():
            approved_files.add(f.relative_to(approved_dir))
    for f in new_dir.rglob("*"):
        if f.is_file():
            new_files.add(f.relative_to(new_dir))

    all_files = sorted(approved_files | new_files)

    if not all_files:
        console.print("  [yellow]No source files found[/yellow]")
        return

    # Report all skipped items and ask for confirmation
    skipped_locks = sorted(f for f in all_files if f.name in lock_files)
    has_skips = bool(skipped_locks) or bool(skipped_test_dirs)

    if has_skips:
        console.print()
        console.print("  [bold]The following files/directories are excluded from comparison:[/bold]")
        if skipped_test_dirs:
            console.print(f"    [dim]⊘ {', '.join(skipped_test_dirs)} (test files only, not part of the action runtime)[/dim]")
        if skipped_locks:
            console.print(f"    [dim]⊘ {len(skipped_locks)} lock file(s) (generated by package managers):[/dim]")
            for f in skipped_locks:
                console.print(f"      [dim]- {f}[/dim]")
        for d in sorted(excluded_dirs - {"dist", "node_modules", ".git"}  - test_dirs):
            if any(d in str(f) for f in all_files):
                console.print(f"    [dim]⊘ {d}/ (not part of the action runtime)[/dim]")

        if not ci_mode:
            try:
                if not ask_confirm("  Proceed with these exclusions?"):
                    console.print("  [yellow]Aborted by user[/yellow]")
                    return
            except UserQuit:
                console.print("  [yellow]Aborted by user[/yellow]")
                return

    skipped_by_user: list[tuple[Path, str]] = []  # (path, reason)
    quit_all = False

    for rel_path in all_files:
        if rel_path.name in lock_files:
            continue

        if quit_all:
            skipped_by_user.append((rel_path, "skipped (quit)"))
            continue

        approved_file = approved_dir / rel_path
        new_file = new_dir / rel_path

        if rel_path not in approved_files:
            console.print(f"  [cyan]+[/cyan] {rel_path} [dim](new file)[/dim]")
            new_content = new_file.read_text(errors="replace")
            result = show_colored_diff(rel_path, "", new_content, from_label="approved", to_label="new", border="cyan", ci_mode=ci_mode)
            if result == "skip_file":
                skipped_by_user.append((rel_path, "new file"))
            elif result == "quit":
                quit_all = True
            continue

        if rel_path not in new_files:
            console.print(f"  [cyan]-[/cyan] {rel_path} [dim](removed)[/dim]")
            approved_content = approved_file.read_text(errors="replace")
            result = show_colored_diff(rel_path, approved_content, "", from_label="approved", to_label="new", border="cyan", ci_mode=ci_mode)
            if result == "skip_file":
                skipped_by_user.append((rel_path, "removed"))
            elif result == "quit":
                quit_all = True
            continue

        approved_content = approved_file.read_text(errors="replace")
        new_content = new_file.read_text(errors="replace")

        if approved_content == new_content:
            console.print(f"  [green]✓[/green] {rel_path} [green](identical)[/green]")
        else:
            console.print(f"  [cyan]~[/cyan] {rel_path} [cyan](changed — expected between versions)[/cyan]")
            result = show_colored_diff(rel_path, approved_content, new_content, from_label="approved", to_label="new", border="cyan", ci_mode=ci_mode)
            if result == "skip_file":
                skipped_by_user.append((rel_path, "changed"))
            elif result == "quit":
                quit_all = True

    # Summary
    console.print()

    # Files excluded by policy and confirmed by user
    if has_skips:
        excluded_summary = []
        if skipped_test_dirs:
            excluded_summary.append(f"  {', '.join(skipped_test_dirs)}/ (test files)")
        if skipped_locks:
            for f in skipped_locks:
                excluded_summary.append(f"  {f} (lock file)")
        for d in sorted(excluded_dirs - {"dist", "node_modules", ".git"} - test_dirs):
            if any(d in str(f) for f in all_files):
                excluded_summary.append(f"  {d}/ (not part of action runtime)")
        if excluded_summary:
            console.print(
                Panel(
                    "\n".join(excluded_summary),
                    title="[green bold]Excluded from comparison (confirmed by reviewer)[/green bold]",
                    border_style="green",
                    padding=(0, 1),
                )
            )

    # Files skipped by user that still need review
    if skipped_by_user:
        console.print(
            Panel(
                "\n".join(f"  - {f} ({reason})" for f, reason in skipped_by_user),
                title="[yellow bold]Files skipped — still need manual review[/yellow bold]",
                border_style="yellow",
                padding=(0, 1),
            )
        )


DOCKERFILE_TEMPLATE = """\
ARG NODE_VERSION=20
FROM node:${NODE_VERSION}-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN corepack enable

WORKDIR /action

ARG REPO_URL
ARG COMMIT_HASH

RUN git clone "$REPO_URL" . && git checkout "$COMMIT_HASH"

# Detect action type from action.yml or action.yaml.
# For monorepo sub-actions (SUB_PATH set), check <sub_path>/action.yml first,
# falling back to the root action.yml.
ARG SUB_PATH=""
RUN if [ -n "$SUB_PATH" ] && [ -f "$SUB_PATH/action.yml" ]; then \
      ACTION_FILE="$SUB_PATH/action.yml"; \
    elif [ -n "$SUB_PATH" ] && [ -f "$SUB_PATH/action.yaml" ]; then \
      ACTION_FILE="$SUB_PATH/action.yaml"; \
    else \
      ACTION_FILE=$(ls action.yml action.yaml 2>/dev/null | head -1); \
    fi; \
    if [ -n "$ACTION_FILE" ]; then \
      grep -E '^\\s+using:' "$ACTION_FILE" | head -1 | sed 's/.*using:\\s*//' | tr -d "'\\\"" > /action-type.txt; \
      MAIN_PATH=$(grep -E '^\\s+main:' "$ACTION_FILE" | head -1 | sed 's/.*main:\\s*//' | tr -d "'\\\"\\ "); \
      echo "$MAIN_PATH" > /main-path.txt; \
    else \
      echo "unknown" > /action-type.txt; \
      echo "" > /main-path.txt; \
    fi

# Detect the output directory from the main: path.
# For monorepo actions the main: field may use relative paths like ../dist/sub/main/index.js
# Resolve relative to the sub-action directory to get the actual repo-root-relative path.
RUN MAIN_PATH=$(cat /main-path.txt); \
    OUT_DIR="dist"; \
    if [ -n "$MAIN_PATH" ] && [ -n "$SUB_PATH" ]; then \
      RESOLVED=$(cd "$SUB_PATH" 2>/dev/null && realpath --relative-to=/action "$MAIN_PATH" 2>/dev/null || echo ""); \
      if [ -n "$RESOLVED" ]; then \
        OUT_DIR=$(echo "$RESOLVED" | cut -d'/' -f1); \
      fi; \
    elif [ -n "$MAIN_PATH" ]; then \
      DIR_PART=$(echo "$MAIN_PATH" | sed 's|/[^/]*$||'); \
      if [ "$DIR_PART" != "$MAIN_PATH" ] && [ -n "$DIR_PART" ]; then \
        OUT_DIR=$(echo "$DIR_PART" | cut -d'/' -f1); \
      fi; \
    fi; \
    echo "$OUT_DIR" > /out-dir.txt

# Save original output files before rebuild
RUN OUT_DIR=$(cat /out-dir.txt); \
    if [ -d "$OUT_DIR" ]; then cp -r "$OUT_DIR" /original-dist; else mkdir /original-dist; fi

# Detect if node_modules/ is committed (vendored dependencies pattern)
RUN if [ -d "node_modules" ]; then \
      echo "true" > /has-node-modules.txt; \
      cp -r node_modules /original-node-modules; \
    else \
      echo "false" > /has-node-modules.txt; \
      mkdir /original-node-modules; \
    fi

# Delete compiled JS from output dir before rebuild to ensure a clean build
RUN OUT_DIR=$(cat /out-dir.txt); \
    if [ -d "$OUT_DIR" ]; then find "$OUT_DIR" -name '*.js' -print -delete > /deleted-js.log 2>&1; else echo "no $OUT_DIR/ directory" > /deleted-js.log; fi

# Detect the build directory — where package.json lives.
# Some repos (e.g. gradle/actions) keep sources in a subdirectory with its own package.json.
# Also check for a root-level build script (e.g. a 'build' shell script).
RUN BUILD_DIR="."; \
    if [ ! -f package.json ]; then \
      for candidate in sources src; do \
        if [ -f "$candidate/package.json" ]; then \
          BUILD_DIR="$candidate"; \
          break; \
        fi; \
      done; \
    fi; \
    echo "$BUILD_DIR" > /build-dir.txt

# For actions with vendored node_modules, delete and reinstall with --production
# before the normal build step (which will also install devDeps for building).
RUN if [ "$(cat /has-node-modules.txt)" = "true" ]; then \
      rm -rf node_modules && \
      BUILD_DIR=$(cat /build-dir.txt) && \
      cd "$BUILD_DIR" && \
      if [ -f yarn.lock ]; then \
        corepack prepare --activate 2>/dev/null; \
        yarn install --production 2>/dev/null || yarn install 2>/dev/null || true; \
        echo "node_modules-reinstall: yarn --production (in $BUILD_DIR)" >> /build-info.log; \
      elif [ -f pnpm-lock.yaml ]; then \
        corepack prepare --activate 2>/dev/null; \
        pnpm install --prod 2>/dev/null || pnpm install 2>/dev/null || true; \
        echo "node_modules-reinstall: pnpm --prod (in $BUILD_DIR)" >> /build-info.log; \
      else \
        npm ci --production 2>/dev/null || npm install --production 2>/dev/null || true; \
        echo "node_modules-reinstall: npm --production (in $BUILD_DIR)" >> /build-info.log; \
      fi && \
      cd /action && \
      cp -r node_modules /rebuilt-node-modules; \
    else \
      mkdir /rebuilt-node-modules; \
    fi

# Detect and install with the correct package manager (in the build directory)
RUN BUILD_DIR=$(cat /build-dir.txt); \
    cd "$BUILD_DIR" && \
    if [ -f yarn.lock ]; then \
      corepack prepare --activate 2>/dev/null; \
      yarn install 2>/dev/null || true; \
      echo "pkg-manager: yarn (in $BUILD_DIR)" >> /build-info.log; \
    elif [ -f pnpm-lock.yaml ]; then \
      corepack prepare --activate 2>/dev/null; \
      pnpm install 2>/dev/null || true; \
      echo "pkg-manager: pnpm (in $BUILD_DIR)" >> /build-info.log; \
    else \
      npm ci 2>/dev/null || npm install 2>/dev/null || true; \
      echo "pkg-manager: npm (in $BUILD_DIR)" >> /build-info.log; \
    fi

# Detect which run command to use (in the build directory)
RUN BUILD_DIR=$(cat /build-dir.txt); \
    cd "$BUILD_DIR" && \
    if [ -f yarn.lock ]; then \
      echo "yarn" > /run-cmd; \
    elif [ -f pnpm-lock.yaml ]; then \
      echo "pnpm" > /run-cmd; \
    else \
      echo "npm" > /run-cmd; \
    fi

# Build: first try a root-level build script (some repos like gradle/actions use one),
# then try npm/yarn/pnpm build in the build directory, then package, then start, then ncc fallback.
# If the build directory is a subdirectory, copy its output dir to root afterwards.
RUN OUT_DIR=$(cat /out-dir.txt); \
    BUILD_DIR=$(cat /build-dir.txt); \
    RUN_CMD=$(cat /run-cmd); \
    BUILD_DONE=false; \
    if [ -x build ] && ./build dist 2>/dev/null; then \
      echo "build-step: ./build dist" >> /build-info.log; \
      if [ -d "$OUT_DIR" ] && find "$OUT_DIR" -name '*.js' -print -quit | grep -q .; then BUILD_DONE=true; fi; \
    fi && \
    if [ "$BUILD_DONE" = "false" ]; then \
      cd "$BUILD_DIR" && \
      if $RUN_CMD run build 2>/dev/null; then \
        echo "build-step: $RUN_CMD run build (in $BUILD_DIR)" >> /build-info.log; \
      elif $RUN_CMD run package 2>/dev/null; then \
        echo "build-step: $RUN_CMD run package (in $BUILD_DIR)" >> /build-info.log; \
      elif $RUN_CMD run start 2>/dev/null; then \
        echo "build-step: $RUN_CMD run start (in $BUILD_DIR)" >> /build-info.log; \
      elif npx ncc build --source-map 2>/dev/null; then \
        echo "build-step: npx ncc build --source-map (in $BUILD_DIR)" >> /build-info.log; \
      fi && \
      cd /action && \
      if [ "$BUILD_DIR" != "." ] && [ -d "$BUILD_DIR/$OUT_DIR" ] && [ ! -d "$OUT_DIR" ]; then \
        cp -r "$BUILD_DIR/$OUT_DIR" "$OUT_DIR"; \
        echo "copied $BUILD_DIR/$OUT_DIR -> $OUT_DIR" >> /build-info.log; \
      fi; \
      if [ -d "$OUT_DIR" ] && find "$OUT_DIR" -name '*.js' -print -quit | grep -q .; then BUILD_DONE=true; fi; \
    fi

# Save rebuilt output files
RUN OUT_DIR=$(cat /out-dir.txt); \
    if [ -d "$OUT_DIR" ]; then cp -r "$OUT_DIR" /rebuilt-dist; else mkdir /rebuilt-dist; fi
"""


def detect_node_version(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    gh: GitHubClient | None = None,
) -> str:
    """Detect the Node.js major version from the action's using: field.

    Fetches action.yml from GitHub at the given commit and extracts the
    node version (e.g. 'node20' -> '20').  Falls back to '20' if detection fails.
    """
    # Try action.yml then action.yaml, in sub_path first if given
    candidates = []
    if sub_path:
        candidates.extend([f"{sub_path}/action.yml", f"{sub_path}/action.yaml"])
    candidates.extend(["action.yml", "action.yaml"])

    for path in candidates:
        url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
        try:
            resp = requests.get(url, timeout=10)
            if not resp.ok:
                continue
            for line in resp.text.splitlines():
                match = re.match(r"\s+using:\s*['\"]?(node\d+)['\"]?", line)
                if match:
                    version = match.group(1).replace("node", "")
                    return version
        except requests.RequestException:
            continue

    return "20"


def _print_docker_build_steps(build_result: subprocess.CompletedProcess[str]) -> None:
    """Parse and display Docker build step summaries from --progress=plain output."""
    build_output = build_result.stderr + build_result.stdout
    step_names: dict[str, str] = {}   # step_id -> description
    step_status: dict[str, str] = {}  # step_id -> "DONE 1.2s" / "CACHED"
    for line in build_output.splitlines():
        # Step description:  #5 [3/12] RUN apt-get update ...
        m = re.match(r"^#(\d+)\s+(\[.+)", line)
        if m:
            step_names[m.group(1)] = m.group(2)
            continue
        # Done / cached:  #5 DONE 1.2s   or   #5 CACHED
        m = re.match(r"^#(\d+)\s+(DONE\s+[\d.]+s|CACHED)", line)
        if m:
            step_status[m.group(1)] = m.group(2)

    if step_names:
        console.print()
        console.rule("[bold blue]Docker build steps[/bold blue]")
        for sid in sorted(step_names, key=lambda x: int(x)):
            name = step_names[sid]
            status_str = step_status.get(sid, "")
            if "CACHED" in status_str:
                console.print(f"  [dim]✓ {name} (cached)[/dim]")
            else:
                console.print(f"  [green]✓[/green] {name} [dim]{status_str}[/dim]")
        console.print()


def build_in_docker(
    org: str, repo: str, commit_hash: str, work_dir: Path,
    sub_path: str = "",
    gh: GitHubClient | None = None,
    cache: bool = True,
    show_build_steps: bool = False,
) -> tuple[Path, Path, str, str, bool, Path, Path]:
    """Build the action in a Docker container and extract original + rebuilt dist.

    Returns (original_dir, rebuilt_dir, action_type, out_dir_name,
             has_node_modules, original_node_modules, rebuilt_node_modules).
    """
    repo_url = f"https://github.com/{org}/{repo}.git"
    container_name = f"verify-action-{org}-{repo}-{commit_hash[:12]}"

    dockerfile_path = work_dir / "Dockerfile"
    dockerfile_path.write_text(DOCKERFILE_TEMPLATE)

    original_dir = work_dir / "original-dist"
    rebuilt_dir = work_dir / "rebuilt-dist"
    original_dir.mkdir(exist_ok=True)
    rebuilt_dir.mkdir(exist_ok=True)

    image_tag = f"verify-action:{org}-{repo}-{commit_hash[:12]}"

    action_display = f"{org}/{repo}"
    if sub_path:
        action_display += f"/{sub_path}"

    repo_link = link(f"https://github.com/{org}/{repo}", action_display)
    commit_link = link(f"https://github.com/{org}/{repo}/commit/{commit_hash}", commit_hash)

    info_table = Table(show_header=False, box=None, padding=(0, 1))
    info_table.add_column(style="bold")
    info_table.add_column()
    info_table.add_row("Action", repo_link)
    info_table.add_row("Commit", commit_link)
    console.print()
    console.print(Panel(info_table, title="Action Build Verification", border_style="blue"))

    # Detect Node.js version from action.yml before building
    node_version = detect_node_version(org, repo, commit_hash, sub_path, gh=gh)
    if node_version != "20":
        console.print(f"  [green]✓[/green] Detected Node.js version: [bold]node{node_version}[/bold]")

    # Build Docker image, capturing output so we can summarise the steps afterwards
    docker_build_cmd = [
        "docker",
        "build",
        "--progress=plain",
        "--build-arg",
        f"NODE_VERSION={node_version}",
        "--build-arg",
        f"REPO_URL={repo_url}",
        "--build-arg",
        f"COMMIT_HASH={commit_hash}",
        "--build-arg",
        f"SUB_PATH={sub_path}",
        "-t",
        image_tag,
        "-f",
        str(dockerfile_path),
        str(work_dir),
    ]
    if not cache:
        docker_build_cmd.insert(3, "--no-cache")

    with console.status("[bold blue]Building Docker image...[/bold blue]"):
        build_result = subprocess.run(
            docker_build_cmd, capture_output=True, text=True,
        )
        if build_result.returncode != 0:
            # Show full output on failure so the user can diagnose
            console.print("[red]Docker build failed. Output:[/red]")
            console.print(build_result.stdout)
            console.print(build_result.stderr)
            _print_docker_build_steps(build_result)
            raise subprocess.CalledProcessError(build_result.returncode, docker_build_cmd)

    if show_build_steps:
        _print_docker_build_steps(build_result)

    with console.status("[bold blue]Extracting build artifacts...[/bold blue]") as status:

        # Extract original and rebuilt dist from container
        try:
            run(
                ["docker", "create", "--name", container_name, image_tag],
                capture_output=True,
            )

            run(
                [
                    "docker",
                    "cp",
                    f"{container_name}:/original-dist/.",
                    str(original_dir),
                ],
                capture_output=True,
            )

            run(
                [
                    "docker",
                    "cp",
                    f"{container_name}:/rebuilt-dist/.",
                    str(rebuilt_dir),
                ],
                capture_output=True,
            )
            console.print("  [green]✓[/green] Artifacts extracted")

            # Extract the detected output directory name
            out_dir_result = subprocess.run(
                ["docker", "cp", f"{container_name}:/out-dir.txt", str(work_dir / "out-dir.txt")],
                capture_output=True,
            )
            out_dir_name = "dist"
            if out_dir_result.returncode == 0:
                out_dir_name = (work_dir / "out-dir.txt").read_text().strip() or "dist"
                if out_dir_name != "dist":
                    console.print(f"  [green]✓[/green] Detected output directory: [bold]{out_dir_name}/[/bold]")

            # Extract and display the deletion log
            deleted_log = subprocess.run(
                ["docker", "cp", f"{container_name}:/deleted-js.log", str(work_dir / "deleted-js.log")],
                capture_output=True,
            )
            if deleted_log.returncode == 0:
                log_content = (work_dir / "deleted-js.log").read_text().strip()
                if log_content.startswith("no ") and log_content.endswith(" directory"):
                    console.print(f"  [yellow]![/yellow] No {out_dir_name}/ directory found before rebuild")
                else:
                    deleted_files = [l for l in log_content.splitlines() if l.strip()]
                    console.print(f"  [green]✓[/green] Deleted {len(deleted_files)} compiled JS file(s) before rebuild:")
                    for f in deleted_files:
                        console.print(f"    [dim]- {f}[/dim]")

            # Extract action type
            action_type_result = subprocess.run(
                ["docker", "cp", f"{container_name}:/action-type.txt", str(work_dir / "action-type.txt")],
                capture_output=True,
            )
            action_type = "unknown"
            if action_type_result.returncode == 0:
                action_type = (work_dir / "action-type.txt").read_text().strip()
                console.print(f"  [green]✓[/green] Action type: [bold]{action_type}[/bold]")

            # Extract node_modules flag and directories
            original_node_modules = work_dir / "original-node-modules"
            rebuilt_node_modules = work_dir / "rebuilt-node-modules"
            original_node_modules.mkdir(exist_ok=True)
            rebuilt_node_modules.mkdir(exist_ok=True)
            has_node_modules = False

            has_nm_result = subprocess.run(
                ["docker", "cp", f"{container_name}:/has-node-modules.txt",
                 str(work_dir / "has-node-modules.txt")],
                capture_output=True,
            )
            if has_nm_result.returncode == 0:
                has_node_modules = (work_dir / "has-node-modules.txt").read_text().strip() == "true"

            if has_node_modules:
                status.update("[bold blue]Extracting node_modules artifacts...[/bold blue]")
                run(
                    ["docker", "cp", f"{container_name}:/original-node-modules/.",
                     str(original_node_modules)],
                    capture_output=True,
                )
                run(
                    ["docker", "cp", f"{container_name}:/rebuilt-node-modules/.",
                     str(rebuilt_node_modules)],
                    capture_output=True,
                )
                console.print("  [green]✓[/green] Vendored node_modules detected and extracted")
        finally:
            status.update("[bold blue]Cleaning up Docker resources...[/bold blue]")
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
            )
            subprocess.run(
                ["docker", "rmi", "-f", image_tag],
                capture_output=True,
            )
            console.print("  [green]✓[/green] Cleanup complete")

    return (original_dir, rebuilt_dir, action_type, out_dir_name,
            has_node_modules, original_node_modules, rebuilt_node_modules)


def diff_node_modules(
    original_dir: Path, rebuilt_dir: Path, org: str, repo: str, commit_hash: str,
) -> bool:
    """Compare original vs rebuilt node_modules. Return True if they match."""
    blob_url = f"https://github.com/{org}/{repo}/blob/{commit_hash}/node_modules"

    # Metadata files that legitimately differ between installs
    noisy_files = {".package-lock.json", ".yarn-integrity"}
    noisy_dirs = {".cache", ".package-lock.json"}

    def collect_files(base: Path) -> dict[Path, str]:
        """Collect all files under base with their SHA256 hashes."""
        result = {}
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(base)
            # Skip noisy metadata
            if rel.name in noisy_files:
                continue
            if any(part in noisy_dirs for part in rel.parts):
                continue
            result[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        return result

    console.print()
    console.rule("[bold]Comparing vendored node_modules[/bold]")

    with console.status("[dim]Hashing files...[/dim]"):
        original_files = collect_files(original_dir)
        rebuilt_files = collect_files(rebuilt_dir)

    original_set = set(original_files.keys())
    rebuilt_set = set(rebuilt_files.keys())

    only_in_original = sorted(original_set - rebuilt_set)
    only_in_rebuilt = sorted(rebuilt_set - original_set)
    common = sorted(original_set & rebuilt_set)

    # Compare packages (top-level dirs)
    original_packages = sorted({p.parts[0] for p in original_set if len(p.parts) > 1})
    rebuilt_packages = sorted({p.parts[0] for p in rebuilt_set if len(p.parts) > 1})
    pkg_only_orig = set(original_packages) - set(rebuilt_packages)
    pkg_only_rebuilt = set(rebuilt_packages) - set(original_packages)

    console.print(
        f"  [dim]Original: {len(original_files)} files in {len(original_packages)} packages[/dim]"
    )
    console.print(
        f"  [dim]Rebuilt:  {len(rebuilt_files)} files in {len(rebuilt_packages)} packages[/dim]"
    )

    all_match = True

    if pkg_only_orig:
        all_match = False
        console.print(f"\n  [red]Packages only in original ({len(pkg_only_orig)}):[/red]")
        for pkg in sorted(pkg_only_orig):
            console.print(f"    [red]-[/red] {pkg}")

    if pkg_only_rebuilt:
        all_match = False
        console.print(f"\n  [red]Packages only in rebuilt ({len(pkg_only_rebuilt)}):[/red]")
        for pkg in sorted(pkg_only_rebuilt):
            console.print(f"    [green]+[/green] {pkg}")

    # Check for extra files only in original (potential injected files)
    extra_in_orig = [f for f in only_in_original if f.parts[0] not in pkg_only_orig]
    if extra_in_orig:
        all_match = False
        console.print(f"\n  [red]Files only in original (not from extra packages) — {len(extra_in_orig)}:[/red]")
        for f in extra_in_orig[:20]:
            file_link = link(f"{blob_url}/{f}", str(f))
            console.print(f"    [red]-[/red] {file_link}")
        if len(extra_in_orig) > 20:
            console.print(f"    [dim]... and {len(extra_in_orig) - 20} more[/dim]")

    extra_in_rebuilt = [f for f in only_in_rebuilt if f.parts[0] not in pkg_only_rebuilt]
    if extra_in_rebuilt:
        # Files only in rebuilt but not original — not necessarily malicious,
        # could be a version difference, but worth noting
        console.print(f"\n  [yellow]Files only in rebuilt (not from extra packages) — {len(extra_in_rebuilt)}:[/yellow]")
        for f in extra_in_rebuilt[:20]:
            console.print(f"    [green]+[/green] {f}")
        if len(extra_in_rebuilt) > 20:
            console.print(f"    [dim]... and {len(extra_in_rebuilt) - 20} more[/dim]")

    # Compare common files by hash
    mismatched = []
    for rel_path in common:
        if original_files[rel_path] != rebuilt_files[rel_path]:
            mismatched.append(rel_path)

    # Filter mismatched: ignore package.json fields that change between installs
    real_mismatches = []
    for rel_path in mismatched:
        if rel_path.name == "package.json":
            # Compare package.json ignoring install-specific fields
            orig_text = (original_dir / rel_path).read_text(errors="replace")
            rebuilt_text = (rebuilt_dir / rel_path).read_text(errors="replace")
            # Strip _resolved, _integrity, _from, _where, _id fields
            install_fields = {"_resolved", "_integrity", "_from", "_where", "_id",
                              "_requested", "_requiredBy", "_shasum", "_spec",
                              "_phantomChildren", "_inBundle"}
            try:
                orig_json = json.loads(orig_text)
                rebuilt_json = json.loads(rebuilt_text)
                for field in install_fields:
                    orig_json.pop(field, None)
                    rebuilt_json.pop(field, None)
                if orig_json == rebuilt_json:
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
        real_mismatches.append(rel_path)

    matched_count = len(common) - len(real_mismatches)
    if real_mismatches:
        all_match = False
        console.print(
            f"\n  [red]Files with different content — {len(real_mismatches)} of {len(common)}:[/red]"
        )
        # Show diffs for first few JS files
        shown = 0
        for rel_path in real_mismatches:
            file_link = link(f"{blob_url}/{rel_path}", str(rel_path))
            console.print(f"    [red]✗[/red] {file_link}")
            if shown < 5 and rel_path.suffix == ".js":
                orig_content = (original_dir / rel_path).read_text(errors="replace")
                rebuilt_content = (rebuilt_dir / rel_path).read_text(errors="replace")
                show_colored_diff(rel_path, orig_content, rebuilt_content)
                shown += 1
        if len(real_mismatches) > 20:
            console.print(f"    [dim]... showing first 20 of {len(real_mismatches)}[/dim]")
    else:
        console.print(f"\n  [green]✓[/green] All {matched_count} common files match")

    return all_match


def diff_js_files(
    original_dir: Path, rebuilt_dir: Path, org: str, repo: str, commit_hash: str,
    out_dir_name: str = "dist",
) -> bool:
    """Diff JS files between original and rebuilt, return True if identical."""
    blob_url = f"https://github.com/{org}/{repo}/blob/{commit_hash}"

    # Files vendored by @vercel/ncc that are not built from the action's source.
    # These are standard ncc runtime helpers and not relevant for verifying
    # that the action's own code matches the rebuild.
    ignored_files = {"sourcemap-register.js"}

    original_files = set()
    rebuilt_files = set()

    for f in original_dir.rglob("*.js"):
        original_files.add(f.relative_to(original_dir))
    for f in rebuilt_dir.rglob("*.js"):
        rebuilt_files.add(f.relative_to(rebuilt_dir))

    all_files = sorted(original_files | rebuilt_files)

    if not all_files:
        console.print(
            f"\n[yellow]No compiled JavaScript found in {out_dir_name}/ — "
            "this action may ship source JS directly (e.g. with node_modules/)[/yellow]"
        )
        return True

    console.print()
    console.rule(f"[bold]Comparing {len(all_files)} JavaScript file(s)[/bold]")

    all_match = True

    def is_minified(content: str) -> bool:
        """Check if JS content appears to be minified."""
        lines = content.splitlines()
        if not lines:
            return False
        avg_len = sum(len(l) for l in lines) / len(lines)
        # Minified JS typically has very few lines with thousands of chars
        return avg_len > 500 or len(lines) < 10

    # Check which ignored files are actually referenced by other JS files
    all_js_contents: dict[Path, str] = {}
    for rel_path in all_files:
        for base_dir in (original_dir, rebuilt_dir):
            full_path = base_dir / rel_path
            if full_path.exists() and rel_path not in all_js_contents:
                all_js_contents[rel_path] = full_path.read_text(errors="replace")

    for rel_path in all_files:
        if rel_path.name in ignored_files:
            # Check if any other JS file references this ignored file
            referenced_by = [
                other
                for other, content in all_js_contents.items()
                if other != rel_path and rel_path.name in content
            ]
            if referenced_by:
                console.print(
                    f"  [yellow]![/yellow] {rel_path} is in the ignore list but is "
                    f"referenced by: {', '.join(str(r) for r in referenced_by)} "
                    f"— [bold]comparing anyway[/bold]"
                )
            else:
                console.print(
                    f"  [dim]⊘ {rel_path} (skipped: vendored @vercel/ncc runtime helper, "
                    f"not referenced by other JS files)[/dim]"
                )
                continue

        orig_file = original_dir / rel_path
        built_file = rebuilt_dir / rel_path

        file_link = link(f"{blob_url}/{out_dir_name}/{rel_path}", str(rel_path))

        if rel_path not in original_files:
            console.print(f"  [green]+[/green] {file_link} [dim](only in rebuilt)[/dim]")
            with console.status(f"[dim]Beautifying {rel_path}...[/dim]"):
                built_content = beautify_js(built_file.read_text(errors="replace"))
            show_colored_diff(rel_path, "", built_content)
            all_match = False
            continue

        if rel_path not in rebuilt_files:
            console.print(f"  [red]-[/red] {file_link} [dim](only in original)[/dim]")
            with console.status(f"[dim]Beautifying {rel_path}...[/dim]"):
                orig_content = beautify_js(orig_file.read_text(errors="replace"))
            show_colored_diff(rel_path, orig_content, "")
            all_match = False
            continue

        orig_raw = orig_file.read_text(errors="replace")
        built_raw = built_file.read_text(errors="replace")

        with console.status(f"[dim]Beautifying {rel_path}...[/dim]"):
            orig_content = beautify_js(orig_raw)
            built_content = beautify_js(built_raw)

        if orig_content == built_content:
            console.print(f"  [green]✓[/green] {file_link} [green](identical)[/green]")
        elif not is_minified(orig_raw):
            # Non-minified JS: differences are likely due to ncc version,
            # not malicious changes. This is common for actions that use
            # `ncc build` without `--minify` — the output is readable but
            # varies slightly between ncc versions.
            console.print(
                f"  [yellow]~[/yellow] {file_link} [yellow](non-minified JS — "
                f"rebuild differs, likely due to ncc/toolchain version differences)[/yellow]"
            )
            console.print(
                f"    [dim]The dist/ JS is human-readable and not minified. Small differences "
                f"in the webpack boilerplate are expected across ncc versions.\n"
                f"    Review the source changes via the approved version diff below instead.[/dim]"
            )
        else:
            all_match = False
            console.print(f"  [red]✗[/red] {file_link} [red bold](DIFFERS)[/red bold]")
            show_colored_diff(rel_path, orig_content, built_content)

    return all_match


def show_colored_diff(
    filename: Path,
    original: str,
    rebuilt: str,
    context_lines: int = 5,
    from_label: str = "original",
    to_label: str = "rebuilt",
    border: str = "red",
    ci_mode: bool = False,
) -> str:
    """Show a colored unified diff between two strings, paged for large diffs.

    Returns "continue", "skip_file", or "quit" (skip all remaining files).
    """
    orig_lines = original.splitlines(keepends=True)
    built_lines = rebuilt.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            built_lines,
            fromfile=f"{from_label}/{filename}",
            tofile=f"{to_label}/{filename}",
            n=context_lines,
        )
    )

    if not diff_lines:
        return "continue"

    terminal_height = console.size.height - 4  # leave room for border and prompt
    page_size = max(terminal_height, 20)
    title = f"[bold]{filename}[/bold]"

    if ci_mode or len(diff_lines) <= page_size:
        # Small diff or CI mode — show in a single panel
        diff_text = _format_diff_text(diff_lines)
        console.print(Panel(diff_text, title=title, border_style=border, padding=(0, 1)))
        return "continue"

    # Large diff — page through it
    total_pages = (len(diff_lines) + page_size - 1) // page_size
    console.print(
        f"  [dim]Diff has {len(diff_lines)} lines ({total_pages} pages) — "
        f"Enter: next page, n: skip file, q: skip all remaining files[/dim]"
    )

    for page_num in range(total_pages):
        start = page_num * page_size
        end = min(start + page_size, len(diff_lines))
        page_lines = diff_lines[start:end]

        diff_text = _format_diff_text(page_lines)
        console.print(Panel(
            diff_text,
            title=title,
            border_style=border,
            padding=(0, 1),
            subtitle=f"[dim]page {page_num + 1}/{total_pages}[/dim]",
        ))

        if page_num < total_pages - 1:
            try:
                key = console.input("[dim]Enter: next page, n: skip file, q: skip all remaining files[/dim] ")
                choice = key.strip().lower()
                if choice == "n":
                    console.print(f"  [dim]Skipped remaining diff for {filename}[/dim]")
                    return "skip_file"
                if choice == "q":
                    console.print(f"  [dim]Skipping all remaining files[/dim]")
                    return "quit"
            except EOFError:
                return "quit"

    return "continue"


def _format_diff_text(lines: list[str]) -> Text:
    """Format diff lines with syntax coloring."""
    diff_text = Text()
    for line in lines:
        line_stripped = line.rstrip("\n")
        if line.startswith("---") or line.startswith("+++"):
            diff_text.append(line_stripped + "\n", style="bold")
        elif line.startswith("@@"):
            diff_text.append(line_stripped + "\n", style="cyan")
        elif line.startswith("+"):
            diff_text.append(line_stripped + "\n", style="green")
        elif line.startswith("-"):
            diff_text.append(line_stripped + "\n", style="red")
        else:
            diff_text.append(line_stripped + "\n")
    return diff_text


def fetch_action_yml(org: str, repo: str, commit_hash: str, sub_path: str = "") -> str | None:
    """Fetch action.yml content from GitHub at a specific commit."""
    candidates = []
    if sub_path:
        candidates.extend([f"{sub_path}/action.yml", f"{sub_path}/action.yaml"])
    candidates.extend(["action.yml", "action.yaml"])

    for path in candidates:
        url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.ok:
                return resp.text
        except requests.RequestException:
            continue
    return None


def fetch_file_from_github(org: str, repo: str, commit_hash: str, path: str) -> str | None:
    """Fetch a file's content from GitHub at a specific commit."""
    url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            return resp.text
    except requests.RequestException:
        pass
    return None


def extract_composite_uses(action_yml_content: str) -> list[dict]:
    """Extract all uses: references from composite action steps.

    Returns a list of dicts with keys: raw (full string), org, repo, sub_path,
    ref, is_hash_pinned, is_local, line_num.
    """
    results = []
    for i, line in enumerate(action_yml_content.splitlines(), 1):
        match = re.search(r"uses:\s+(.+?)(?:\s*#.*)?$", line.strip())
        if not match:
            continue
        raw = match.group(1).strip().strip("'\"")

        # Local action reference (e.g., ./.github/actions/foo)
        if raw.startswith("./"):
            results.append({
                "raw": raw, "org": "", "repo": "", "sub_path": "",
                "ref": "", "is_hash_pinned": True, "is_local": True,
                "line_num": i,
            })
            continue

        # Docker reference
        if raw.startswith("docker://"):
            results.append({
                "raw": raw, "org": "", "repo": "", "sub_path": "",
                "ref": "", "is_hash_pinned": True, "is_local": False,
                "line_num": i, "is_docker": True,
            })
            continue

        # Standard action reference: org/repo[/sub]@ref
        if "@" not in raw:
            continue
        action_path, ref = raw.rsplit("@", 1)
        parts = action_path.split("/")
        if len(parts) < 2:
            continue
        org, repo = parts[0], parts[1]
        sub_path = "/".join(parts[2:])
        is_hash = bool(re.match(r"^[0-9a-f]{40}$", ref))

        results.append({
            "raw": raw, "org": org, "repo": repo, "sub_path": sub_path,
            "ref": ref, "is_hash_pinned": is_hash, "is_local": False,
            "line_num": i,
        })

    return results


def _detect_action_type_from_yml(action_yml_content: str) -> str:
    """Extract the using: field from an action.yml string."""
    for line in action_yml_content.splitlines():
        m = re.match(r"\s+using:\s*['\"]?(\S+?)['\"]?\s*$", line)
        if m:
            return m.group(1)
    return "unknown"


def analyze_nested_actions(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    ci_mode: bool = False, gh: GitHubClient | None = None,
    _depth: int = 0, _visited: set | None = None,
    _checked: list | None = None,
) -> tuple[list[str], list[dict]]:
    """Analyze actions referenced in composite steps, recursing into ALL types.

    Returns (warnings, checked_actions) where checked_actions is a list of dicts
    describing each nested action that was inspected (for the summary).

    For every nested action (composite, node, docker) the function:
      - Checks hash-pinning
      - Checks our approved list
      - Detects the nested action type
      - For composite nested actions: recurses into their steps
      - For node nested actions: reports the node version and dist/ presence
      - For docker nested actions: reports the docker image
    """
    MAX_DEPTH = 3
    warnings: list[str] = []

    if _visited is None:
        _visited = set()
    if _checked is None:
        _checked = []

    action_key = f"{org}/{repo}/{sub_path}@{commit_hash}"
    if action_key in _visited:
        return warnings, _checked
    _visited.add(action_key)

    indent = "  " * (_depth + 1)

    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if not action_yml:
        warnings.append(f"Could not fetch action.yml for {org}/{repo}@{commit_hash[:12]}")
        return warnings, _checked

    uses_refs = extract_composite_uses(action_yml)
    if not uses_refs:
        return warnings, _checked

    if _depth == 0:
        console.print()
        console.rule("[bold]Nested Action Analysis[/bold]")

    for ref_info in uses_refs:
        raw = ref_info["raw"]
        line = ref_info["line_num"]

        if ref_info.get("is_local"):
            console.print(f"{indent}[dim]line {line}:[/dim] [cyan]{raw}[/cyan] [dim](local action)[/dim]")
            _checked.append({
                "action": raw, "type": "local", "pinned": True,
                "approved": True, "status": "ok",
            })
            continue

        if ref_info.get("is_docker"):
            console.print(f"{indent}[dim]line {line}:[/dim] [cyan]{raw}[/cyan] [dim](docker reference)[/dim]")
            _checked.append({
                "action": raw, "type": "docker-ref", "pinned": True,
                "approved": True, "status": "ok",
            })
            continue

        r_org, r_repo, r_sub = ref_info["org"], ref_info["repo"], ref_info["sub_path"]
        ref_str = ref_info["ref"]
        display_name = f"{r_org}/{r_repo}"
        if r_sub:
            display_name += f"/{r_sub}"

        checked_entry: dict = {
            "action": display_name, "ref": ref_str,
            "pinned": ref_info["is_hash_pinned"],
            "approved": False, "type": "unknown", "status": "ok",
            "depth": _depth + 1,
        }

        if ref_info["is_hash_pinned"]:
            # Check if this hash is in our approved_patterns / actions.yml
            approved = find_approved_versions(r_org, r_repo)
            approved_hashes = {v["hash"] for v in approved}
            is_approved = ref_str in approved_hashes
            checked_entry["approved"] = is_approved

            # Try to resolve the tag via comment
            tag_comment = ""
            for yml_line in action_yml.splitlines():
                if ref_str in yml_line and "#" in yml_line:
                    tag_comment = yml_line.split("#", 1)[1].strip()
                    break
            checked_entry["tag"] = tag_comment

            if is_approved:
                console.print(
                    f"{indent}[dim]line {line}:[/dim] [green]✓[/green] "
                    f"[link=https://github.com/{r_org}/{r_repo}/commit/{ref_str}]{display_name}@{ref_str[:12]}[/link] "
                    f"[green](hash-pinned, in our approved list)[/green]"
                )
            else:
                tag_display = f" [dim]# {tag_comment}[/dim]" if tag_comment else ""
                console.print(
                    f"{indent}[dim]line {line}:[/dim] [green]✓[/green] "
                    f"[link=https://github.com/{r_org}/{r_repo}/commit/{ref_str}]{display_name}@{ref_str[:12]}[/link]"
                    f"{tag_display} [yellow](hash-pinned, NOT in our approved list)[/yellow]"
                )
                warnings.append(
                    f"Nested action {display_name}@{ref_str[:12]} is not in our approved actions list"
                )
                checked_entry["status"] = "warn"

            # GitHub-official orgs whose actions are trusted — skip recursive
            # deep-dive but still report type for informational purposes.
            TRUSTED_ORGS = {"actions", "github"}
            is_trusted = r_org in TRUSTED_ORGS
            checked_entry["trusted"] = is_trusted

            # Fetch and inspect the nested action regardless of type
            if _depth < MAX_DEPTH:
                nested_yml = fetch_action_yml(r_org, r_repo, ref_str, r_sub)
                if nested_yml:
                    nested_type = _detect_action_type_from_yml(nested_yml)
                    checked_entry["type"] = nested_type

                    if is_trusted:
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action "
                            f"(trusted org '{r_org}' — skipping deep inspection)[/dim]"
                        )
                    elif nested_type == "composite":
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action — analyzing nested steps...[/dim]"
                        )
                        nested_warnings, _ = analyze_nested_actions(
                            r_org, r_repo, ref_str, r_sub,
                            ci_mode=ci_mode, gh=gh,
                            _depth=_depth + 1, _visited=_visited,
                            _checked=_checked,
                        )
                        warnings.extend(nested_warnings)
                    elif nested_type.startswith("node"):
                        node_ver = nested_type.replace("node", "")
                        # Check for compiled JS — try the main: path from action.yml
                        has_dist = False
                        main_path = ""
                        for yml_line in nested_yml.splitlines():
                            main_m = re.match(r"\s+main:\s*['\"]?(\S+?)['\"]?\s*$", yml_line)
                            if main_m:
                                main_path = main_m.group(1)
                                break
                        if main_path:
                            main_check = fetch_file_from_github(r_org, r_repo, ref_str, main_path)
                            has_dist = main_check is not None
                        else:
                            # Fallback: check dist/index.js
                            dist_check = fetch_file_from_github(r_org, r_repo, ref_str, "dist/index.js")
                            has_dist = dist_check is not None
                        if has_dist:
                            dist_status = f"[green]has {main_path or 'dist/'}[/green]"
                        else:
                            dist_status = "[dim]no compiled JS found[/dim]"
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action (Node.js {node_ver}), {dist_status}[/dim]"
                        )
                        # Check for nested uses: (some node actions are wrappers)
                        nested_uses = extract_composite_uses(nested_yml)
                        if nested_uses:
                            console.print(
                                f"{indent}  [dim]↳ node action also references "
                                f"{len(nested_uses)} other action(s) — inspecting...[/dim]"
                            )
                            nested_warnings, _ = analyze_nested_actions(
                                r_org, r_repo, ref_str, r_sub,
                                ci_mode=ci_mode, gh=gh,
                                _depth=_depth + 1, _visited=_visited,
                                _checked=_checked,
                            )
                            warnings.extend(nested_warnings)
                    elif nested_type == "docker":
                        # Check the docker image reference
                        for yml_line in nested_yml.splitlines():
                            img_m = re.search(r"image:\s*['\"]?(\S+?)['\"]?\s*$", yml_line.strip())
                            if img_m:
                                image = img_m.group(1)
                                if image.startswith("Dockerfile") or image.startswith("./"):
                                    console.print(
                                        f"{indent}  [dim]↳ docker action (local Dockerfile)[/dim]"
                                    )
                                elif "@sha256:" in image:
                                    console.print(
                                        f"{indent}  [dim]↳ docker action, image digest-pinned[/dim]"
                                    )
                                else:
                                    console.print(
                                        f"{indent}  [dim]↳ docker action, image: {image}[/dim]"
                                    )
                                break
                    else:
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action[/dim]"
                        )
        else:
            console.print(
                f"{indent}[dim]line {line}:[/dim] [red]✗[/red] "
                f"{display_name}@{ref_str} [red bold](NOT hash-pinned — uses tag/branch!)[/red bold]"
            )
            warnings.append(
                f"Nested action {display_name}@{ref_str} is NOT pinned to a commit hash"
            )
            checked_entry["status"] = "fail"

        _checked.append(checked_entry)

    return warnings, _checked


def analyze_dockerfile(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze Dockerfiles in the action for security concerns.

    Returns a list of warning strings.
    """
    warnings: list[str] = []

    # Try common Dockerfile locations
    candidates = ["Dockerfile"]
    if sub_path:
        candidates.insert(0, f"{sub_path}/Dockerfile")

    found_dockerfile = False
    for path in candidates:
        content = fetch_file_from_github(org, repo, commit_hash, path)
        if content is None:
            continue
        found_dockerfile = True

        console.print()
        console.rule(f"[bold]Dockerfile Analysis ({path})[/bold]")

        lines = content.splitlines()
        from_lines = []
        suspicious_cmds = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Check FROM lines for pinning
            from_match = re.match(r"FROM\s+(.+?)(?:\s+AS\s+\S+)?$", stripped, re.IGNORECASE)
            if from_match:
                image = from_match.group(1).strip()
                from_lines.append((i, image))
                # Check if it uses a digest
                if "@sha256:" in image:
                    console.print(
                        f"  [green]✓[/green] [dim]line {i}:[/dim] FROM {image} "
                        f"[green](digest-pinned)[/green]"
                    )
                elif ":" in image and not image.endswith(":latest"):
                    tag = image.split(":")[-1]
                    console.print(
                        f"  [yellow]~[/yellow] [dim]line {i}:[/dim] FROM {image} "
                        f"[yellow](tag-pinned to '{tag}', but not digest-pinned)[/yellow]"
                    )
                    warnings.append(f"Dockerfile FROM {image} is tag-pinned, not digest-pinned")
                else:
                    console.print(
                        f"  [red]✗[/red] [dim]line {i}:[/dim] FROM {image} "
                        f"[red bold](unpinned or :latest!)[/red bold]"
                    )
                    warnings.append(f"Dockerfile FROM {image} is not pinned")
                continue

            # Flag potentially suspicious commands
            lower = stripped.lower()
            if any(cmd in lower for cmd in ["curl ", "wget ", "git clone"]):
                if "requirements" not in lower and "pip" not in lower:
                    suspicious_cmds.append((i, stripped))
            # Check for network fetches to unusual places
            if re.search(r"https?://(?!github\.com|pypi\.org|registry\.npmjs\.org|dl-cdn\.alpinelinux\.org)", lower):
                url_match = re.search(r"(https?://\S+)", stripped)
                if url_match:
                    suspicious_cmds.append((i, f"External URL: {url_match.group(1)}"))

        if suspicious_cmds:
            console.print()
            console.print("  [yellow]Potentially suspicious commands:[/yellow]")
            for line_num, cmd in suspicious_cmds:
                console.print(f"    [dim]line {line_num}:[/dim] [yellow]{cmd}[/yellow]")
                warnings.append(f"Dockerfile line {line_num}: {cmd[:80]}")
        elif from_lines:
            console.print(f"  [green]✓[/green] No suspicious commands detected")

    if not found_dockerfile:
        # Check action.yml for docker image references
        action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
        if action_yml:
            for line in action_yml.splitlines():
                m = re.search(r"image:\s*['\"]?(docker://\S+)['\"]?", line.strip())
                if m:
                    console.print()
                    console.rule("[bold]Docker Image Analysis[/bold]")
                    image = m.group(1)
                    console.print(f"  [dim]Docker image reference:[/dim] {image}")
                    if "@sha256:" in image:
                        console.print(f"  [green]✓[/green] Image is digest-pinned")
                    else:
                        console.print(f"  [yellow]![/yellow] Image is NOT digest-pinned")
                        warnings.append(f"Docker image {image} is not digest-pinned")

    return warnings


def analyze_scripts(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze scripts referenced by the action for suspicious patterns.

    Returns a list of warning strings.
    """
    warnings: list[str] = []
    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if not action_yml:
        return warnings

    # Collect script files referenced in the action
    script_files: set[str] = set()

    # Look for scripts in run: blocks and in COPY/references
    for line in action_yml.splitlines():
        stripped = line.strip()
        # Skip lines that are GitHub Actions expressions
        if "${{" in stripped and "}}" in stripped:
            continue
        # Python/shell scripts in run blocks
        for ext in (".py", ".sh", ".bash", ".rb", ".pl"):
            matches = re.findall(r"(?<![.\w])[\w][\w./-]*" + re.escape(ext) + r"\b", stripped)
            for m in matches:
                # Clean up the path
                clean = m.lstrip("./").strip("'\"")
                # Skip GitHub Actions context references (e.g. steps.foo.outputs.py)
                if "steps." in clean or "outputs." in clean or "inputs." in clean:
                    continue
                # Skip URLs (e.g. upload.pypi.org -> upload.py match)
                if re.search(r"https?://.*" + re.escape(m), stripped):
                    continue
                if clean and ("/" not in clean or clean.count("/") <= 2):
                    script_files.add(clean)

    # Also look for scripts referenced in Dockerfile
    dockerfile_content = fetch_file_from_github(org, repo, commit_hash, "Dockerfile")
    if sub_path:
        sub_df = fetch_file_from_github(org, repo, commit_hash, f"{sub_path}/Dockerfile")
        if sub_df:
            dockerfile_content = sub_df
    if dockerfile_content:
        for line in dockerfile_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("COPY") or stripped.startswith("ADD"):
                for ext in (".py", ".sh", ".bash"):
                    matches = re.findall(r"[\w./-]+" + re.escape(ext), stripped)
                    for m in matches:
                        # Strip absolute container paths to get the source filename
                        clean = m.strip().lstrip("/")
                        # Remove container directory prefixes (e.g. /app/foo.sh -> foo.sh)
                        if "/" in clean:
                            clean = clean.rsplit("/", 1)[-1]
                        if clean:
                            script_files.add(clean)
            if stripped.startswith("ENTRYPOINT") or stripped.startswith("CMD"):
                for ext in (".py", ".sh", ".bash"):
                    matches = re.findall(r"[\w./-]+" + re.escape(ext), stripped)
                    for m in matches:
                        clean = m.strip().lstrip("/")
                        if "/" in clean:
                            clean = clean.rsplit("/", 1)[-1]
                        if clean:
                            script_files.add(clean)

    if not script_files:
        return warnings

    console.print()
    console.rule("[bold]Script Analysis[/bold]")

    suspicious_patterns = [
        (r"eval\s*\(", "eval() call — potential code injection"),
        (r"exec\s*\(", "exec() call — potential code injection"),
        (r"subprocess\.call\(.*shell\s*=\s*True", "subprocess with shell=True"),
        (r"os\.system\s*\(", "os.system() call"),
        (r"base64\.b64decode|atob\(", "base64 decoding — potential obfuscation"),
        (r"\\x[0-9a-f]{2}", "hex-escaped strings — potential obfuscation"),
        (r"requests?\.(get|post|put|delete|patch)\s*\(", "HTTP request (review target URL)"),
        (r"urllib\.request", "urllib request (review target URL)"),
        (r"socket\.", "socket operations"),
    ]

    for script_path in sorted(script_files):
        base_path = f"{sub_path}/{script_path}" if sub_path else script_path
        content = fetch_file_from_github(org, repo, commit_hash, base_path)
        if content is None:
            # Try without sub_path
            content = fetch_file_from_github(org, repo, commit_hash, script_path)
        if content is None:
            console.print(f"  [dim]⊘ {script_path} (not found at commit)[/dim]")
            continue

        line_count = len(content.splitlines())
        console.print(
            f"  [green]✓[/green] [link=https://github.com/{org}/{repo}/blob/{commit_hash}/{base_path}]"
            f"{script_path}[/link] [dim]({line_count} lines)[/dim]"
        )

        # Check for suspicious patterns
        findings: list[tuple[int, str, str]] = []
        for i, line in enumerate(content.splitlines(), 1):
            for pattern, description in suspicious_patterns:
                if re.search(pattern, line):
                    findings.append((i, description, line.strip()[:100]))

        if findings:
            # Group by pattern to avoid flooding
            seen_patterns: set[str] = set()
            for line_num, desc, snippet in findings:
                if desc not in seen_patterns:
                    seen_patterns.add(desc)
                    console.print(
                        f"    [yellow]![/yellow] [dim]line {line_num}:[/dim] "
                        f"[yellow]{desc}[/yellow]"
                    )
                    console.print(f"      [dim]{snippet}[/dim]")
            if len(findings) > len(seen_patterns):
                console.print(
                    f"    [dim]({len(findings)} total findings, "
                    f"{len(findings) - len(seen_patterns)} similar suppressed)[/dim]"
                )

    return warnings


def analyze_dependency_pinning(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze dependency files for pinning practices.

    Returns a list of warning strings.
    """
    warnings: list[str] = []

    # Check for Python requirements files
    req_candidates = [
        "requirements.txt", "requirements/runtime.txt",
        "requirements/runtime.in", "requirements/runtime-prerequisites.txt",
        "requirements/runtime-prerequisites.in",
    ]
    if sub_path:
        req_candidates = [f"{sub_path}/{r}" for r in req_candidates] + req_candidates

    found_reqs = False
    for req_path in req_candidates:
        content = fetch_file_from_github(org, repo, commit_hash, req_path)
        if content is None:
            continue

        if not found_reqs:
            console.print()
            console.rule("[bold]Dependency Pinning Analysis[/bold]")
            found_reqs = True

        lines = content.splitlines()
        total_deps = 0
        pinned_deps = 0
        unpinned_deps = []
        has_hashes = False

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            # Skip constraint references
            if stripped.startswith("-c "):
                continue

            total_deps += 1
            # Check for hash pinning
            if "--hash=" in stripped or "\\$" in stripped:
                has_hashes = True

            # Check for version pinning (==, ~=, >=)
            if "==" in stripped:
                pinned_deps += 1
            elif "~=" in stripped or ">=" in stripped:
                pinned_deps += 1
                # ~= and >= are less strict than ==
                pkg_name = re.split(r"[~>=<!\s]", stripped)[0]
                if ".in" in req_path:
                    pass  # .in files are expected to have loose pins
                else:
                    unpinned_deps.append((pkg_name, stripped))
            else:
                pkg_name = re.split(r"[~>=<!\s\[]", stripped)[0]
                if pkg_name and not pkg_name.startswith("("):
                    unpinned_deps.append((pkg_name, stripped))

        # Determine if this is a .in (input) or .txt (compiled) file
        is_compiled = req_path.endswith(".txt")
        file_type = "compiled" if is_compiled else "input"

        if total_deps > 0:
            pin_pct = (pinned_deps / total_deps) * 100
            status = "[green]✓[/green]" if pin_pct >= 90 else "[yellow]![/yellow]"
            console.print(
                f"  {status} [link=https://github.com/{org}/{repo}/blob/{commit_hash}/{req_path}]"
                f"{req_path}[/link] [dim]({file_type})[/dim]: "
                f"{pinned_deps}/{total_deps} deps pinned ({pin_pct:.0f}%)"
            )

            if unpinned_deps and is_compiled:
                for pkg, spec in unpinned_deps[:5]:
                    console.print(f"    [yellow]![/yellow] [dim]{spec}[/dim]")
                    warnings.append(f"{req_path}: {pkg} not strictly pinned")
                if len(unpinned_deps) > 5:
                    console.print(f"    [dim]... and {len(unpinned_deps) - 5} more[/dim]")

    # Check for package.json
    pkg_json_path = f"{sub_path}/package.json" if sub_path else "package.json"
    content = fetch_file_from_github(org, repo, commit_hash, pkg_json_path)
    if content:
        if not found_reqs:
            console.print()
            console.rule("[bold]Dependency Pinning Analysis[/bold]")
            found_reqs = True

        try:
            pkg = json.loads(content)
            for dep_type in ("dependencies", "devDependencies"):
                deps = pkg.get(dep_type, {})
                if not deps:
                    continue
                unpinned = [
                    (name, ver) for name, ver in deps.items()
                    if not re.match(r"^\d+\.\d+\.\d+$", ver)  # exact version only
                ]
                total = len(deps)
                pinned = total - len(unpinned)
                pin_pct = (pinned / total) * 100 if total else 100
                status = "[green]✓[/green]" if pin_pct >= 80 else "[yellow]![/yellow]"
                console.print(
                    f"  {status} {pkg_json_path} [{dep_type}]: "
                    f"{pinned}/{total} deps exact-pinned ({pin_pct:.0f}%)"
                )
                if unpinned[:5]:
                    for name, ver in unpinned[:5]:
                        console.print(f"    [dim]{name}: {ver}[/dim]")
        except (json.JSONDecodeError, KeyError):
            pass

    # Check for lock files existence
    lock_files = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]
    if sub_path:
        lock_files = [f"{sub_path}/{lf}" for lf in lock_files] + lock_files
    for lf_path in lock_files:
        content = fetch_file_from_github(org, repo, commit_hash, lf_path)
        if content is not None:
            if not found_reqs:
                console.print()
                console.rule("[bold]Dependency Pinning Analysis[/bold]")
                found_reqs = True
            console.print(f"  [green]✓[/green] Lock file present: {lf_path}")
            break

    return warnings


def analyze_action_metadata(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze action.yml metadata for security-relevant fields.

    Checks: permissions requests, environment variable usage, inline shell
    commands in run: blocks, github_token exposure, and GITHUB_ENV writes.
    """
    warnings: list[str] = []
    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if not action_yml:
        return warnings

    console.print()
    console.rule("[bold]Action Metadata Analysis[/bold]")

    lines = action_yml.splitlines()

    # --- Check inputs for secrets / sensitive defaults ---
    sensitive_input_patterns = [
        (r"default:\s*\$\{\{\s*secrets\.", "input defaults to a secret"),
        (r"default:\s*\$\{\{\s*github\.token", "input defaults to github.token"),
    ]
    for i, line in enumerate(lines, 1):
        for pattern, desc in sensitive_input_patterns:
            if re.search(pattern, line):
                console.print(
                    f"  [yellow]![/yellow] [dim]line {i}:[/dim] "
                    f"[yellow]{desc}[/yellow]"
                )
                console.print(f"    [dim]{line.strip()[:100]}[/dim]")
                warnings.append(f"action.yml line {i}: {desc}")

    # --- Analyze inline run: blocks ---
    in_run_block = False
    run_lines: list[tuple[int, str]] = []
    dangerous_shell_patterns = [
        (r"curl\s+.*\|\s*(ba)?sh", "pipe-to-shell (curl | sh) — high risk"),
        (r"wget\s+.*\|\s*(ba)?sh", "pipe-to-shell (wget | sh) — high risk"),
        (r'\$\{\{\s*inputs\.', "direct input interpolation in shell (injection risk)"),
        (r'GITHUB_ENV', "writes to GITHUB_ENV (can affect subsequent steps)"),
        (r'GITHUB_PATH', "writes to GITHUB_PATH (can affect subsequent steps)"),
        (r'GITHUB_OUTPUT', None),  # Normal — just note it
    ]

    shell_findings: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"run:\s*\|", stripped) or re.match(r"run:\s+\S", stripped):
            in_run_block = True
            continue
        if in_run_block:
            # End of run block: next key at same/lower indent
            if stripped and not line[0].isspace():
                in_run_block = False
            elif stripped and re.match(r"\s+\w+:", line) and not line.startswith("        "):
                # New YAML key at step level
                if not stripped.startswith("#") and not stripped.startswith("-"):
                    in_run_block = False

        if in_run_block or (re.match(r"\s+run:\s+", line)):
            for pattern, desc in dangerous_shell_patterns:
                if desc is None:
                    continue
                if re.search(pattern, line):
                    shell_findings.append((i, desc, stripped[:100]))

    if shell_findings:
        # Deduplicate by description
        seen: set[str] = set()
        shown = 0
        for line_num, desc, snippet in shell_findings:
            key = desc
            if key not in seen:
                seen.add(key)
                console.print(
                    f"  [yellow]![/yellow] [dim]line {line_num}:[/dim] "
                    f"[yellow]{desc}[/yellow]"
                )
                console.print(f"    [dim]{snippet}[/dim]")
                if "high risk" in desc or "injection" in desc:
                    warnings.append(f"action.yml line {line_num}: {desc}")
                shown += 1
        if len(shell_findings) > shown:
            console.print(
                f"  [dim]({len(shell_findings)} total shell findings, "
                f"{len(shell_findings) - shown} similar suppressed)[/dim]"
            )
    else:
        console.print("  [green]✓[/green] No dangerous shell patterns in run: blocks")

    # --- Check for environment variable exposure ---
    env_secrets = []
    for i, line in enumerate(lines, 1):
        if re.search(r"\$\{\{\s*secrets\.", line):
            env_secrets.append((i, line.strip()[:100]))
    if env_secrets:
        console.print(f"  [dim]ℹ[/dim] Secrets referenced in {len(env_secrets)} place(s):")
        for line_num, snippet in env_secrets[:5]:
            console.print(f"    [dim]line {line_num}: {snippet}[/dim]")
    else:
        console.print("  [green]✓[/green] No secrets referenced")

    # --- Count total steps and run blocks ---
    step_count = sum(1 for line in lines if re.match(r"\s+- name:", line))
    run_count = sum(1 for line in lines if re.match(r"\s+run:", line.rstrip()))
    uses_count = sum(1 for line in lines if re.match(r"\s+uses:", line.rstrip()))
    console.print(
        f"  [dim]ℹ[/dim] {step_count} step(s): "
        f"{uses_count} uses: action(s) + {run_count} run: block(s)"
    )

    return warnings


def analyze_repo_metadata(
    org: str, repo: str, commit_hash: str,
) -> list[str]:
    """Check repo-level signals: license, recent commits, contributor count."""
    warnings: list[str] = []

    console.print()
    console.rule("[bold]Repository Metadata[/bold]")

    # Check for LICENSE file
    for license_name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        content = fetch_file_from_github(org, repo, commit_hash, license_name)
        if content is not None:
            # Try to identify the license type from first few lines
            first_lines = content[:500].lower()
            license_type = "unknown"
            for name, pattern in [
                ("MIT", "mit license"),
                ("Apache 2.0", "apache license"),
                ("BSD", "bsd"),
                ("GPL", "gnu general public"),
                ("ISC", "isc license"),
                ("MPL", "mozilla public"),
            ]:
                if pattern in first_lines:
                    license_type = name
                    break
            console.print(f"  [green]✓[/green] License: {license_name} ({license_type})")
            break
    else:
        console.print(f"  [yellow]![/yellow] No LICENSE file found")
        warnings.append("No LICENSE file found in repository")

    # Check for security policy
    for sec_name in ("SECURITY.md", ".github/SECURITY.md"):
        content = fetch_file_from_github(org, repo, commit_hash, sec_name)
        if content is not None:
            console.print(f"  [green]✓[/green] Security policy: {sec_name}")
            break
    else:
        console.print(f"  [dim]ℹ[/dim] No SECURITY.md found")

    # Show the org/owner for trust signal
    well_known_orgs = {
        "actions", "github", "google-github-actions", "aws-actions",
        "azure", "docker", "hashicorp", "pypa", "gradle",
    }
    if org in well_known_orgs:
        console.print(f"  [green]✓[/green] Well-known org: [bold]{org}[/bold]")
    else:
        console.print(f"  [dim]ℹ[/dim] Org: {org} (not in well-known list)")

    return warnings


def show_verification_summary(
    org: str, repo: str, commit_hash: str, sub_path: str,
    action_type: str, is_js_action: bool, all_match: bool,
    non_js_warnings: list[str] | None,
    checked_actions: list[dict] | None,
    checks_performed: list[tuple[str, str, str]],
    ci_mode: bool = False,
) -> None:
    """Show a structured summary of all checks performed.

    checks_performed is a list of (check_name, status, detail) where
    status is one of "pass", "warn", "fail", "skip", "info".
    """
    console.print()
    console.rule("[bold]Verification Summary[/bold]")

    display_name = f"{org}/{repo}"
    if sub_path:
        display_name += f"/{sub_path}"

    action_url = f"https://github.com/{org}/{repo}/tree/{commit_hash}"
    if sub_path:
        action_url += f"/{sub_path}"

    # Summary table
    table = Table(show_header=True, border_style="blue", title=f"[bold]{display_name}@{commit_hash[:12]}[/bold]")
    table.add_column("Check", style="bold", min_width=30)
    table.add_column("Status", min_width=6, justify="center")
    table.add_column("Detail", max_width=60)

    status_icons = {
        "pass": "[green]✓[/green]",
        "warn": "[yellow]![/yellow]",
        "fail": "[red]✗[/red]",
        "skip": "[dim]⊘[/dim]",
        "info": "[dim]ℹ[/dim]",
    }

    for check_name, status, detail in checks_performed:
        icon = status_icons.get(status, "[dim]?[/dim]")
        table.add_row(check_name, icon, detail)

    console.print(table)

    # Show nested actions sub-table if any were checked
    if checked_actions:
        console.print()
        nested_table = Table(
            show_header=True, border_style="cyan",
            title="[bold]Nested Actions Inspected[/bold]",
        )
        nested_table.add_column("Action", min_width=30)
        nested_table.add_column("Type", min_width=10)
        nested_table.add_column("Pinned", justify="center")
        nested_table.add_column("Approved", justify="center")
        nested_table.add_column("Trusted", justify="center")

        for entry in checked_actions:
            action_name = entry.get("action", "?")
            atype = entry.get("type", "?")
            tag = entry.get("tag", "")
            if tag:
                action_name += f" ({tag})"
            pinned_icon = "[green]✓[/green]" if entry.get("pinned") else "[red]✗[/red]"
            approved_icon = "[green]✓[/green]" if entry.get("approved") else "[yellow]—[/yellow]"
            if entry.get("type") in ("local", "docker-ref"):
                approved_icon = "[dim]n/a[/dim]"
            if entry.get("trusted"):
                trusted_icon = "[green]✓[/green]"
            elif entry.get("type") in ("local", "docker-ref"):
                trusted_icon = "[dim]n/a[/dim]"
            else:
                trusted_icon = "[dim]—[/dim]"
            nested_table.add_row(action_name, atype, pinned_icon, approved_icon, trusted_icon)

        console.print(nested_table)


def verify_single_action(
    action_ref: str, gh: GitHubClient | None = None, ci_mode: bool = False,
    cache: bool = True, show_build_steps: bool = False,
) -> bool:
    """Verify a single action reference. Returns True if verification passed."""
    org, repo, sub_path, commit_hash = parse_action_ref(action_ref)

    # Track all checks performed for the summary
    checks_performed: list[tuple[str, str, str]] = []
    non_js_warnings: list[str] = []
    checked_actions: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="verify-action-") as tmp:
        work_dir = Path(tmp)
        (original_dir, rebuilt_dir, action_type, out_dir_name,
         has_node_modules, original_node_modules, rebuilt_node_modules) = build_in_docker(
            org, repo, commit_hash, work_dir, sub_path=sub_path, gh=gh,
            cache=cache, show_build_steps=show_build_steps,
        )

        checks_performed.append(("Action type detection", "info", action_type))

        # Non-JavaScript actions (docker, composite) don't have compiled JS to verify
        is_js_action = action_type.startswith("node") or action_type in ("unknown",)
        if not is_js_action:
            console.print()
            console.print(
                Panel(
                    f"[yellow]This is a [bold]{action_type}[/bold] action, not a JavaScript action.\n"
                    f"Build verification of compiled JS is not applicable — "
                    f"running composite/docker-specific checks instead.[/yellow]",
                    border_style="yellow",
                    title="NON-JS ACTION",
                )
            )
            all_match = True
            checks_performed.append(("JS build verification", "skip", f"not applicable for {action_type}"))

            # Run nested action analysis (for ALL action types, not just composite)
            nested_warnings, checked_actions = analyze_nested_actions(
                org, repo, commit_hash, sub_path,
                ci_mode=ci_mode, gh=gh,
            )
            non_js_warnings.extend(nested_warnings)
            if checked_actions:
                unpinned = sum(1 for a in checked_actions if not a.get("pinned"))
                unapproved = sum(
                    1 for a in checked_actions
                    if not a.get("approved") and a.get("type") not in ("local", "docker-ref")
                )
                status = "pass"
                detail = f"{len(checked_actions)} action(s) inspected"
                if unpinned:
                    status = "fail"
                    detail += f", {unpinned} NOT hash-pinned"
                elif unapproved:
                    status = "warn"
                    detail += f", {unapproved} not in approved list"
                checks_performed.append(("Nested action analysis", status, detail))
            else:
                checks_performed.append(("Nested action analysis", "info", "no nested uses: found"))

            if action_type in ("composite", "docker"):
                docker_warnings = analyze_dockerfile(org, repo, commit_hash, sub_path)
                non_js_warnings.extend(docker_warnings)
                if docker_warnings:
                    checks_performed.append(("Dockerfile analysis", "warn", f"{len(docker_warnings)} warning(s)"))
                else:
                    # Check if Dockerfile exists
                    df_exists = fetch_file_from_github(org, repo, commit_hash, "Dockerfile") is not None
                    if df_exists:
                        checks_performed.append(("Dockerfile analysis", "pass", "no issues found"))
                    else:
                        checks_performed.append(("Dockerfile analysis", "skip", "no Dockerfile"))

            script_warnings = analyze_scripts(org, repo, commit_hash, sub_path)
            non_js_warnings.extend(script_warnings)
            checks_performed.append((
                "Script analysis",
                "warn" if script_warnings else "pass",
                f"{len(script_warnings)} warning(s)" if script_warnings else "no suspicious patterns",
            ))

            dep_warnings = analyze_dependency_pinning(org, repo, commit_hash, sub_path)
            non_js_warnings.extend(dep_warnings)
            checks_performed.append((
                "Dependency pinning",
                "warn" if dep_warnings else "pass",
                f"{len(dep_warnings)} warning(s)" if dep_warnings else "dependencies pinned",
            ))

            # Action metadata analysis (permissions, shell, env)
            metadata_warnings = analyze_action_metadata(org, repo, commit_hash, sub_path)
            non_js_warnings.extend(metadata_warnings)
            checks_performed.append((
                "Action metadata (shell/env/secrets)",
                "warn" if metadata_warnings else "pass",
                f"{len(metadata_warnings)} warning(s)" if metadata_warnings else "no issues",
            ))

            # Repo metadata (license, security policy, org trust)
            repo_warnings = analyze_repo_metadata(org, repo, commit_hash)
            non_js_warnings.extend(repo_warnings)
            checks_performed.append((
                "Repository metadata",
                "warn" if repo_warnings else "pass",
                f"{len(repo_warnings)} warning(s)" if repo_warnings else "ok",
            ))

            # Show warnings summary
            if non_js_warnings:
                console.print()
                console.print(
                    Panel(
                        "\n".join(f"  [yellow]![/yellow] {w}" for w in non_js_warnings),
                        title=f"[yellow bold]{len(non_js_warnings)} Warning(s)[/yellow bold]",
                        border_style="yellow",
                        padding=(0, 1),
                    )
                )
            else:
                console.print()
                console.print(
                    "  [green]✓[/green] All checks passed with no warnings"
                )
        else:
            all_match = diff_js_files(
                original_dir, rebuilt_dir, org, repo, commit_hash, out_dir_name,
            )
            checks_performed.append((
                "JS build verification",
                "pass" if all_match else "fail",
                "compiled JS matches rebuild" if all_match else "DIFFERENCES DETECTED",
            ))

            # If no compiled JS was found in dist/ but node_modules is vendored,
            # verify node_modules instead
            if has_node_modules:
                nm_match = diff_node_modules(
                    original_node_modules, rebuilt_node_modules,
                    org, repo, commit_hash,
                )
                all_match = all_match and nm_match

        # Check for previously approved versions and offer to diff
        approved = find_approved_versions(org, repo)
        if approved:
            checks_performed.append(("Approved versions", "info", f"{len(approved)} version(s) on file"))
            selected_hash = show_approved_versions(org, repo, commit_hash, approved, gh=gh, ci_mode=ci_mode)
            if selected_hash:
                show_commits_between(org, repo, selected_hash, commit_hash, gh=gh)
                diff_approved_vs_new(org, repo, selected_hash, commit_hash, work_dir, ci_mode=ci_mode)
                checks_performed.append(("Source diff vs approved", "info", f"compared against {selected_hash[:12]}"))
        else:
            checks_performed.append(("Approved versions", "info", "new action (none on file)"))
            if not is_js_action:
                console.print(
                    "  [dim]No previously approved versions found — "
                    "this appears to be a new action[/dim]"
                )

    # Show verification summary
    show_verification_summary(
        org, repo, commit_hash, sub_path,
        action_type, is_js_action, all_match,
        non_js_warnings if not is_js_action else None,
        checked_actions if checked_actions else None,
        checks_performed,
        ci_mode=ci_mode,
    )

    # Final result banner
    console.print()
    checklist_hint = f"\n[dim]Security review checklist: {SECURITY_CHECKLIST_URL}[/dim]"
    if all_match:
        if is_js_action:
            if has_node_modules:
                result_msg = "[green bold]Vendored node_modules matches fresh install[/green bold]"
            else:
                result_msg = "[green bold]All compiled JavaScript matches the rebuild[/green bold]"
        else:
            if non_js_warnings:
                result_msg = (
                    f"[yellow bold]{action_type} action — {len(non_js_warnings)} warning(s) "
                    f"found during analysis (review above)[/yellow bold]"
                )
            else:
                result_msg = (
                    f"[green bold]{action_type} action — all checks passed[/green bold]"
                )
        border = "yellow" if not is_js_action and non_js_warnings else "green"
        console.print(Panel(result_msg + checklist_hint, border_style=border, title="RESULT"))
    else:
        console.print(
            Panel(
                "[red bold]Differences detected between published and rebuilt JS[/red bold]"
                + checklist_hint,
                border_style="red",
                title="RESULT",
            )
        )

    return all_match


def extract_action_refs_from_pr(pr_number: int, gh: GitHubClient | None = None) -> list[str]:
    """Extract all new action org/repo[/sub]@hash refs from a PR diff.

    Looks in two places:
    1. Workflow files: ``uses: org/repo@hash`` lines
    2. actions.yml: top-level ``org/repo:`` keys followed by indented commit hashes

    Returns a deduplicated list of action references found in added lines.
    """
    if gh is None:
        return []
    diff_text = gh.get_pr_diff(pr_number)
    if not diff_text:
        return []

    seen: set[str] = set()
    refs: list[str] = []

    # Track the current action key from actions.yml for multi-line matching
    # Format:
    #   +org/repo:
    #   +  <40-hex-hash>:
    actions_yml_key: str | None = None

    for line in diff_text.splitlines():
        # --- Workflow files: uses: org/repo@hash ---
        # Also match 'use:' (common typo for 'uses:')
        match = re.search(r"^\+.*uses?:\s+([^@\s]+)@([0-9a-f]{40})", line)
        if match:
            action_path = match.group(1)
            commit_hash = match.group(2)
            ref = f"{action_path}@{commit_hash}"
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
            continue

        # --- actions.yml: org/repo: as top-level key ---
        # Match added lines like: +org/repo:  or  +org/repo/sub:
        key_match = re.match(r"^\+([a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+):\s*$", line)
        if key_match:
            actions_yml_key = key_match.group(1).rstrip("/")
            continue

        # Match indented hash under the current key: +  <40-hex>:
        if actions_yml_key:
            hash_match = re.match(r"^\+\s+['\"]?([0-9a-f]{40})['\"]?:\s*$", line)
            if hash_match:
                commit_hash = hash_match.group(1)
                ref = f"{actions_yml_key}@{commit_hash}"
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
                continue

            # Still under the same key if the line is an added indented property
            # (e.g. +    tag: v1.0.0) — don't reset
            if re.match(r"^\+\s{4,}", line):
                continue

            # Any other line resets the key context
            actions_yml_key = None

    return refs


def get_gh_user(gh: GitHubClient | None = None) -> str:
    """Get the currently authenticated GitHub username."""
    if gh is None:
        return "unknown"
    return gh.get_authenticated_user()


def check_dependabot_prs(gh: GitHubClient, cache: bool = True, show_build_steps: bool = False) -> None:
    """List open dependabot PRs, verify each, and optionally merge."""
    console.print()
    console.rule("[bold]Dependabot PR Review[/bold]")

    with console.status("[bold blue]Fetching open dependabot PRs...[/bold blue]"):
        all_prs = gh.list_open_prs(author="app/dependabot")

    if not all_prs:
        console.print("[green]No open dependabot PRs found[/green]")
        return

    # Separate eligible PRs from excluded ones
    eligible_prs: list[dict] = []
    excluded_prs: list[tuple[dict, str]] = []  # (pr, reason)

    for pr in all_prs:
        # Check for "changes requested" reviews
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            excluded_prs.append((pr, "changes requested by reviewer"))
            continue

        # Check for failed status checks
        checks = pr.get("statusCheckRollup", []) or []
        failed_checks = [
            c.get("name", "unknown")
            for c in checks
            if c.get("conclusion") in ("FAILURE", "ERROR", "CANCELLED")
            and c.get("status") == "COMPLETED"
        ]
        if failed_checks:
            excluded_prs.append((pr, f"failed checks: {', '.join(failed_checks)}"))
            continue

        eligible_prs.append(pr)

    # Show excluded PRs first
    if excluded_prs:
        console.print()
        console.print("[bold]Excluded PRs:[/bold]")
        exc_table = Table(show_header=True, border_style="yellow")
        exc_table.add_column("PR", style="bold", min_width=8)
        exc_table.add_column("Title")
        exc_table.add_column("Reason", style="yellow")

        for pr, reason in excluded_prs:
            pr_link = link(pr["url"], f"#{pr['number']}")
            exc_table.add_row(pr_link, pr["title"], reason)

        console.print(exc_table)
        console.print(
            f"\n  [dim]{len(excluded_prs)} PR(s) excluded — these need manual attention "
            f"(resolve review comments or fix failing checks first)[/dim]"
        )

    if not eligible_prs:
        console.print("\n[yellow]No eligible dependabot PRs to review[/yellow]")
        return

    prs = eligible_prs

    # Display eligible PRs
    console.print()
    console.print("[bold]Eligible PRs:[/bold]")
    table = Table(show_header=True, border_style="blue")
    table.add_column("#", style="bold", min_width=5)
    table.add_column("Title")
    table.add_column("PR", min_width=8)

    for pr in prs:
        pr_link = link(pr["url"], f"#{pr['number']}")
        table.add_row(str(pr["number"]), pr["title"], pr_link)

    console.print(table)
    console.print(f"\n  [dim]{len(prs)} eligible PR(s) to review[/dim]")

    try:
        if not ask_confirm("\n  Review these PRs?"):
            return
    except UserQuit:
        return

    gh_user = get_gh_user(gh=gh)
    reviewed: list[dict] = []
    failed: list[dict] = []

    for pr in prs:
        console.print()
        pr_link = link(f"https://github.com/apache/infrastructure-actions/pull/{pr['number']}", f"#{pr['number']}")
        console.rule(f"[bold]PR {pr_link}: {pr['title']}[/bold]")

        # Extract all action references from PR diff
        with console.status("[bold blue]Extracting action references from PR...[/bold blue]"):
            action_refs = extract_action_refs_from_pr(pr["number"], gh=gh)

        if not action_refs:
            console.print(
                f"  [yellow]Could not extract action reference from PR {pr_link} — skipping[/yellow]"
            )
            continue

        for ref in action_refs:
            console.print(f"  Action: [bold]{ref}[/bold]")

        # Group refs by org/repo@hash to detect monorepo sub-actions
        # For a PR with gradle/actions/setup-gradle@abc and gradle/actions/dependency-submission@abc,
        # we verify once via the first ref, passing all sub-paths as siblings
        refs_by_base: dict[str, list[str]] = {}
        for ref in action_refs:
            org, repo, sub_path, commit_hash = parse_action_ref(ref)
            base_key = f"{org}/{repo}@{commit_hash}"
            refs_by_base.setdefault(base_key, []).append(sub_path)

        # Run verification
        passed = True
        for base_key, sub_paths in refs_by_base.items():
            org_repo, commit_hash = base_key.rsplit("@", 1)
            if any(sub_paths):
                # Monorepo with sub-actions — verify each sub-action directly
                console.print()
                console.print(
                    Panel(
                        f"[cyan]Monorepo action — verifying "
                        f"{len(sub_paths)} sub-action(s): "
                        f"{', '.join(sp for sp in sub_paths if sp)}[/cyan]",
                        border_style="cyan",
                        title="MONOREPO",
                    )
                )
                for sp in sub_paths:
                    if sp:
                        sub_ref = f"{org_repo}/{sp}@{commit_hash}"
                    else:
                        sub_ref = f"{org_repo}@{commit_hash}"
                    if not verify_single_action(sub_ref, gh=gh, cache=cache, show_build_steps=show_build_steps):
                        passed = False
            else:
                # Simple single action (no sub-path)
                if not verify_single_action(f"{org_repo}@{commit_hash}", gh=gh, cache=cache, show_build_steps=show_build_steps):
                    passed = False

        if not passed:
            console.print(
                f"\n  [red]Verification failed for PR {pr_link} — skipping merge[/red]"
            )
            failed.append(pr)
            continue

        # Ask to merge
        try:
            if not ask_confirm(f"\n  Merge PR {pr_link}?"):
                console.print(f"  [dim]Skipped merging PR {pr_link}[/dim]")
                continue
        except UserQuit:
            console.print(f"  [dim]Quitting review[/dim]")
            break

        # Add review comment and merge
        verified_list = "\n".join(f"- `{ref}`" for ref in action_refs)
        comment = (
            f"Reviewed by @{gh_user} using `verify-action-build.py`.\n\n"
            f"Verified:\n{verified_list}\n\n"
            f"- All CI/status checks were passing\n"
            f"- No review changes were requested\n"
            f"- Compiled JavaScript was rebuilt in an isolated Docker container "
            f"and compared against the published version\n"
            f"- Source changes between the previously approved version and this commit "
            f"were reviewed\n\n"
            f"Approving and merging."
        )

        console.print(f"  [dim]Adding review comment...[/dim]")
        if not gh.approve_pr(pr["number"], comment):
            console.print(f"  [yellow]Warning: could not add review comment[/yellow]")

        console.print(f"  [dim]Merging PR {pr_link}...[/dim]")
        success, err = gh.merge_pr(pr["number"])
        if success:
            console.print(f"  [green]✓ PR {pr_link} merged successfully[/green]")
            reviewed.append(pr)
        else:
            console.print(
                f"  [red]Failed to merge PR {pr_link}: {err}[/red]"
            )
            failed.append(pr)

    # Summary
    console.print()
    console.rule("[bold]Dependabot Review Summary[/bold]")
    if reviewed:
        console.print(
            Panel(
                "\n".join(f"  ✓ #{pr['number']} — {pr['title']}" for pr in reviewed),
                title="[green bold]Merged[/green bold]",
                border_style="green",
                padding=(0, 1),
            )
        )
    if failed:
        console.print(
            Panel(
                "\n".join(f"  ✗ #{pr['number']} — {pr['title']}" for pr in failed),
                title="[red bold]Failed / Skipped[/red bold]",
                border_style="red",
                padding=(0, 1),
            )
        )


def _exit(code: int) -> None:
    console.print(f"Exit code: {code}")
    sys.exit(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify compiled JS in a GitHub Action matches a local rebuild.",
        usage="uv run %(prog)s [org/repo@commit_hash | --check-dependabot-prs | --from-pr N]",
        epilog=f"Security review checklist: {SECURITY_CHECKLIST_URL}",
    )
    parser.add_argument(
        "action_ref",
        nargs="?",
        help="Action reference in org/repo@commit_hash format",
    )
    parser.add_argument(
        "--check-dependabot-prs",
        action="store_true",
        help="Review open dependabot PRs: verify each action, optionally approve and merge",
    )
    parser.add_argument(
        "--no-gh",
        action="store_true",
        help="Use the GitHub REST API via requests instead of the gh CLI",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token for API access (default: $GITHUB_TOKEN env var). Required with --no-gh",
    )
    parser.add_argument(
        "--from-pr",
        type=int,
        metavar="N",
        help="Extract action reference from PR #N and verify it",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Non-interactive mode: skip all prompts, auto-select defaults (for CI pipelines)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Build Docker image from scratch without using layer cache",
    )
    parser.add_argument(
        "--show-build-steps",
        action="store_true",
        help="Show Docker build step summary on successful builds (always shown on failure)",
    )
    args = parser.parse_args()

    ci_mode = args.ci
    cache = not args.no_cache
    show_build_steps = args.show_build_steps

    if not shutil.which("docker"):
        console.print("[red]Error:[/red] docker is required but not found in PATH")
        _exit(1)

    # Build the GitHub client
    if args.no_gh:
        if not args.github_token:
            console.print(
                "[red]Error:[/red] --no-gh requires a GitHub token. "
                "Pass --github-token TOKEN or set the GITHUB_TOKEN environment variable."
            )
            _exit(1)
        gh = GitHubClient(token=args.github_token)
    else:
        if not shutil.which("gh"):
            console.print(
                "[red]Error:[/red] gh (GitHub CLI) is not installed. "
                "Either install gh or use --no-gh with a --github-token."
            )
            _exit(1)
        gh = GitHubClient(token=args.github_token)

    if args.from_pr:
        action_refs = extract_action_refs_from_pr(args.from_pr, gh=gh)
        if not action_refs:
            console.print(f"[red]Error:[/red] could not extract action reference from PR #{args.from_pr}")
            _exit(1)
        for ref in action_refs:
            console.print(f"  Extracted action reference from PR #{args.from_pr}: [bold]{ref}[/bold]")
        passed = all(verify_single_action(ref, gh=gh, ci_mode=ci_mode, cache=cache, show_build_steps=show_build_steps) for ref in action_refs)
        _exit(0 if passed else 1)
    elif args.check_dependabot_prs:
        check_dependabot_prs(gh=gh, cache=cache, show_build_steps=show_build_steps)
    elif args.action_ref:
        passed = verify_single_action(args.action_ref, gh=gh, ci_mode=ci_mode, cache=cache, show_build_steps=show_build_steps)
        _exit(0 if passed else 1)
    else:
        parser.print_help()
        _exit(1)


if __name__ == "__main__":
    main()
