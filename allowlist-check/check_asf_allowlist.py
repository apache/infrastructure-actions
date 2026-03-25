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

"""Check that all GitHub Actions uses: refs are on the ASF allowlist.

Usage:
    python3 check_asf_allowlist.py <allowlist_path>

The allowlist is the approved_patterns.yml file colocated at the root of
this repository (../approved_patterns.yml relative to this script).

The glob pattern for YAML files to scan can be overridden via the
GITHUB_YAML_GLOB environment variable (default: .github/**/*.yml).

Exits with code 1 if any action ref is not allowlisted.
"""

import fnmatch
import glob
import os
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


def find_action_refs(node: Any) -> Generator[str, None, None]:
    """Recursively find all `uses:` values from a parsed YAML tree.

    Args:
        node: A parsed YAML node (any type returned by yaml.safe_load)

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

    Args:
        scan_glob: Glob pattern for files to scan.

    Returns:
        dict: Mapping of each action ref to the list of file paths that use it.
            Local (./) and Docker (docker://) refs are excluded.
    """

    action_refs = {}
    for filepath in sorted(glob.glob(scan_glob, recursive=True)):
        try:
            yaml = ruyaml.YAML()
            with open(filepath) as f:
                content = yaml.load(f)
        except ruyaml.YAMLError as exc:
            print(f"::warning file={filepath}::Skipping file with invalid YAML: {exc}")
            continue
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


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <allowlist_path>", file=sys.stderr)
        sys.exit(2)

    allowlist_path = sys.argv[1]
    allowlist = load_allowlist(allowlist_path)
    scan_glob = os.environ.get("GITHUB_YAML_GLOB", DEFAULT_GITHUB_YAML_GLOB)
    action_refs = collect_action_refs(scan_glob)

    violations = []
    for action_ref, filepaths in sorted(action_refs.items()):
        if not is_allowed(action_ref, allowlist):
            for filepath in filepaths:
                violations.append((filepath, action_ref))

    if violations:
        print(
            f"::error::Found {len(violations)} action ref(s) not on the ASF allowlist:"
        )
        for filepath, action_ref in violations:
            print(f"::error file={filepath}::{action_ref} is not on the ASF allowlist")
        sys.exit(1)
    else:
        print(f"All {len(action_refs)} unique action refs are on the ASF allowlist")


if __name__ == "__main__":
    main()
