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
"""Dependabot PR review and merge workflow."""

from rich.panel import Panel
from rich.table import Table

from .action_ref import parse_action_ref
from .console import console, link, ask_confirm, UserQuit
from .github_client import GitHubClient
from .pr_extraction import extract_action_refs_from_pr
from .verification import verify_single_action


def get_gh_user(gh: GitHubClient | None = None) -> str:
    """Get the currently authenticated GitHub username."""
    if gh is None:
        return "unknown"
    return gh.get_authenticated_user()


def check_dependabot_prs(
    gh: GitHubClient, cache: bool = True, show_build_steps: bool = False,
    check_binary_downloads: bool = True,
) -> None:
    """List open dependabot PRs, verify each, and optionally merge."""
    console.print()
    console.rule("[bold]Dependabot PR Review[/bold]")

    with console.status("[bold blue]Fetching open dependabot PRs...[/bold blue]"):
        all_prs = gh.list_open_prs(author="app/dependabot")

    if not all_prs:
        console.print("[green]No open dependabot PRs found[/green]")
        return

    eligible_prs: list[dict] = []
    excluded_prs: list[tuple[dict, str]] = []

    for pr in all_prs:
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            excluded_prs.append((pr, "changes requested by reviewer"))
            continue

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

        with console.status("[bold blue]Extracting action references from PR...[/bold blue]"):
            action_refs = extract_action_refs_from_pr(pr["number"], gh=gh)

        if not action_refs:
            console.print(
                f"  [yellow]Could not extract action reference from PR {pr_link} — skipping[/yellow]"
            )
            continue

        for ref in action_refs:
            console.print(f"  Action: [bold]{ref}[/bold]")

        refs_by_base: dict[str, list[str]] = {}
        for ref in action_refs:
            org, repo, sub_path, commit_hash = parse_action_ref(ref)
            base_key = f"{org}/{repo}@{commit_hash}"
            refs_by_base.setdefault(base_key, []).append(sub_path)

        passed = True
        for base_key, sub_paths in refs_by_base.items():
            org_repo, commit_hash = base_key.rsplit("@", 1)
            if any(sub_paths):
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
                    if not verify_single_action(
                        sub_ref, gh=gh, cache=cache, show_build_steps=show_build_steps,
                        check_binary_downloads=check_binary_downloads,
                    ):
                        passed = False
            else:
                if not verify_single_action(
                    f"{org_repo}@{commit_hash}", gh=gh, cache=cache,
                    show_build_steps=show_build_steps,
                    check_binary_downloads=check_binary_downloads,
                ):
                    passed = False

        if not passed:
            console.print(
                f"\n  [red]Verification failed for PR {pr_link} — skipping merge[/red]"
            )
            failed.append(pr)
            continue

        try:
            if not ask_confirm(f"\n  Merge PR {pr_link}?"):
                console.print(f"  [dim]Skipped merging PR {pr_link}[/dim]")
                continue
        except UserQuit:
            console.print(f"  [dim]Quitting review[/dim]")
            break

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
