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
"""Interaction with the approved-actions database (actions.yml)."""

import re
import subprocess
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from .console import console, link, ask_confirm, UserQuit
from .github_client import GitHubClient

# Path to the actions.yml file relative to the package
ACTIONS_YML = Path(__file__).resolve().parent.parent.parent / "actions.yml"


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

        if line and not line[0].isspace() and not line.startswith("#"):
            in_action = stripped == action_key
            current_hash = None
            continue

        if not in_action:
            continue

        if line.startswith("  ") and not line.startswith("    "):
            key = stripped.rstrip(":")
            clean_key = key.strip("'\"")
            if re.match(r"^[0-9a-f]{40}$", clean_key):
                current_hash = clean_key
                approved.append({"hash": current_hash})
            else:
                current_hash = None
            continue

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
    result = subprocess.run(
        ["git", "log", "--all", "--format=%H|%aI|%an|%s", f"-S{action_hash}", "--", "actions.yml"],
        capture_output=True,
        text=True,
        cwd=ACTIONS_YML.parent,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

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

    other_versions = [v for v in approved if v["hash"] != new_hash]
    if not other_versions:
        return None

    if ci_mode:
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

    if len(other_versions) == 1:
        selected = other_versions[0]
        console.print(
            f"  Using approved version: [cyan]{selected.get('tag', '')}[/cyan] "
            f"({selected['hash'][:12]})"
        )
        return selected["hash"]

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
