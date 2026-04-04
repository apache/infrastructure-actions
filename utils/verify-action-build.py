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
import json
import os
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
from rich.prompt import Confirm
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

    if not Confirm.ask(
        "\nWould you like to see the diff between an approved version and the one being checked?",
        console=console,
        default=True,
    ):
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
            choice = console.input(f"\nEnter number [{default_idx}]: ").strip()
            if not choice:
                return other_versions[default_idx - 1]["hash"]
            idx = int(choice) - 1
            if 0 <= idx < len(other_versions):
                return other_versions[idx]["hash"]
        except (ValueError, EOFError):
            pass
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

        if not ci_mode and not Confirm.ask("  Proceed with these exclusions?", console=console, default=True):
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
) -> tuple[Path, Path, str, str]:
    """Build the action in a Docker container and extract original + rebuilt dist.

    Returns (original_dir, rebuilt_dir, action_type, out_dir_name).
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

    return original_dir, rebuilt_dir, action_type, out_dir_name


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


def verify_single_action(
    action_ref: str, gh: GitHubClient | None = None, ci_mode: bool = False,
    cache: bool = True, show_build_steps: bool = False,
) -> bool:
    """Verify a single action reference. Returns True if verification passed."""
    org, repo, sub_path, commit_hash = parse_action_ref(action_ref)

    with tempfile.TemporaryDirectory(prefix="verify-action-") as tmp:
        work_dir = Path(tmp)
        original_dir, rebuilt_dir, action_type, out_dir_name = build_in_docker(
            org, repo, commit_hash, work_dir, sub_path=sub_path, gh=gh,
            cache=cache, show_build_steps=show_build_steps,
        )

        # Non-JavaScript actions (docker, composite) don't have compiled JS to verify
        is_js_action = action_type.startswith("node") or action_type in ("unknown",)
        if not is_js_action:
            console.print()
            console.print(
                Panel(
                    f"[yellow]This is a [bold]{action_type}[/bold] action, not a JavaScript action.\n"
                    f"Build verification of compiled JS is not applicable.[/yellow]",
                    border_style="yellow",
                    title="SKIPPED",
                )
            )
            all_match = True
        else:
            all_match = diff_js_files(
                original_dir, rebuilt_dir, org, repo, commit_hash, out_dir_name,
            )

        # Check for previously approved versions and offer to diff
        approved = find_approved_versions(org, repo)
        if approved:
            selected_hash = show_approved_versions(org, repo, commit_hash, approved, gh=gh, ci_mode=ci_mode)
            if selected_hash:
                show_commits_between(org, repo, selected_hash, commit_hash, gh=gh)
                diff_approved_vs_new(org, repo, selected_hash, commit_hash, work_dir, ci_mode=ci_mode)
        elif not is_js_action:
            console.print(
                "  [dim]No previously approved versions found — "
                "this appears to be a new action[/dim]"
            )

    console.print()
    checklist_hint = f"\n[dim]Security review checklist: {SECURITY_CHECKLIST_URL}[/dim]"
    if all_match:
        if is_js_action:
            result_msg = "[green bold]All compiled JavaScript matches the rebuild[/green bold]"
        else:
            result_msg = f"[green bold]{action_type} action — no compiled JS to verify[/green bold]"
        console.print(Panel(result_msg + checklist_hint, border_style="green", title="RESULT"))
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
    """Extract all new action org/repo[/sub]@hash refs from a dependabot PR diff.

    Returns a deduplicated list of action references found in added lines.
    """
    if gh is None:
        return []
    diff_text = gh.get_pr_diff(pr_number)
    if not diff_text:
        return []

    seen: set[str] = set()
    refs: list[str] = []
    for line in diff_text.splitlines():
        # Match lines like: +      - uses: org/repo/sub@hash  # tag
        # Also match 'use:' (common typo for 'uses:')
        match = re.search(r"^\+.*uses?:\s+([^@\s]+)@([0-9a-f]{40})", line)
        if match:
            action_path = match.group(1)
            commit_hash = match.group(2)
            ref = f"{action_path}@{commit_hash}"
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)

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

    if not Confirm.ask(
        "\n  Review these PRs?",
        console=console,
        default=True,
    ):
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
        if not Confirm.ask(
            f"\n  Merge PR {pr_link}?",
            console=console,
            default=True,
        ):
            console.print(f"  [dim]Skipped merging PR {pr_link}[/dim]")
            continue

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
