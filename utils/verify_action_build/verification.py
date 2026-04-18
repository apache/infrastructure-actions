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
"""Verification orchestration and summary display."""

import tempfile
import webbrowser
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from .action_ref import fetch_file_from_github, parse_action_ref
from .approved_actions import find_approved_versions, show_approved_versions, show_commits_between
from .console import console
from .diff_js import diff_js_files
from .diff_node_modules import diff_node_modules
from .diff_source import diff_approved_vs_new
from .docker_build import build_in_docker
from .github_client import GitHubClient
from .security import (
    analyze_action_metadata,
    analyze_dependency_pinning,
    analyze_dockerfile,
    analyze_nested_actions,
    analyze_repo_metadata,
    analyze_scripts,
)

SECURITY_CHECKLIST_URL = "https://github.com/apache/infrastructure-actions#security-review-checklist"


def show_verification_summary(
    org: str, repo: str, commit_hash: str, sub_path: str,
    action_type: str, is_js_action: bool, all_match: bool,
    non_js_warnings: list[str] | None,
    checked_actions: list[dict] | None,
    checks_performed: list[tuple[str, str, str]],
    ci_mode: bool = False,
) -> None:
    """Show a structured summary of all checks performed."""
    console.print()
    console.rule("[bold]Verification Summary[/bold]")

    display_name = f"{org}/{repo}"
    if sub_path:
        display_name += f"/{sub_path}"

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


def offer_open_and_approve(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    ci_mode: bool = False,
) -> str | None:
    """Offer to open the action in a browser and/or approve it.

    Returns "approve" if the user chose to approve, None otherwise.
    """
    if ci_mode:
        return None

    action_url = f"https://github.com/{org}/{repo}/tree/{commit_hash}"
    if sub_path:
        action_url += f"/{sub_path}"

    urls = [
        ("Source tree", action_url),
        ("Commit", f"https://github.com/{org}/{repo}/commit/{commit_hash}"),
        ("Repository", f"https://github.com/{org}/{repo}"),
    ]

    console.print()
    console.print("[bold]Quick links:[/bold]")
    for label, url in urls:
        console.print(f"  [link={url}]{label}: {url}[/link]")

    console.print()
    console.print(
        "  [bold]o[/bold] = open in browser, "
        "[bold]a[/bold] = approve action, "
        "[bold]Enter[/bold]/[bold]q[/bold] = done"
    )

    while True:
        try:
            choice = console.input("  > ").strip().lower()
        except EOFError:
            break
        if choice == "o":
            webbrowser.open(action_url)
            console.print("  [dim]Opened in browser[/dim]")
            continue
        if choice == "a":
            return "approve"
        break

    return None


def verify_single_action(
    action_ref: str, gh: GitHubClient | None = None, ci_mode: bool = False,
    cache: bool = True, show_build_steps: bool = False,
) -> bool:
    """Verify a single action reference. Returns True if verification passed."""
    org, repo, sub_path, commit_hash = parse_action_ref(action_ref)

    # Look up approved versions early — used for the lock-file retry and the
    # later approved-version diff section.
    approved = find_approved_versions(org, repo)

    checks_performed: list[tuple[str, str, str]] = []
    non_js_warnings: list[str] = []
    checked_actions: list[dict] = []
    matched_with_approved_lockfile = False

    with tempfile.TemporaryDirectory(prefix="verify-action-") as tmp:
        work_dir = Path(tmp)
        (original_dir, rebuilt_dir, action_type, out_dir_name,
         has_node_modules, original_node_modules, rebuilt_node_modules) = build_in_docker(
            org, repo, commit_hash, work_dir, sub_path=sub_path, gh=gh,
            cache=cache, show_build_steps=show_build_steps,
        )

        checks_performed.append(("Action type detection", "info", action_type))

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

            metadata_warnings = analyze_action_metadata(org, repo, commit_hash, sub_path)
            non_js_warnings.extend(metadata_warnings)
            checks_performed.append((
                "Action metadata (shell/env/secrets)",
                "warn" if metadata_warnings else "pass",
                f"{len(metadata_warnings)} warning(s)" if metadata_warnings else "no issues",
            ))

            repo_warnings = analyze_repo_metadata(org, repo, commit_hash)
            non_js_warnings.extend(repo_warnings)
            checks_performed.append((
                "Repository metadata",
                "warn" if repo_warnings else "pass",
                f"{len(repo_warnings)} warning(s)" if repo_warnings else "ok",
            ))

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

            # If no compiled JS was found in dist/ but node_modules is vendored,
            # verify node_modules instead
            if has_node_modules:
                nm_match = diff_node_modules(
                    original_node_modules, rebuilt_node_modules,
                    org, repo, commit_hash,
                )
                all_match = all_match and nm_match

            if not all_match and approved:
                # The rebuild produced different JS.  This may be caused by a
                # dev-dependency bump (e.g. rollup, ncc, webpack) where the
                # committed dist/ was built with the *previous* toolchain but
                # the lock file now pins a newer version.
                # Retry the build using the approved version's lock files to
                # diagnose *why* the rebuild differs — but a match under those
                # conditions is still reported as a hard failure, because the
                # committed dist/ does not match a clean rebuild from the
                # current lock files.
                prev_hash = approved[0]["hash"]
                console.print()
                console.print(
                    Panel(
                        f"[yellow]JS mismatch detected — retrying build with dev-dependency "
                        f"lock files from the previously approved commit "
                        f"[bold]{prev_hash[:12]}[/bold] to check whether the difference "
                        f"is caused by a toolchain version bump.[/yellow]",
                        border_style="yellow",
                        title="RETRY WITH APPROVED LOCK FILES",
                    )
                )

                retry_dir = work_dir / "retry"
                retry_dir.mkdir(exist_ok=True)
                (retry_orig, retry_rebuilt, _, _, retry_has_nm,
                 retry_orig_nm, retry_rebuilt_nm) = build_in_docker(
                    org, repo, commit_hash, retry_dir, sub_path=sub_path, gh=gh,
                    cache=cache, show_build_steps=show_build_steps,
                    approved_hash=prev_hash,
                )

                retry_match = diff_js_files(
                    retry_orig, retry_rebuilt, org, repo, commit_hash, out_dir_name,
                )
                if retry_has_nm:
                    retry_nm = diff_node_modules(
                        retry_orig_nm, retry_rebuilt_nm,
                        org, repo, commit_hash,
                    )
                    retry_match = retry_match and retry_nm

                if retry_match:
                    matched_with_approved_lockfile = True
                    console.print()
                    console.print(
                        Panel(
                            "[red bold]The compiled JS only matches when rebuilt with the "
                            "previously approved version's dev-dependency lock files.[/red bold]\n\n"
                            "This means the action's [bold]devDependencies[/bold] (build toolchain) "
                            "changed between versions, but the committed dist/ was built with the "
                            "old toolchain — so a clean rebuild from the current lock files does "
                            "[bold]not[/bold] reproduce the committed output.\n\n"
                            "[bold]Required action:[/bold] the action maintainer must rebuild "
                            "dist/ with the current lock files and recommit, or roll back the "
                            "devDependency changes.  This is reported as a failure.",
                            border_style="red",
                            title="MATCHED ONLY WITH APPROVED LOCK FILES",
                        )
                    )

            if all_match:
                js_status, js_detail = "pass", "compiled JS matches rebuild"
            elif matched_with_approved_lockfile:
                js_status, js_detail = (
                    "fail",
                    "only matches with approved lock files (devDeps changed)",
                )
            else:
                js_status, js_detail = "fail", "DIFFERENCES DETECTED"
            checks_performed.append(("JS build verification", js_status, js_detail))

        # Check for previously approved versions and offer to diff
        # (reuse the list fetched earlier for the approved_hash build arg)
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

    show_verification_summary(
        org, repo, commit_hash, sub_path,
        action_type, is_js_action, all_match,
        non_js_warnings if not is_js_action else None,
        checked_actions if checked_actions else None,
        checks_performed,
        ci_mode=ci_mode,
    )

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
        if matched_with_approved_lockfile:
            fail_msg = (
                "[red bold]Compiled JS only matches when rebuilt with the "
                "previously approved version's lock files — devDependencies "
                "changed and dist/ was not rebuilt[/red bold]"
            )
        else:
            fail_msg = "[red bold]Differences detected between published and rebuilt JS[/red bold]"
        console.print(
            Panel(
                fail_msg + checklist_hint,
                border_style="red",
                title="RESULT",
            )
        )

    offer_open_and_approve(org, repo, commit_hash, sub_path, ci_mode=ci_mode)

    return all_match
