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
"""Parsing action references and extracting metadata from action.yml files."""

import re
import sys
from functools import lru_cache

import requests

from .console import console


def parse_action_ref(ref: str) -> tuple[str, str, str, str]:
    """Parse org/repo[/sub_path]@hash into (org, repo, sub_path, hash).

    sub_path is empty string for top-level actions (e.g. ``dorny/test-reporter@abc``),
    or a relative path for monorepo sub-actions (e.g. ``gradle/actions/setup-gradle@abc``
    yields sub_path="setup-gradle").
    """
    if "@" not in ref:
        console.print(f"[red]Error:[/red] invalid format '{ref}', expected org/repo@hash")
        sys.exit(1)
    action_path, commit_hash = ref.rsplit("@", 1)
    parts = action_path.split("/")
    if len(parts) < 2:
        console.print(f"[red]Error:[/red] invalid action path '{action_path}', expected org/repo")
        sys.exit(1)
    org, repo = parts[0], parts[1]
    sub_path = "/".join(parts[2:])
    return org, repo, sub_path, commit_hash


@lru_cache(maxsize=512)
def fetch_action_yml(org: str, repo: str, commit_hash: str, sub_path: str = "") -> str | None:
    """Fetch action.yml content from GitHub at a specific commit.

    Cached so that multiple security checks walking the same action graph
    only pay the HTTP cost once per (org, repo, commit, sub_path).
    """
    candidates = []
    if sub_path:
        candidates.extend([f"{sub_path}/action.yml", f"{sub_path}/action.yaml"])
    candidates.extend(["action.yml", "action.yaml"])

    for path in candidates:
        url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.ok:
                return resp.text
        except requests.RequestException:
            continue
    return None


def fetch_file_from_github(org: str, repo: str, commit_hash: str, path: str) -> str | None:
    """Fetch a file's content from GitHub at a specific commit."""
    url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            return resp.text
    except requests.RequestException:
        pass
    return None


def extract_composite_uses(action_yml_content: str) -> list[dict]:
    """Extract all uses: references from composite action steps.

    Returns a list of dicts with keys: raw (full string), org, repo, sub_path,
    ref, is_hash_pinned, is_local, line_num.
    """
    results = []
    for i, line in enumerate(action_yml_content.splitlines(), 1):
        match = re.search(r"uses:\s+(.+?)(?:\s*#.*)?$", line.strip())
        if not match:
            continue
        raw = match.group(1).strip().strip("'\"")

        if raw.startswith("./"):
            results.append({
                "raw": raw, "org": "", "repo": "", "sub_path": "",
                "ref": "", "is_hash_pinned": True, "is_local": True,
                "line_num": i,
            })
            continue

        if raw.startswith("docker://"):
            results.append({
                "raw": raw, "org": "", "repo": "", "sub_path": "",
                "ref": "", "is_hash_pinned": True, "is_local": False,
                "line_num": i, "is_docker": True,
            })
            continue

        if "@" not in raw:
            continue
        action_path, ref = raw.rsplit("@", 1)
        parts = action_path.split("/")
        if len(parts) < 2:
            continue
        org, repo = parts[0], parts[1]
        sub_path = "/".join(parts[2:])
        is_hash = bool(re.match(r"^[0-9a-f]{40}$", ref))

        results.append({
            "raw": raw, "org": org, "repo": repo, "sub_path": sub_path,
            "ref": ref, "is_hash_pinned": is_hash, "is_local": False,
            "line_num": i,
        })

    return results


def detect_action_type_from_yml(action_yml_content: str) -> str:
    """Extract the using: field from an action.yml string."""
    for line in action_yml_content.splitlines():
        m = re.match(r"\s+using:\s*['\"]?(\S+?)['\"]?\s*$", line)
        if m:
            return m.group(1)
    return "unknown"
