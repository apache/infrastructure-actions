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
"""Extracting action references from PR diffs."""

import re

from .github_client import GitHubClient


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

    return extract_action_refs_from_diff(diff_text)


PATTERNS_FILE = "approved_patterns.yml"

# An ``approved_patterns.yml`` entry: ``- org/repo[/sub]@ref``.
_PATTERN_ENTRY_RE = re.compile(r"^\+-\s+['\"]?([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@([^\s'\"]+)['\"]?\s*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def extract_pattern_entries_from_diff(diff_text: str) -> dict[str, list[str]]:
    """Classify the entries a diff adds to ``approved_patterns.yml``.

    Returns ``{"sha": [...], "wildcard": [...], "unpinned": [...]}`` of
    ``org/repo@ref`` strings.  ``sha`` entries can be verified like any other
    ref; ``wildcard`` (``@*``) entries are an explicit policy decision with
    nothing to rebuild; ``unpinned`` entries name a tag or branch, which can
    move after review, so they must never pass verification silently.
    """
    found: dict[str, list[str]] = {"sha": [], "wildcard": [], "unpinned": []}
    current_file: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current_file = path[2:] if path.startswith("b/") else path
            continue
        if line.startswith("diff "):
            current_file = None
            continue
        if current_file != PATTERNS_FILE:
            continue
        match = _PATTERN_ENTRY_RE.match(line)
        if not match:
            continue
        entry = f"{match.group(1)}@{match.group(2)}"
        ref = match.group(2)
        kind = "sha" if _SHA_RE.match(ref) else "wildcard" if ref == "*" else "unpinned"
        if entry not in found[kind]:
            found[kind].append(entry)
    return found


def extract_action_refs_from_diff(diff_text: str) -> list[str]:
    """Extract action refs from a unified diff string.

    This is the pure-logic core of PR extraction, separated for testability.

    The actions.yml format is a map of ``org/repo:`` keys to nested commit-hash
    entries.  A PR may add a wholly new key (``+org/repo:`` followed by ``+
    <hash>:``) or a new hash under an *existing* key — in which case the key
    only appears as a context line (`` org/repo:``) or in the hunk header
    (``@@ -N,N +N,N @@ org/repo:``).  Both forms must be handled, otherwise
    multi-action PRs that add hashes under several pre-existing keys (e.g.
    apache/infrastructure-actions#803) silently drop all but the first
    fully-new key.
    """
    seen: set[str] = set()
    refs: list[str] = []

    actions_yml_key: str | None = None

    # actions.yml top-level key, with the diff marker (+/space) included so
    # both added (``+org/repo:``) and context (`` org/repo:``) lines match.
    # Note we deliberately *exclude* removed lines (``-org/repo:``): a removal
    # block shouldn't leave a stale key context for any subsequent hash.
    yml_key_re = re.compile(r"^[+ ]([a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+):\s*$")
    # Same key shape, but appearing as the function-context tail of a hunk
    # header — git includes the closest enclosing block name there.
    hunk_key_re = re.compile(
        r"^@@ [-+,\d ]+ @@\s*([a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+):\s*$"
    )

    for line in diff_text.splitlines():
        # Reset key context at hunk boundaries so a hash addition can't pick
        # up a stale key from an earlier hunk.  Hunk headers may carry the
        # enclosing actions.yml key as their tail context — if so, seed it.
        if line.startswith("@@"):
            hunk_match = hunk_key_re.match(line)
            actions_yml_key = (
                hunk_match.group(1).rstrip("/") if hunk_match else None
            )
            continue
        if line.startswith(("---", "+++", "diff ")):
            actions_yml_key = None
            continue

        # Workflow files: uses: org/repo@hash (added lines only).
        match = re.search(r"^\+.*uses?:\s+([^@\s]+)@([0-9a-f]{40})", line)
        if match:
            ref = f"{match.group(1)}@{match.group(2)}"
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
            continue

        # actions.yml top-level key on either an added or context line.
        key_match = yml_key_re.match(line)
        if key_match:
            actions_yml_key = key_match.group(1).rstrip("/")
            continue

        if actions_yml_key:
            # Hash entries: only attribute *added* hashes — context-line
            # hashes belong to entries that were already in main.
            hash_match = re.match(
                r"^\+\s+['\"]?([0-9a-f]{40})['\"]?:\s*$", line
            )
            if hash_match:
                ref = f"{actions_yml_key}@{hash_match.group(1)}"
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
                continue

            # Indented properties under the current key (``tag:``,
            # ``expires_at:``, etc.) — keep the key context active.  Accepted
            # on either added or context lines for the same reason as above.
            if re.match(r"^[+ ]\s{2,}", line):
                continue

            # Anything else means we've left the current key's block.
            actions_yml_key = None

    # approved_patterns.yml is generated from actions.yml, but a hand edit to
    # it still lands in the org allow list, so its SHA entries are verified too.
    for ref in extract_pattern_entries_from_diff(diff_text)["sha"]:
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)

    return refs
