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
"""Detect and resolve source-detached release tags.

Some actions (e.g. slackapi/slack-github-action, ones using changesets'
release automation) publish their tagged commit as a rootless *orphan*
commit whose tree only contains the distributable artifacts — ``action.yml``,
``dist/``, ``LICENSE``, ``README.md``, etc — but no ``src/``, no
``package.json`` and no lock files.  Consumers pin to that commit SHA, but
there is literally no source to rebuild from, so a naive ``git checkout &&
npm run build`` produces empty output.

This module detects that pattern and resolves the corresponding *source
commit* on the default branch that was released from, so the verifier can
rebuild against real source and still diff the rebuilt ``dist/`` against the
orphan tag's published ``dist/``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone


def _gh_api(endpoint: str) -> dict | list | None:
    """Call ``gh api`` and return parsed JSON, or ``None`` on any failure."""
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _tree_top_level_names(org: str, repo: str, commit_hash: str) -> set[str]:
    """Return the set of top-level entry names in the commit's tree.

    Returns an empty set if the lookup fails — callers should treat that as
    "unknown, don't infer anything".
    """
    data = _gh_api(f"repos/{org}/{repo}/git/trees/{commit_hash}")
    if not isinstance(data, dict):
        return set()
    return {entry.get("path", "") for entry in data.get("tree", []) if entry.get("path")}


def is_source_detached(org: str, repo: str, commit_hash: str, sub_path: str = "") -> bool:
    """Return True when the commit tree lacks buildable source.

    A "source-detached" commit is one where the tagged tree contains only
    distributable artifacts — no ``package.json`` at the build root.  When
    ``sub_path`` is set (monorepo sub-action), we check that sub-tree; else
    the repo root.  The heuristic is intentionally narrow: we only flag
    commits that *also* contain a ``dist/`` directory, so repos that simply
    don't use a build step (composite/docker actions) aren't false-positived.
    """
    # Monorepo sub-actions typically keep their build tooling at the repo
    # root, so a sub_path without package.json is expected, not source-
    # detached.  Limit this heuristic to the top-level case for now.
    if sub_path:
        return False

    names = _tree_top_level_names(org, repo, commit_hash)
    if not names:
        return False

    has_dist = "dist" in names
    has_pkg = "package.json" in names
    has_src_tree = "src" in names
    return has_dist and not has_pkg and not has_src_tree


def _find_tags_for_commit(org: str, repo: str, commit_hash: str) -> list[str]:
    """Return every tag name that points at ``commit_hash``, most specific first.

    Actions often attach both a pinned version tag (``v3.0.2``) and rolling
    major/minor tags (``v3``, ``v3.0``) to the same commit.  Only the pinned
    version has its own GitHub Release, so we return all matches and sort by
    specificity — longest tag name first, which for semver-like schemes is a
    good proxy for "more specific" (``v3.0.2`` beats ``v3``).
    """
    matches: list[str] = []
    for page in range(1, 6):
        data = _gh_api(
            f"repos/{org}/{repo}/git/matching-refs/tags?per_page=100&page={page}"
        )
        if not isinstance(data, list) or not data:
            break
        for ref in data:
            obj = ref.get("object", {})
            obj_sha = obj.get("sha")
            obj_type = obj.get("type")
            ref_name = ref.get("ref", "")
            if not ref_name.startswith("refs/tags/"):
                continue
            tag_name = ref_name[len("refs/tags/"):]
            if obj_sha == commit_hash:
                matches.append(tag_name)
                continue
            if obj_type == "tag":
                # Annotated tag — the ref points at a tag object whose
                # .object.sha is the actual commit.  Fetch and check.
                tag_obj = _gh_api(f"repos/{org}/{repo}/git/tags/{obj_sha}")
                if isinstance(tag_obj, dict):
                    inner = tag_obj.get("object", {})
                    if inner.get("sha") == commit_hash:
                        matches.append(tag_name)
        if len(data) < 100:
            break
    matches.sort(key=lambda t: (-len(t), t))
    return matches


def _release_published_at(org: str, repo: str, tag_name: str) -> datetime | None:
    """Return the release's published_at timestamp for the given tag, or None."""
    data = _gh_api(f"repos/{org}/{repo}/releases/tags/{tag_name}")
    if not isinstance(data, dict):
        return None
    ts = data.get("published_at") or data.get("created_at")
    if not ts:
        return None
    try:
        # GitHub returns ISO8601 with trailing Z.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _default_branch(org: str, repo: str) -> str:
    """Return the repo's default branch name (falls back to ``main``)."""
    data = _gh_api(f"repos/{org}/{repo}")
    if isinstance(data, dict):
        br = data.get("default_branch")
        if isinstance(br, str) and br:
            return br
    return "main"


def _commit_has_package_json(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> bool:
    """Cheap tree check: does this commit have a buildable package.json?"""
    if sub_path:
        data = _gh_api(f"repos/{org}/{repo}/contents/{sub_path}?ref={commit_hash}")
        if not isinstance(data, list):
            return False
        return any(e.get("name") == "package.json" for e in data)
    names = _tree_top_level_names(org, repo, commit_hash)
    return "package.json" in names


def resolve_source_commit(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> tuple[str, str] | None:
    """Resolve the default-branch source commit that a source-detached tag was cut from.

    Returns ``(source_commit_sha, tag_name)`` on success, or ``None`` if we
    couldn't confidently identify the source commit.

    Strategy:
      1. Find the tag name(s) that point at ``commit_hash``.
      2. Look up the GitHub Release object for that tag — use its
         ``published_at`` as a time anchor.
      3. List commits on the default branch at or just before that time.
      4. Pick the most recent one whose tree actually has ``package.json``
         at the build root (confirming it's buildable source).
    """
    candidate_tags = _find_tags_for_commit(org, repo, commit_hash)
    if not candidate_tags:
        return None

    tag_name = None
    published = None
    for candidate in candidate_tags:
        ts = _release_published_at(org, repo, candidate)
        if ts is not None:
            tag_name = candidate
            published = ts
            break
    if tag_name is None or published is None:
        return None

    default_branch = _default_branch(org, repo)

    # The orphan tag is typically pushed a few seconds *after* the release
    # PR lands on the default branch, so we cap the window at published_at +
    # a short tolerance to cover race conditions while keeping commits that
    # landed *after* the release (e.g. subsequent dependabot bumps) out.
    cutoff = published + timedelta(minutes=1)
    until = cutoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = _gh_api(
        f"repos/{org}/{repo}/commits?sha={default_branch}&until={until}&per_page=20"
    )
    if not isinstance(commits, list):
        return None

    # Prefer commits whose message looks like a release commit (changesets
    # uses "chore: release", release-please uses "chore(main): release
    # x.y.z", other automations use "Release …").  Fall back to the most
    # recent buildable commit in the window otherwise.
    release_markers = ("chore: release", "chore(main): release", "release:", "Release ", "Version Packages")

    def _is_release_commit(commit: dict) -> bool:
        msg = commit.get("commit", {}).get("message", "")
        first_line = msg.splitlines()[0] if msg else ""
        return any(marker.lower() in first_line.lower() for marker in release_markers)

    ordered = sorted(
        commits,
        key=lambda c: (not _is_release_commit(c), commits.index(c)),
    )
    for commit in ordered:
        sha = commit.get("sha")
        if not sha:
            continue
        if _commit_has_package_json(org, repo, sha, sub_path):
            return sha, tag_name

    return None


def _commit_committer_date(org: str, repo: str, commit_hash: str) -> datetime | None:
    """Return the committer date of ``commit_hash`` as a tz-aware datetime."""
    data = _gh_api(f"repos/{org}/{repo}/commits/{commit_hash}")
    if not isinstance(data, dict):
        return None
    ts = (
        data.get("commit", {}).get("committer", {}).get("date")
        or data.get("commit", {}).get("author", {}).get("date")
    )
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_release_or_commit_time(
    org: str, repo: str, commit_hash: str,
) -> tuple[datetime, str | None, str] | None:
    """Return when this commit was released, with the tag name if known.

    Resolution order:
      1. Find every tag pointing at ``commit_hash``; for each (most-specific
         first) try to fetch its GitHub Release and use ``published_at``.
      2. If no release exists, fall back to the commit's committer date.

    Returns ``(timestamp, tag_name, source)`` where ``source`` is either
    ``"release"`` or ``"commit"``, or ``None`` if neither lookup succeeds.
    ``tag_name`` is ``None`` when the commit isn't pointed at by any tag.
    """
    tags = _find_tags_for_commit(org, repo, commit_hash)
    for tag in tags:
        ts = _release_published_at(org, repo, tag)
        if ts is not None:
            return ts, tag, "release"

    ts = _commit_committer_date(org, repo, commit_hash)
    if ts is None:
        return None
    # Even if we have no GitHub Release, we may have a tag name to display.
    return ts, (tags[0] if tags else None), "commit"


def format_release_time(ts: datetime) -> str:
    """Format ``ts`` for the verification summary as 'Weekday YYYY-MM-DD HH:MM UTC'.

    Leads with the day of the week so the reader can eyeball how many days
    ago this was relative to today — without baking a "N days ago" string
    that silently rots when the same output is re-read later (a CI log,
    a PR-comment quote, a saved transcript). The absolute timestamp is
    self-validating; the weekday makes the recency obvious.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%A %Y-%m-%d %H:%M UTC")
