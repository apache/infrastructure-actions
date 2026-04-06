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
"""CLI argument parsing and entry point."""

import argparse
import os
import shutil
import sys

from .console import console
from .dependabot import check_dependabot_prs
from .github_client import GitHubClient
from .pr_extraction import extract_action_refs_from_pr
from .verification import SECURITY_CHECKLIST_URL, verify_single_action


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
