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


def extract_action_refs_from_diff(diff_text: str) -> list[str]:
    """Extract action refs from a unified diff string.

    This is the pure-logic core of PR extraction, separated for testability.
    """
    seen: set[str] = set()
    refs: list[str] = []

    actions_yml_key: str | None = None

    for line in diff_text.splitlines():
        # Workflow files: uses: org/repo@hash
        match = re.search(r"^\+.*uses?:\s+([^@\s]+)@([0-9a-f]{40})", line)
        if match:
            action_path = match.group(1)
            commit_hash = match.group(2)
            ref = f"{action_path}@{commit_hash}"
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
            continue

        # actions.yml: org/repo: as top-level key
        key_match = re.match(r"^\+([a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+):\s*$", line)
        if key_match:
            actions_yml_key = key_match.group(1).rstrip("/")
            continue

        # Match indented hash under the current key
        if actions_yml_key:
            hash_match = re.match(r"^\+\s+['\"]?([0-9a-f]{40})['\"]?:\s*$", line)
            if hash_match:
                commit_hash = hash_match.group(1)
                ref = f"{actions_yml_key}@{commit_hash}"
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
                continue

            if re.match(r"^\+\s{4,}", line):
                continue

            actions_yml_key = None

    return refs
