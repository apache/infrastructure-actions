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

"""Check that all GitHub Actions uses: refs are on the ASF allowlist.

Usage:
    python3 check_asf_allowlist.py <allowlist_path>

The allowlist is the approved_patterns.yml file colocated at the root of
this repository (../approved_patterns.yml relative to this script).

The glob pattern for YAML files to scan can be overridden via the
GITHUB_YAML_GLOB environment variable (default: .github/**/*.yml).

Exits with code 1 if any action ref is not allowlisted.
"""

import datetime
import fnmatch
import glob
import os
import shlex
import sys
from typing import Any, Generator

import ruyaml

# actions/*, github/*, apache/* are implicitly trusted by GitHub/ASF
# See ../README.md ("Management of Organization-wide GitHub Actions Allow List")
TRUSTED_OWNERS = {"actions", "github", "apache"}

# Default glob pattern for YAML files to scan for action refs
DEFAULT_GITHUB_YAML_GLOB = ".github/**/*.yml"

# Prefixes that indicate local or non-GitHub refs (not subject to allowlist)
# ./  — local composite actions within the same repo
# docker:// — container actions pulled directly from a registry
SKIPPED_PREFIXES = ("./", "docker://")

# YAML key that references a GitHub Action
USES_KEY = "uses"

# How many days before an allowlisted pin's expiry to start warning about it.
DEFAULT_EXPIRY_WARNING_DAYS = 30


def find_action_refs(node: Any) -> Generator[str, None, None]:
    """Recursively find all `uses:` values from a parsed YAML tree.

    Args:
        node: A parsed YAML node (any type returned by ruyaml)

    Yields:
        str: Each `uses:` string value found in the tree
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == USES_KEY and isinstance(value, str):
                yield value
            else:
                yield from find_action_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from find_action_refs(item)


def collect_action_refs(
    scan_glob: str = DEFAULT_GITHUB_YAML_GLOB,
) -> dict[str, list[str]]:
    """Collect all third-party action refs from YAML files.

    Skips local (./) and Docker (docker://) refs, as these are not
    subject to the org-level allowlist.

    Args:
        scan_glob: Glob pattern for files to scan.

    Returns:
        dict: Mapping of each action ref to the list of file paths that use it.
    """

    action_refs = {}
    for filepath in sorted(glob.glob(scan_glob, recursive=True)):
        try:
            yaml = ruyaml.YAML()
            with open(filepath) as f:
                content = yaml.load(f)
        except ruyaml.YAMLError as exc:
            print(f"::error file={filepath}::Failed to parse YAML: {exc}")
            sys.exit(1)
        if not content:
            continue
        for ref in find_action_refs(content):
            if ref.startswith(SKIPPED_PREFIXES):
                continue
            action_refs.setdefault(ref, []).append(filepath)
    return action_refs


def load_allowlist(allowlist_path: str) -> list[str]:
    """Load the ASF approved_patterns.yml file.

    The file is a flat YAML list of entries like:
      - owner/action@<sha>       (exact SHA match)
      - owner/action@*           (any ref allowed)
      - golangci/*@*             (any repo under owner, any ref)

    Python's fnmatch.fnmatch matches "/" with "*" (unlike shell globs),
    so these patterns work directly without transformation.

    Args:
        allowlist_path: Path to the approved_patterns.yml file

    Returns:
        list[str]: List of allowlist patterns (empty list if file is empty)
    """
    yaml = ruyaml.YAML()
    with open(allowlist_path) as f:
        result = yaml.load(f)
    return result if result else []


def is_allowed(action_ref: str, allowlist: list[str]) -> bool:
    """Check whether a single action ref is allowed.

    An action ref is allowed if its owner is in TRUSTED_OWNERS or it
    matches any pattern in the allowlist via fnmatch.

    Args:
        action_ref: The action reference string (e.g., "owner/action@ref")
        allowlist: List of allowlist patterns to match against

    Returns:
        bool: True if the action ref is allowed
    """
    owner = action_ref.split("/")[0]
    if owner in TRUSTED_OWNERS:
        return True
    return any(fnmatch.fnmatch(action_ref, pattern) for pattern in allowlist)


def load_expiry_map(actions_path: str) -> dict[str, datetime.date]:
    """Map each exactly-pinned, expiring allowlist ref to its expiry date.

    Parses ``actions.yml`` (the source of truth, which -- unlike
    ``approved_patterns.yml`` -- carries ``expires_at`` metadata) and returns a
    mapping of ``owner/action@<sha>`` -> expiry date for every ref that has an
    ``expires_at``. Wildcard / ``keep`` entries never expire and are omitted.

    Returns an empty dict if the file is missing or unparseable, so expiry
    warnings are strictly best-effort and never break the check.

    Args:
        actions_path: Path to the ``actions.yml`` source file.

    Returns:
        dict: Mapping of ``owner/action@ref`` to its expiry ``datetime.date``.
    """
    try:
        yaml = ruyaml.YAML()
        with open(actions_path) as f:
            actions = yaml.load(f)
    except (OSError, ruyaml.YAMLError):
        return {}
    if not actions:
        return {}

    expiry: dict[str, datetime.date] = {}
    for name, refs in actions.items():
        if not isinstance(refs, dict):
            continue
        for ref, details in refs.items():
            if not isinstance(details, dict):
                continue
            when = details.get("expires_at")
            if isinstance(when, datetime.date):
                expiry[f"{name}@{ref}"] = when
            elif isinstance(when, str):
                try:
                    expiry[f"{name}@{ref}"] = datetime.date.fromisoformat(when)
                except ValueError:
                    continue
    return expiry


def upcoming_expiry_warnings(
    action_refs: dict[str, list[str]],
    expiry_map: dict[str, datetime.date],
    warning_days: int,
    today: datetime.date,
) -> list[tuple[str, str, datetime.date, int]]:
    """Find caller refs whose allowlisted pin expires within ``warning_days``.

    Only refs that exactly match an expiring ``actions.yml`` entry are
    considered (a ref allowed via a ``@*`` wildcard has no expiry).

    Args:
        action_refs: Mapping of action ref -> files that use it.
        expiry_map: Output of :func:`load_expiry_map`.
        warning_days: Warn when a pin expires within this many days.
        today: Reference date for the countdown (injected for testability).

    Returns:
        list: ``(filepath, action_ref, expiry_date, days_left)`` tuples, one per
        (ref, file) pair, soonest expiry first. ``days_left`` is negative for a
        pin that is already past its expiry but still listed.
    """
    warnings: list[tuple[str, str, datetime.date, int]] = []
    for ref, filepaths in action_refs.items():
        expiry_date = expiry_map.get(ref)
        if expiry_date is None:
            continue
        days_left = (expiry_date - today).days
        if days_left <= warning_days:
            for filepath in filepaths:
                warnings.append((filepath, ref, expiry_date, days_left))
    warnings.sort(key=lambda w: (w[3], w[1], w[0]))
    return warnings


def build_gh_pr_command(action_name: str, refs: list[str], repo_name: str) -> str:
    """Build a shell command that creates a PR adding one action to the allowlist.

    The generated script forks ``apache/infrastructure-actions``, inserts
    pinned version entries into ``actions.yml`` in alphabetical order, and
    opens a pull request — all via the ``gh`` CLI with no manual file editing
    required.

    Args:
        action_name: The action name (e.g. ``"owner/action"``).
        refs: Full action refs for this action (e.g. ``["owner/action@sha"]``).
        repo_name: Value of ``$GITHUB_REPOSITORY`` (may be empty).

    Returns:
        str: A copy-pasteable shell script.
    """
    branch = f"allowlist-add-{action_name.replace('/', '-')}"
    title = f"Add {action_name} to the GitHub Actions allowlist"

    body_lines = [f"Add `{action_name}` to the allowlist:", ""]
    for ref in sorted(refs):
        body_lines.append(f"- `{ref}`")
    if repo_name:
        body_lines.extend(["", f"Needed by: `{repo_name}`"])
    body = "\n".join(body_lines)

    ref_args = " ".join(shlex.quote(r) for r in sorted(refs))

    inserter_url = (
        "https://raw.githubusercontent.com/apache/infrastructure-actions/"
        "main/allowlist-check/insert_actions.py"
    )

    return (
        f"( set -e; _d=$(mktemp -d); trap 'rm -rf \"$_d\"' EXIT; cd \"$_d\"\n"
        f"  gh repo fork apache/infrastructure-actions --clone -- --depth=1\n"
        f"  cd infrastructure-actions\n"
        f"  git checkout -b {shlex.quote(branch)}\n"
        f"  curl -fsSL {shlex.quote(inserter_url)} | python3 - actions.yml {ref_args}\n"
        f"  git add actions.yml\n"
        f"  git commit -m {shlex.quote(f'Add {action_name} to allowlist')}\n"
        f"  git push -u origin {shlex.quote(branch)}\n"
        f"  gh pr create --repo apache/infrastructure-actions --head \"$(gh api user -q .login):{shlex.quote(branch)}\""
        f" --title {shlex.quote(title)}"
        f" --body {shlex.quote(body)} )\n"
    )


def emit_expiry_warnings(
    action_refs: dict[str, list[str]], actions_path: str
) -> None:
    """Print GitHub ``::warning::`` annotations for soon-to-expire pins.

    Best-effort and never fatal: a missing/unparseable ``actions.yml`` or a bad
    ``EXPIRY_WARNING_DAYS`` value simply yields no warnings.
    """
    try:
        warning_days = int(
            os.environ.get("EXPIRY_WARNING_DAYS", "") or DEFAULT_EXPIRY_WARNING_DAYS
        )
    except ValueError:
        warning_days = DEFAULT_EXPIRY_WARNING_DAYS

    expiry_map = load_expiry_map(actions_path)
    warnings = upcoming_expiry_warnings(
        action_refs, expiry_map, warning_days, datetime.date.today()
    )
    if not warnings:
        return

    print(
        f"\n{len(warnings)} allowlisted ref(s) expiring within {warning_days} day(s) "
        "-- bump these to a newer approved version before they are removed:"
    )
    for filepath, ref, expiry_date, days_left in warnings:
        if days_left < 0:
            detail = (
                f"its approved pin EXPIRED on {expiry_date.isoformat()} "
                f"({-days_left} day(s) ago)"
            )
        else:
            detail = (
                f"its approved pin expires on {expiry_date.isoformat()} "
                f"(in {days_left} day(s))"
            )
        print(
            f"::warning file={filepath}::{ref} is allowlisted but {detail}; "
            "bump to a newer approved version to avoid a future CI failure."
        )


def main():
    if len(sys.argv) not in (2, 3):
        print(
            f"Usage: {sys.argv[0]} <allowlist_path> [actions_yml_path]",
            file=sys.stderr,
        )
        sys.exit(2)

    allowlist_path = sys.argv[1]
    actions_path = sys.argv[2] if len(sys.argv) == 3 else None
    allowlist = load_allowlist(allowlist_path)
    scan_glob = os.environ.get("GITHUB_YAML_GLOB", DEFAULT_GITHUB_YAML_GLOB)
    action_refs = collect_action_refs(scan_glob)

    print(f"Checking {len(action_refs)} unique action ref(s) against the ASF allowlist:\n")
    violations = []
    for action_ref, filepaths in sorted(action_refs.items()):
        allowed = is_allowed(action_ref, allowlist)
        owner = action_ref.split("/")[0]
        if owner in TRUSTED_OWNERS:
            reason = f"trusted owner ({owner})"
        elif allowed:
            reason = "matches allowlist"
        else:
            reason = "NOT ON ALLOWLIST"
        status = "✅" if allowed else "❌"
        files_str = ", ".join(filepaths)
        print(f"  {status} {action_ref} — {reason}  ({files_str})")
        if not allowed:
            for filepath in filepaths:
                violations.append((filepath, action_ref))

    # Best-effort expiry warnings (never fail the build); shown whether or not
    # there are hard violations, so projects get advance notice to bump pins.
    if actions_path:
        emit_expiry_warnings(action_refs, actions_path)

    if violations:
        print(
            f"::error::Found {len(violations)} action ref(s) not on the ASF allowlist:"
        )
        for filepath, action_ref in violations:
            print(f"::error file={filepath}::{action_ref} is not on the ASF allowlist")
        print(
            "::error::To resolve, open a PR in apache/infrastructure-actions to add"
            " the action or version to the allowlist:"
            " https://github.com/apache/infrastructure-actions#adding-a-new-action-to-the-allow-list"
        )

        missing_refs = sorted({ref for _, ref in violations})
        repo_name = os.environ.get("GITHUB_REPOSITORY", "")

        # Group by action name so we can suggest one PR per action
        by_action: dict[str, list[str]] = {}
        for ref in missing_refs:
            name = ref.split("@")[0]
            by_action.setdefault(name, []).append(ref)

        print(
            "\n::notice::Please create one PR per action."
            " You can create the PRs by running the commands below:"
        )
        for action_name in sorted(by_action):
            script = build_gh_pr_command(action_name, by_action[action_name], repo_name)
            print(f"\n# {action_name}\n{script}")

        sys.exit(1)
    else:
        print(f"All {len(action_refs)} unique action refs are on the ASF allowlist")


if __name__ == "__main__":
    main()
