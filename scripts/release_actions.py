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
"""Cut per-action, path-prefixed releases for infrastructure-actions.

This repository serves several independently-consumed actions from a single
repo (a monorepo of actions).  To let downstream projects pin a specific
version -- and let Dependabot propose bumps -- each action is released under
its own *path-prefixed* tag, e.g. ``allowlist-check/v1.2.3`` or
``restore/v1.4.0``.  Consumers then pin::

    - uses: apache/infrastructure-actions/stash/restore@<sha>  # restore/v1.4.0

Dependabot's ``github_actions`` ecosystem understands this leaf-name prefix
scheme (dependabot/dependabot-core#11286, added specifically for this repo),
matching candidate tags by the ``restore/`` prefix so each action bumps
independently.

The module keeps its decision logic (which actions changed, what the next
version is, which bump to apply) as pure functions so they can be unit
tested; all git/GitHub side effects live in ``main`` and the small ``_run``
helpers and only fire with ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Action:
    """A releasable action served from this repo.

    ``tag_prefix`` is the *leaf* directory name used as the tag/ref prefix
    (the format Dependabot matches).  ``consumed_path`` is how downstream
    workflows reference the action.  ``watch_paths`` are the repo-relative
    path prefixes whose changes warrant a new release of this action --
    including any shared code the action depends on at runtime.
    """

    tag_prefix: str
    consumed_path: str
    watch_paths: tuple[str, ...] = field(default_factory=tuple)


# The actions this repo publishes.  ``stash/save`` and ``stash/restore`` both
# import ``stash/shared/mung.py`` at runtime (PYTHONPATH -> ../shared), so a
# change there releases both.
ACTIONS: tuple[Action, ...] = (
    Action("allowlist-check", "allowlist-check", ("allowlist-check/",)),
    Action("pelican", "pelican", ("pelican/",)),
    Action("save", "stash/save", ("stash/save/", "stash/shared/")),
    Action("restore", "stash/restore", ("stash/restore/", "stash/shared/")),
)

VALID_BUMPS = ("major", "minor", "patch")


# --------------------------------------------------------------------------- #
# Pure logic (unit tested)
# --------------------------------------------------------------------------- #
def affected_actions(
    changed_files: list[str], actions: tuple[Action, ...] = ACTIONS
) -> list[Action]:
    """Return the actions whose ``watch_paths`` match any changed file."""
    hit: list[Action] = []
    for action in actions:
        if any(
            f.startswith(prefix)
            for f in changed_files
            for prefix in action.watch_paths
        ):
            hit.append(action)
    return hit


def parse_version(tag: str, prefix: str) -> tuple[int, int, int] | None:
    """Parse ``<prefix>/vX.Y.Z`` -> ``(X, Y, Z)``; ``None`` if it doesn't match.

    Only fully-specified ``X.Y.Z`` tags are treated as releases; the moving
    major tag ``<prefix>/vX`` is intentionally ignored here.
    """
    m = re.fullmatch(rf"{re.escape(prefix)}/v(\d+)\.(\d+)\.(\d+)", tag)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def latest_version(
    tags: list[str], prefix: str
) -> tuple[int, int, int] | None:
    """Highest ``X.Y.Z`` among ``tags`` for ``prefix``; ``None`` if none."""
    versions = [v for v in (parse_version(t, prefix) for t in tags) if v]
    return max(versions) if versions else None


def bump_version(
    current: tuple[int, int, int] | None, bump: str
) -> tuple[int, int, int]:
    """Apply ``bump`` to ``current`` (``None`` => first release ``1.0.0``)."""
    if bump not in VALID_BUMPS:
        raise ValueError(f"invalid bump {bump!r}; expected one of {VALID_BUMPS}")
    if current is None:
        # Seed the first release at v1.0.0 so the moving major tag is vN>=1.
        return (1, 0, 0)
    major, minor, patch = current
    if bump == "major":
        return (major + 1, 0, 0)
    if bump == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def format_tag(prefix: str, version: tuple[int, int, int]) -> str:
    return f"{prefix}/v{version[0]}.{version[1]}.{version[2]}"


def format_major_tag(prefix: str, version: tuple[int, int, int]) -> str:
    return f"{prefix}/v{version[0]}"


def determine_bump(
    labels: list[str], commit_message: str = ""
) -> str | None:
    """Decide the bump for a merge from PR labels / commit message.

    Precedence (highest first):
      * a skip request -> ``None`` (no release)
      * ``release:major`` label or ``[major]`` in the message
      * ``release:minor`` label or ``[minor]`` in the message
      * ``release:patch`` label or ``[patch]`` in the message
      * default -> ``"patch"``
    """
    label_set = {label.strip().lower() for label in labels}
    msg = commit_message.lower()

    if "release:skip" in label_set or "[skip release]" in msg or "[no release]" in msg:
        return None
    if "release:major" in label_set or "[major]" in msg:
        return "major"
    if "release:minor" in label_set or "[minor]" in msg:
        return "minor"
    if "release:patch" in label_set or "[patch]" in msg:
        return "patch"
    return "patch"


# --------------------------------------------------------------------------- #
# git / GitHub side effects
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> str:
    result = subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if capture and result.returncode != 0 and not check:
        return ""
    return (result.stdout or "").strip()


def changed_files_in_range(before: str, after: str) -> list[str]:
    """Files changed between two commits.

    Falls back to the diff of ``after`` against its first parent when
    ``before`` is unusable (all-zero sha of a brand-new ref, or missing).
    """
    zero = re.fullmatch(r"0+", before or "")
    if not before or zero:
        out = _run(["git", "diff", "--name-only", f"{after}^", after], check=False)
    else:
        out = _run(["git", "diff", "--name-only", f"{before}..{after}"], check=False)
    return [line for line in out.splitlines() if line.strip()]


def existing_tags() -> list[str]:
    return [t for t in _run(["git", "tag", "--list"]).splitlines() if t.strip()]


def pr_labels_for_commit(sha: str, repo: str) -> tuple[list[str], str]:
    """Return ``(labels, title)`` of the PR that produced ``sha`` (best effort)."""
    out = _run(
        [
            "gh",
            "api",
            f"repos/{repo}/commits/{sha}/pulls",
            "--jq",
            "[.[] | {labels: [.labels[].name], title: .title}]",
        ],
        check=False,
    )
    if not out:
        return [], ""
    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return [], ""
    if not prs:
        return [], ""
    first = prs[0]
    return list(first.get("labels", [])), str(first.get("title", ""))


def create_release(
    action: Action,
    version: tuple[int, int, int],
    sha: str,
    repo: str,
    *,
    apply: bool,
) -> None:
    """Create the ``X.Y.Z`` tag, move the major tag, publish a GitHub release."""
    version_tag = format_tag(action.tag_prefix, version)
    major_tag = format_major_tag(action.tag_prefix, version)
    title = f"{action.consumed_path} {version_tag.split('/', 1)[1]}"

    print(f"  -> {version_tag}  (major {major_tag})  @ {sha[:12]}")
    if not apply:
        print("     [dry-run] skipping tag/push/release")
        return

    # Annotated version tag (immutable), pushed once.
    _run(["git", "tag", "-a", version_tag, "-m", title, sha])
    _run(["git", "push", "origin", version_tag])

    # Moving major tag -- force-updated to the newest release of this action.
    _run(["git", "tag", "-f", "-a", major_tag, "-m", f"{action.consumed_path} {major_tag.split('/', 1)[1]}", sha])
    _run(["git", "push", "--force", "origin", major_tag])

    # GitHub Release with auto-generated notes for the version tag.
    _run(
        [
            "gh",
            "release",
            "create",
            version_tag,
            "--repo",
            repo,
            "--title",
            title,
            "--generate-notes",
            "--target",
            sha,
        ],
        check=False,
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="", help="previous commit sha (github.event.before)")
    parser.add_argument("--after", default="", help="pushed commit sha (github.sha)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name")
    parser.add_argument(
        "--bump",
        choices=VALID_BUMPS,
        default=None,
        help="force a bump type instead of inferring from PR labels/commit message",
    )
    parser.add_argument(
        "--action",
        action="append",
        default=None,
        help="restrict to a specific action tag_prefix (repeatable); default: auto-detect changed",
    )
    parser.add_argument("--apply", action="store_true", help="actually create tags/releases")
    args = parser.parse_args(argv)

    after = args.after or _run(["git", "rev-parse", "HEAD"])
    if not args.repo:
        print("error: --repo or GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2

    # 1. Which actions changed?
    if args.action:
        selected = {a.lower() for a in args.action}
        actions = [a for a in ACTIONS if a.tag_prefix.lower() in selected]
        print(f"Releasing explicitly requested actions: {sorted(selected)}")
    else:
        changed = changed_files_in_range(args.before, after)
        actions = affected_actions(changed)
        print(f"{len(changed)} file(s) changed; affected actions: "
              f"{[a.tag_prefix for a in actions] or 'none'}")

    if not actions:
        print("No releasable actions changed; nothing to do.")
        return 0

    # 2. What bump?
    if args.bump:
        bump = args.bump
        print(f"Bump forced to: {bump}")
    else:
        labels, title = pr_labels_for_commit(after, args.repo)
        commit_msg = _run(["git", "log", "-1", "--pretty=%B", after], check=False)
        bump = determine_bump(labels, f"{title}\n{commit_msg}")
        print(f"PR labels: {labels or 'none'} -> bump: {bump or 'SKIP'}")

    if bump is None:
        print("Release skipped by request (release:skip / [skip release]).")
        return 0

    # 3. Cut a release per affected action.
    tags = existing_tags()
    for action in actions:
        current = latest_version(tags, action.tag_prefix)
        new_version = bump_version(current, bump)
        cur_str = format_tag(action.tag_prefix, current) if current else "(none)"
        print(f"{action.consumed_path}: {cur_str} --{bump}--> "
              f"{format_tag(action.tag_prefix, new_version)}")
        create_release(action, new_version, after, args.repo, apply=args.apply)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
