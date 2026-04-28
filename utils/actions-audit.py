# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31",
#     "rich>=13.0",
#     "pyyaml>=6.0",
# ]
# ///

"""
Audit Apache GitHub repositories for proper GitHub Actions security configurations.

Checks each repo for: dependabot (github-actions with cooldown), CodeQL analysis,
zizmor workflow scanning, and ASF allowlist-check. Creates PRs to add missing configs.

Usage:
    uv run actions-audit.py --dry-run --pmc spark --max-num 5
    uv run actions-audit.py --pmc kafka --pmc flink --max-num 10
    uv run actions-audit.py --dry-run --max-num 50 --batch-size 100
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

console = Console(stderr=True)

GITHUB_API = "https://api.github.com"
PMC_CACHE_DIR = Path("~/.cache/asf-actions-audit").expanduser()
PMC_CACHE_FILE = PMC_CACHE_DIR / "pmc-list.json"
PMC_CACHE_MAX_AGE = 86400  # 1 day

BRANCH_NAME = "asf-actions-security-audit"

# Apache license header for generated files
APACHE_LICENSE_HEADER = """\
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
"""

DEPENDABOT_TEMPLATE = """\
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 4
"""

DEPENDABOT_ACTIONS_ENTRY = {
    "package-ecosystem": "github-actions",
    "directory": "/",
    "schedule": {"interval": "weekly"},
    "cooldown": {"default-days": 4},
}

CODEQL_TEMPLATE = """\
{license}
---
name: "CodeQL"

on:  # yamllint disable-line rule:truthy
  pull_request:
    branches: ['{default_branch}']
  push:
    branches: ['{default_branch}']
  schedule:
    - cron: '0 2 * * *'

permissions:
  contents: read
concurrency:
  group: codeql-${{{{ github.event.pull_request.number || github.ref }}}}
  cancel-in-progress: true

jobs:
  analyze:
    name: Analyze
    runs-on: ["ubuntu-22.04"]
    permissions:
      actions: read
      contents: read
      pull-requests: read
      security-events: write
    steps:
      - name: Checkout repository
        uses: actions/checkout@v6
        with:
          persist-credentials: false

      - name: Initialize CodeQL
        uses: github/codeql-action/init@v4
        with:
          languages: actions

      - name: Perform CodeQL Analysis
        uses: github/codeql-action/analyze@v4
"""

ZIZMOR_TEMPLATE = """\
{license}
---
name: "Zizmor"

on:  # yamllint disable-line rule:truthy
  push:
    branches: ['{default_branch}']
    paths: ['.github/workflows/**']
  pull_request:
    paths: ['.github/workflows/**']

permissions:
  contents: read
  security-events: write

jobs:
  zizmor:
    name: Zizmor
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v6
        with:
          persist-credentials: false

      - name: Run Zizmor
        uses: zizmorcore/zizmor-action@71321a20a9ded102f6e9ce5718a2fcec2c4f70d8  # v0.5.2
        with:
          sarif: results.sarif
          config: .github/zizmor.yml

      - name: Upload SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v4
        with:
          sarif_file: results.sarif
"""

ZIZMOR_CONFIG_TEMPLATE = """\
rules:
  secrets-outside-env:
    disable: true
"""

ALLOWLIST_TEMPLATE = """\
{license}
---
name: "ASF Actions Allowlist Check"

on:  # yamllint disable-line rule:truthy
  pull_request:
    paths: ['.github/workflows/**']
  push:
    branches: ['{default_branch}']
    paths: ['.github/workflows/**']

permissions:
  contents: read

jobs:
  allowlist-check:
    name: Allowlist Check
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v6
        with:
          persist-credentials: false

      - name: Check ASF Allowlist
        uses: apache/infrastructure-actions/allowlist-check@main
"""


@dataclass
class AuditResult:
    repo: str
    default_branch: str = "main"
    default_branch_sha: str = ""
    has_workflows: bool = False
    missing_dependabot: bool = False
    missing_dependabot_actions: bool = False
    missing_dependabot_cooldown: bool = False
    existing_dependabot_content: str | None = None
    existing_dependabot_sha: str | None = None
    missing_codeql: bool = False
    missing_zizmor: bool = False
    missing_allowlist: bool = False
    zizmor_has_errors: bool = False
    zizmor_output: str = ""
    pr_url: str = ""
    error: str = ""
    skipped: str = ""
    workflow_files: list[str] = field(default_factory=list)
    workflow_contents: dict[str, str] = field(default_factory=dict)

    @property
    def needs_pr(self) -> bool:
        return (
            self.missing_dependabot
            or self.missing_dependabot_actions
            or self.missing_dependabot_cooldown
            or self.missing_codeql
            or self.missing_zizmor
            or self.missing_allowlist
        )

    @property
    def missing_items(self) -> list[str]:
        items = []
        if self.missing_dependabot:
            items.append("dependabot")
        elif self.missing_dependabot_actions:
            items.append("dependabot (actions ecosystem)")
        elif self.missing_dependabot_cooldown:
            items.append("dependabot (cooldown)")
        if self.missing_codeql:
            items.append("codeql")
        if self.missing_zizmor:
            items.append("zizmor")
        if self.missing_allowlist:
            items.append("allowlist-check")
        return items


class GitHubClient:
    """GitHub API client — uses either gh CLI or requests with a token."""

    def __init__(self, token: str | None = None, use_requests: bool = False,
                 verbose: bool = False):
        self.token = token
        self._use_requests = use_requests or token is not None
        self.verbose = verbose

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _gh_api(self, endpoint: str, method: str = "GET",
                 input_data: str | None = None) -> dict | list | None:
        cmd = ["gh", "api", endpoint]
        if method != "GET":
            cmd.extend(["--method", method])
        if input_data is not None:
            cmd.extend(["--input", "-"])
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            input=input_data,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None

    def _get(self, endpoint: str) -> dict | list | None:
        if self._use_requests:
            resp = requests.get(f"{GITHUB_API}/{endpoint}", headers=self._headers())
            if resp.ok:
                return resp.json()
            return None
        return self._gh_api(endpoint)

    def _post(self, endpoint: str, data: dict) -> dict | None:
        if self._use_requests:
            resp = requests.post(
                f"{GITHUB_API}/{endpoint}", headers=self._headers(), json=data,
            )
            if resp.ok or resp.status_code == 201:
                return resp.json()
            console.print(f"[red]POST {endpoint} failed: {resp.status_code} {resp.text[:200]}[/]")
            return None
        return self._gh_api(endpoint, method="POST", input_data=json.dumps(data))

    def _put(self, endpoint: str, data: dict) -> dict | None:
        if self._use_requests:
            resp = requests.put(
                f"{GITHUB_API}/{endpoint}", headers=self._headers(), json=data,
            )
            if resp.ok or resp.status_code == 201:
                return resp.json()
            console.print(f"[red]PUT {endpoint} failed: {resp.status_code} {resp.text[:200]}[/]")
            return None
        return self._gh_api(endpoint, method="PUT", input_data=json.dumps(data))

    def graphql(self, query: str, variables: dict) -> dict | None:
        if self.verbose:
            console.print(f"[dim]GraphQL query:[/]")
            console.print(f"[dim]{query.strip()}[/]")
            console.print(f"[dim]Variables: {json.dumps(variables)}[/]")
        if self._use_requests:
            resp = requests.post(
                f"{GITHUB_API}/graphql",
                headers=self._headers(),
                json={"query": query, "variables": variables},
            )
            if resp.ok:
                return resp.json()
            console.print(f"[red]GraphQL failed: {resp.status_code} {resp.text[:200]}[/]")
            return None
        # Build gh api graphql command
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in variables.items():
            if isinstance(v, int):
                cmd.extend(["-F", f"{k}={v}"])
            elif v is None:
                continue
            else:
                cmd.extend(["-f", f"{k}={v}"])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if self.verbose:
            console.print(f"[dim]GraphQL response (rc={result.returncode}):[/]")
            if result.stdout.strip():
                console.print(f"[dim]{result.stdout[:500]}[/]")
            if result.stderr.strip():
                console.print(f"[dim]stderr: {result.stderr[:500]}[/]")
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        if result.returncode != 0 and not self.verbose:
            console.print(f"[red]GraphQL call failed (rc={result.returncode}): {result.stderr[:200]}[/]")
        return None

    def get_repos_page(self, org: str, batch_size: int,
                       cursor: str | None = None) -> tuple[list[dict], bool, str | None]:
        """Fetch a page of repos with workflow info via GraphQL."""
        query = """
        query($org: String!, $batchSize: Int!, $cursor: String) {
          organization(login: $org) {
            repositories(first: $batchSize, after: $cursor,
                         orderBy: {field: NAME, direction: ASC}) {
              pageInfo { hasNextPage endCursor }
              nodes {
                name
                isArchived
                isDisabled
                defaultBranchRef {
                  name
                  target { oid }
                }
                workflows: object(expression: "HEAD:.github/workflows") {
                  ... on Tree { entries { name } }
                }
                dependabot: object(expression: "HEAD:.github/dependabot.yml") {
                  ... on Blob { text }
                }
              }
            }
          }
        }
        """
        variables: dict = {"org": org, "batchSize": batch_size}
        if cursor:
            variables["cursor"] = cursor

        data = self.graphql(query, variables)
        if not data or "data" not in data:
            return [], False, None

        repos_data = data["data"]["organization"]["repositories"]
        page_info = repos_data["pageInfo"]
        return (
            repos_data["nodes"],
            page_info["hasNextPage"],
            page_info["endCursor"],
        )

    def search_repos_page(self, org: str, pmc_prefix: str, batch_size: int,
                          cursor: str | None = None) -> tuple[list[dict], bool, str | None]:
        """Search for repos by name prefix via GraphQL search.

        Uses GitHub search to find repos matching the PMC prefix, avoiding
        pagination through the entire org. Client-side prefix filtering is
        still needed since search matches anywhere in the name.
        """
        query = """
        query($searchQuery: String!, $batchSize: Int!, $cursor: String) {
          search(query: $searchQuery, type: REPOSITORY, first: $batchSize, after: $cursor) {
            repositoryCount
            pageInfo { hasNextPage endCursor }
            nodes {
              ... on Repository {
                name
                isArchived
                isDisabled
                defaultBranchRef {
                  name
                  target { oid }
                }
                workflows: object(expression: "HEAD:.github/workflows") {
                  ... on Tree { entries { name } }
                }
                dependabot: object(expression: "HEAD:.github/dependabot.yml") {
                  ... on Blob { text }
                }
              }
            }
          }
        }
        """
        search_query = f"org:{org} {pmc_prefix} in:name fork:true"
        variables: dict = {"searchQuery": search_query, "batchSize": batch_size}
        if cursor:
            variables["cursor"] = cursor

        data = self.graphql(query, variables)
        if not data or "data" not in data:
            return [], False, None

        search_data = data["data"]["search"]
        page_info = search_data["pageInfo"]
        # Filter out empty nodes (can happen with search union types)
        nodes = [n for n in search_data["nodes"] if n and n.get("name")]
        return (
            nodes,
            page_info["hasNextPage"],
            page_info["endCursor"],
        )

    def get_workflow_contents(self, owner: str, repo: str,
                              filenames: list[str]) -> dict[str, str]:
        """Fetch all workflow file contents in a single GraphQL query.

        Returns a dict mapping filename -> content for files that exist.
        Uses dynamically aliased object() expressions to fetch each file's
        blob text in one round-trip.
        """
        if not filenames:
            return {}

        # Build aliased fields: wf0: object(expression: "HEAD:.github/workflows/foo.yml") { ... on Blob { text } }
        # Alias names must be valid GraphQL identifiers (letters, digits, underscore)
        fragments = []
        alias_map = {}  # alias -> filename
        for i, name in enumerate(filenames):
            alias = f"wf{i}"
            alias_map[alias] = name
            escaped = name.replace("\\", "\\\\").replace('"', '\\"')
            fragments.append(
                f'{alias}: object(expression: "HEAD:.github/workflows/{escaped}") '
                f"{{ ... on Blob {{ text }} }}"
            )

        fields = "\n              ".join(fragments)
        query = f"""
        query($owner: String!, $repo: String!) {{
          repository(owner: $owner, name: $repo) {{
              {fields}
          }}
        }}
        """
        data = self.graphql(query, {"owner": owner, "repo": repo})
        if not data or "data" not in data:
            return {}

        repo_data = data["data"]["repository"]
        result = {}
        for alias, filename in alias_map.items():
            obj = repo_data.get(alias)
            if obj and isinstance(obj, dict) and obj.get("text"):
                result[filename] = obj["text"]
        return result

    def get_file_content(self, owner: str, repo: str, path: str) -> tuple[str | None, str | None]:
        """Fetch file content and its blob SHA. Returns (content, sha) or (None, None)."""
        data = self._get(f"repos/{owner}/{repo}/contents/{path}")
        if isinstance(data, dict) and data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data.get("sha")
        return None, None

    def create_branch(self, owner: str, repo: str, branch: str, sha: str) -> bool:
        data = self._post(f"repos/{owner}/{repo}/git/refs", {
            "ref": f"refs/heads/{branch}",
            "sha": sha,
        })
        return data is not None

    def create_or_update_file(self, owner: str, repo: str, path: str,
                              content: str, message: str, branch: str,
                              sha: str | None = None) -> bool:
        payload: dict = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        data = self._put(f"repos/{owner}/{repo}/contents/{path}", payload)
        return data is not None

    def create_pull_request(self, owner: str, repo: str, title: str, body: str,
                            head: str, base: str) -> str | None:
        """Create a PR. Returns the HTML URL or None."""
        data = self._post(f"repos/{owner}/{repo}/pulls", {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        })
        if isinstance(data, dict):
            return data.get("html_url")
        return None

    def check_existing_pr(self, owner: str, repo: str,
                          head_branch: str) -> tuple[str | None, str | None]:
        """Check if a PR with the given head branch exists (any state).

        Returns (url, state) where state is 'open', 'closed', or 'merged', or (None, None).
        """
        # Check all states — open first, then closed (which includes merged)
        for state in ("open", "closed"):
            data = self._get(
                f"repos/{owner}/{repo}/pulls?state={state}&head={owner}:{head_branch}"
            )
            if isinstance(data, list) and data:
                pr = data[0]
                pr_state = "merged" if pr.get("merged_at") else pr["state"]
                return pr.get("html_url"), pr_state
        return None, None

    def check_branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        data = self._get(f"repos/{owner}/{repo}/git/ref/heads/{branch}")
        return data is not None and isinstance(data, dict) and "ref" in data

    def delete_branch(self, owner: str, repo: str, branch: str) -> bool:
        if self._use_requests:
            resp = requests.delete(
                f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
                headers=self._headers(),
            )
            return resp.ok or resp.status_code == 204
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/git/refs/heads/{branch}",
             "--method", "DELETE"],
            capture_output=True, text=True,
        )
        return result.returncode == 0


def get_pmc_list() -> list[str]:
    """Get list of Apache PMC names, with file caching."""
    if PMC_CACHE_FILE.exists():
        age = time.time() - PMC_CACHE_FILE.stat().st_mtime
        if age < PMC_CACHE_MAX_AGE:
            return json.loads(PMC_CACHE_FILE.read_text())

    console.print("[dim]Fetching PMC list from whimsy.apache.org...[/]")
    try:
        resp = requests.get(
            "https://whimsy.apache.org/public/committee-info.json",
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # The committees key contains a dict of PMC name -> info
        pmcs = sorted(data.get("committees", {}).keys())
    except Exception as e:
        console.print(f"[yellow]Warning: Could not fetch PMC list: {e}[/]")
        if PMC_CACHE_FILE.exists():
            console.print("[yellow]Using stale cache.[/]")
            return json.loads(PMC_CACHE_FILE.read_text())
        return []

    PMC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PMC_CACHE_FILE.write_text(json.dumps(pmcs))
    console.print(f"[dim]Cached {len(pmcs)} PMCs.[/]")
    return pmcs


def repo_pmc_prefix(repo_name: str) -> str:
    """Extract PMC prefix from repo name (text before first '-')."""
    return repo_name.split("-")[0] if "-" in repo_name else repo_name


def check_dependabot(dependabot_text: str | None) -> tuple[bool, bool, bool]:
    """Check dependabot config. Returns (missing_entirely, missing_actions, missing_cooldown)."""
    if not dependabot_text:
        return True, False, False

    try:
        config = yaml.safe_load(dependabot_text)
    except yaml.YAMLError:
        return True, False, False

    if not isinstance(config, dict) or "updates" not in config:
        return True, False, False

    for entry in config.get("updates", []):
        if entry.get("package-ecosystem") == "github-actions":
            cooldown = entry.get("cooldown", {})
            if isinstance(cooldown, dict) and cooldown.get("default-days", 0) >= 4:
                return False, False, False
            return False, False, True

    return False, True, False


def check_workflow_for_action(content: str, action_ref: str) -> bool:
    """Check if a workflow file content references a given action."""
    return action_ref in content


def run_zizmor_check(workflow_contents: dict[str, str]) -> tuple[bool, str]:
    """Run zizmor on already-fetched workflow contents. Returns (has_errors, output)."""
    zizmor_bin = shutil.which("zizmor")
    if not zizmor_bin:
        return False, "zizmor not found in PATH, skipping check"

    if not workflow_contents:
        return False, "no workflow files to check"

    with tempfile.TemporaryDirectory() as tmpdir:
        workflows_dir = Path(tmpdir) / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)

        for name, content in workflow_contents.items():
            (workflows_dir / name).write_text(content)

        # Write config to skip secrets-outside-env (too noisy, low signal)
        config_path = Path(tmpdir) / "zizmor.yml"
        config_path.write_text(ZIZMOR_CONFIG_TEMPLATE)

        result = subprocess.run(
            [zizmor_bin, "--config", str(config_path), str(workflows_dir)],
            capture_output=True, text=True,
            timeout=120,
        )
        return result.returncode != 0, result.stdout + result.stderr


def _print_zizmor_output(output: str) -> None:
    """Print zizmor output, filtering out INFO/completion noise."""
    for line in output.strip().splitlines():
        # Skip INFO log lines and empty lines
        stripped = line.strip()
        if stripped.startswith("INFO ") or stripped.startswith("🌈"):
            continue
        console.print(f"  [dim]  | {line}[/]")


def comment_out_workflow(content: str, reason_lines: list[str]) -> str:
    """Comment out an entire workflow file with explanatory header."""
    lines = []
    for reason in reason_lines:
        lines.append(f"# {reason}")
    lines.append("#")
    for line in content.splitlines():
        if line.strip():
            lines.append(f"# {line}")
        else:
            lines.append("#")
    return "\n".join(lines) + "\n"


def build_dependabot_content(existing_content: str | None) -> str:
    """Build dependabot.yml content, merging with existing if present."""
    if not existing_content:
        return DEPENDABOT_TEMPLATE

    try:
        config = yaml.safe_load(existing_content)
    except yaml.YAMLError:
        return DEPENDABOT_TEMPLATE

    if not isinstance(config, dict):
        return DEPENDABOT_TEMPLATE

    updates = config.get("updates", [])

    # Check if github-actions ecosystem already exists
    for entry in updates:
        if entry.get("package-ecosystem") == "github-actions":
            # Just add/fix cooldown
            entry["cooldown"] = {"default-days": 4}
            return yaml.dump(config, default_flow_style=False, sort_keys=False)

    # Add new entry
    updates.append(DEPENDABOT_ACTIONS_ENTRY)
    config["updates"] = updates
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def build_pr_body(result: AuditResult) -> str:
    """Build the PR description body."""
    items_added = []
    if result.missing_dependabot or result.missing_dependabot_actions or result.missing_dependabot_cooldown:
        items_added.append(
            "- **Dependabot** (`.github/dependabot.yml`): Automated dependency updates for "
            "GitHub Actions with a 4-day cooldown to allow time for review."
        )
    if result.missing_codeql:
        items_added.append(
            "- **CodeQL Analysis** (`.github/workflows/codeql-analysis.yml`): Static analysis "
            "scanning for GitHub Actions workflow syntax and security issues."
        )
    if result.missing_zizmor:
        items_added.append(
            "- **Zizmor** (`.github/workflows/zizmor.yml`): Specialized scanner for GitHub "
            "Actions security anti-patterns and misconfigurations."
        )
    if result.missing_allowlist:
        items_added.append(
            "- **ASF Allowlist Check** (`.github/workflows/allowlist-check.yml`): Ensures all "
            "actions used are on the ASF Infrastructure approved allowlist."
        )

    body = f"""\
## Improve GitHub Actions Security Tooling

Due to recent security breaches and the fact that GitHub Actions can fail
silently, ASF Infrastructure is rolling out baseline security checks for
all repositories that use GitHub Actions.

This PR adds the following (where missing):

{chr(10).join(items_added)}
"""

    if result.zizmor_has_errors:
        body += """
> [!IMPORTANT]
> **Zizmor found existing issues in this repository's workflows.**
> The **CodeQL** and **Zizmor** workflows have been added but are **commented out**
> because enabling them now would immediately flag existing issues.
>
> To enable them:
> 1. Run `zizmor --fix .github/workflows/` to auto-fix common issues
> 2. Manually fix remaining issues flagged by zizmor
> 3. Uncomment the CodeQL and Zizmor workflow files
> 4. Open a follow-up PR
>
> We kindly ask the PMC to follow up with this after merging this PR.

<details>
<summary>Zizmor errors that need to be fixed</summary>

```
"""
        # Include filtered zizmor output
        for line in result.zizmor_output.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("INFO ") or stripped.startswith("🌈"):
                continue
            body += line + "\n"
        body += """```

</details>
"""

    body += """
---
Generated by [`apache/infrastructure-actions`](https://github.com/apache/infrastructure-actions) actions-audit
"""
    return body


def dry_run_preview(gh: GitHubClient, owner: str, result: AuditResult) -> None:
    """Show what would happen if a PR were created for this repo."""
    repo = result.repo

    # Run zizmor pre-check to show accurate preview
    if result.missing_zizmor or result.missing_codeql:
        result.zizmor_has_errors, result.zizmor_output = run_zizmor_check(
            result.workflow_contents,
        )

    # Files that would be created/updated
    files: list[tuple[str, str]] = []
    if result.missing_dependabot:
        files.append(("create", ".github/dependabot.yml"))
    elif result.missing_dependabot_actions or result.missing_dependabot_cooldown:
        files.append(("update", ".github/dependabot.yml"))
    if result.missing_codeql:
        status = "create (commented out)" if result.zizmor_has_errors else "create"
        files.append((status, ".github/workflows/codeql-analysis.yml"))
    if result.missing_zizmor:
        status = "create (commented out)" if result.zizmor_has_errors else "create"
        files.append((status, ".github/workflows/zizmor.yml"))
        files.append(("create", ".github/zizmor.yml"))
    if result.missing_allowlist:
        files.append(("create", ".github/workflows/allowlist-check.yml"))

    console.print(f"  [dim]Would create PR: \"Add GitHub Actions security tooling\"[/]")
    console.print(f"  [dim]Branch: {BRANCH_NAME} -> {result.default_branch}[/]")
    for action, path in files:
        console.print(f"  [dim]  {action}: {path}[/]")
    if result.zizmor_has_errors:
        console.print(f"  [yellow]  zizmor found errors — CodeQL and Zizmor would be commented out[/]")
        _print_zizmor_output(result.zizmor_output)


def confirm_pr_creation(owner: str, result: AuditResult) -> str:
    """Ask user to confirm PR creation. Returns 'yes', 'no', or 'quit'."""
    console.print(f"  [bold]Create PR for {owner}/{result.repo}?[/]")
    console.print(f"  Will add: {', '.join(result.missing_items)}")
    answer = Prompt.ask(
        "  Proceed?",
        choices=["yes", "no", "quit"],
        default="yes",
        console=console,
    )
    return answer


def audit_repo(gh: GitHubClient, owner: str, node: dict) -> AuditResult:
    """Run all audit checks on a single repo."""
    repo_name = node["name"]
    result = AuditResult(repo=repo_name)

    # Default branch info
    default_ref = node.get("defaultBranchRef")
    if not default_ref:
        result.skipped = "no default branch"
        return result

    result.default_branch = default_ref["name"]
    result.default_branch_sha = default_ref["target"]["oid"]

    # Check for workflows
    workflows_obj = node.get("workflows")
    if not workflows_obj or not isinstance(workflows_obj, dict):
        result.skipped = "no workflows"
        return result

    entries = workflows_obj.get("entries", [])
    if not entries:
        result.skipped = "no workflow files"
        return result

    result.has_workflows = True
    result.workflow_files = [e["name"] for e in entries if e["name"].endswith((".yml", ".yaml"))]

    # Check dependabot (already fetched via GraphQL)
    dependabot_obj = node.get("dependabot")
    dependabot_text = None
    if dependabot_obj and isinstance(dependabot_obj, dict):
        dependabot_text = dependabot_obj.get("text")

    missing_dep, missing_actions, missing_cooldown = check_dependabot(dependabot_text)
    result.missing_dependabot = missing_dep
    result.missing_dependabot_actions = missing_actions
    result.missing_dependabot_cooldown = missing_cooldown
    result.existing_dependabot_content = dependabot_text

    # Fetch all workflow file contents in a single GraphQL query
    result.workflow_contents = gh.get_workflow_contents(owner, repo_name, result.workflow_files)

    # Check workflow contents for the three action references
    codeql_found = False
    zizmor_found = False
    allowlist_found = False

    for content in result.workflow_contents.values():
        if not codeql_found and check_workflow_for_action(content, "github/codeql-action"):
            if "actions" in content:
                codeql_found = True
        if not zizmor_found and check_workflow_for_action(content, "zizmorcore/zizmor-action"):
            zizmor_found = True
        if not allowlist_found and check_workflow_for_action(
            content, "apache/infrastructure-actions/allowlist-check"
        ):
            allowlist_found = True

        if codeql_found and zizmor_found and allowlist_found:
            break

    result.missing_codeql = not codeql_found
    result.missing_zizmor = not zizmor_found
    result.missing_allowlist = not allowlist_found

    return result


def create_pr(gh: GitHubClient, owner: str, result: AuditResult) -> str | None:
    """Create a PR with missing configurations. Returns PR URL or None."""
    repo = result.repo

    # Check if branch exists (leftover from previous run)
    if gh.check_branch_exists(owner, repo, BRANCH_NAME):
        gh.delete_branch(owner, repo, BRANCH_NAME)

    # Run zizmor pre-check using already-fetched workflow contents
    if result.missing_zizmor or result.missing_codeql:
        console.print(f"  [dim]Running zizmor pre-check on {repo}...[/]")
        result.zizmor_has_errors, result.zizmor_output = run_zizmor_check(
            result.workflow_contents,
        )
        if result.zizmor_has_errors:
            console.print(f"  [yellow]Zizmor found errors in {repo} — will comment out CodeQL and Zizmor workflows[/]")
            _print_zizmor_output(result.zizmor_output)

    # Create branch
    if not gh.create_branch(owner, repo, BRANCH_NAME, result.default_branch_sha):
        result.error = "failed to create branch"
        return None

    default_branch = result.default_branch

    # Add missing files
    if result.missing_dependabot or result.missing_dependabot_actions or result.missing_dependabot_cooldown:
        new_content = build_dependabot_content(result.existing_dependabot_content)
        # Need to get the SHA if updating existing file
        sha = None
        if not result.missing_dependabot:
            # File exists, get its SHA on the branch
            _, sha = gh.get_file_content(owner, repo, ".github/dependabot.yml")
        msg = "Add GitHub Actions dependabot configuration" if result.missing_dependabot else \
              "Update dependabot: add github-actions ecosystem with cooldown"
        if not gh.create_or_update_file(owner, repo, ".github/dependabot.yml",
                                        new_content, msg, BRANCH_NAME, sha):
            result.error = "failed to create dependabot.yml"
            return None

    if result.missing_codeql:
        content = CODEQL_TEMPLATE.format(
            license=APACHE_LICENSE_HEADER, default_branch=default_branch,
        )
        if result.zizmor_has_errors:
            content = comment_out_workflow(content, [
                "NOTE: This workflow is commented out because zizmor found existing",
                "errors in this repository's GitHub Actions workflows.",
                "",
                "CodeQL for Actions depends on clean workflow syntax. Please:",
                "1. Run 'zizmor --fix .github/workflows/' to auto-fix common issues",
                "2. Manually fix remaining issues flagged by zizmor",
                "3. Uncomment this file and open a follow-up PR",
            ])
        if not gh.create_or_update_file(owner, repo, ".github/workflows/codeql-analysis.yml",
                                        content, "Add CodeQL analysis workflow for Actions",
                                        BRANCH_NAME):
            result.error = "failed to create codeql-analysis.yml"
            return None

    if result.missing_zizmor:
        content = ZIZMOR_TEMPLATE.format(
            license=APACHE_LICENSE_HEADER, default_branch=default_branch,
        )
        if result.zizmor_has_errors:
            content = comment_out_workflow(content, [
                "NOTE: This workflow is commented out because zizmor found existing",
                "errors in this repository's GitHub Actions workflows.",
                "",
                "Zizmor can fix many of these issues automatically. Please:",
                "1. Run 'zizmor --fix .github/workflows/' to auto-fix common issues",
                "2. Manually fix remaining issues",
                "3. Uncomment this file and open a follow-up PR",
            ])
        if not gh.create_or_update_file(owner, repo, ".github/workflows/zizmor.yml",
                                        content, "Add Zizmor workflow scanning",
                                        BRANCH_NAME):
            result.error = "failed to create zizmor.yml"
            return None
        # Add zizmor config file (skips secrets-outside-env)
        if not gh.create_or_update_file(owner, repo, ".github/zizmor.yml",
                                        ZIZMOR_CONFIG_TEMPLATE,
                                        "Add zizmor configuration",
                                        BRANCH_NAME):
            result.error = "failed to create zizmor.yml config"
            return None

    if result.missing_allowlist:
        content = ALLOWLIST_TEMPLATE.format(
            license=APACHE_LICENSE_HEADER, default_branch=default_branch,
        )
        if not gh.create_or_update_file(owner, repo, ".github/workflows/allowlist-check.yml",
                                        content, "Add ASF allowlist check workflow",
                                        BRANCH_NAME):
            result.error = "failed to create allowlist-check.yml"
            return None

    # Create PR
    title = "Add GitHub Actions security tooling"
    body = build_pr_body(result)
    pr_url = gh.create_pull_request(owner, repo, title, body, BRANCH_NAME, default_branch)
    if not pr_url:
        result.error = "failed to create PR"
        return None

    result.pr_url = pr_url
    return pr_url


def main():
    parser = argparse.ArgumentParser(
        description="Audit Apache GitHub repos for proper Actions security configurations.",
    )
    parser.add_argument(
        "--pmc", action="append", default=[],
        help="Filter by PMC prefix (repeatable). Prefix is text before first '-' in repo name.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report findings without creating PRs.",
    )
    parser.add_argument(
        "--max-num", type=int, default=0,
        help="Maximum number of repos to check (0=unlimited).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="GraphQL page size for repo queries (default: 50).",
    )
    parser.add_argument(
        "--github-token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        help="GitHub token (default: GH_TOKEN or GITHUB_TOKEN env var).",
    )
    parser.add_argument(
        "--no-gh", action="store_true",
        help="Use requests library instead of gh CLI.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print GraphQL queries and other debug information.",
    )
    args = parser.parse_args()

    # Validate prerequisites
    if not args.no_gh and not args.github_token and not shutil.which("gh"):
        console.print("[red]Error: gh CLI not found. Install it or provide --github-token.[/]")
        sys.exit(1)

    if not args.dry_run and not shutil.which("zizmor"):
        console.print("[yellow]Warning: zizmor not found in PATH. Zizmor pre-checks will be skipped.[/]")

    gh = GitHubClient(token=args.github_token, use_requests=args.no_gh,
                      verbose=args.verbose)

    # Load PMC filter
    pmc_filter = set()
    if args.pmc:
        pmc_list = get_pmc_list()
        for pmc_name in args.pmc:
            pmc_lower = pmc_name.lower()
            if pmc_lower not in [p.lower() for p in pmc_list]:
                console.print(f"[yellow]Warning: '{pmc_name}' not found in PMC list. Using as prefix anyway.[/]")
            pmc_filter.add(pmc_lower)

    owner = "apache"
    cursor = None
    checked = 0
    quit_requested = False
    results: list[AuditResult] = []

    console.print(f"[bold]Auditing apache/ repositories...[/]")
    if pmc_filter:
        console.print(f"[dim]PMC filter: {', '.join(sorted(pmc_filter))}[/]")
    if args.dry_run:
        console.print("[dim]Dry run mode — no PRs will be created.[/]")
    console.print()

    def process_node(node: dict) -> AuditResult | None:
        """Process a single repo node. Returns None if skipped without counting."""
        nonlocal checked, quit_requested

        repo_name = node["name"]

        # Skip archived/disabled
        if node.get("isArchived") or node.get("isDisabled"):
            return None

        # PMC filter (still needed for search results — search matches anywhere in name)
        if pmc_filter:
            prefix = repo_pmc_prefix(repo_name)
            if prefix.lower() not in pmc_filter:
                return None

        checked += 1
        console.print(f"[bold]{checked}.[/] {owner}/{repo_name} ", end="")

        try:
            result = audit_repo(gh, owner, node)
        except Exception as e:
            result = AuditResult(repo=repo_name, error=str(e))

        if result.skipped:
            console.print(f"[dim]— skipped: {result.skipped}[/]")
            return result

        if result.error:
            console.print(f"[red]— error: {result.error}[/]")
            return result

        if not result.needs_pr:
            console.print("[green]— compliant[/]")
            return result

        missing_str = ", ".join(result.missing_items)
        console.print(f"[yellow]— missing: {missing_str}[/]")

        # Check for existing PR (open, closed, or merged)
        existing_url, existing_state = gh.check_existing_pr(
            owner, repo_name, BRANCH_NAME,
        )
        if existing_url:
            result.skipped = f"PR already {existing_state}: {existing_url}"
            console.print(f"  [dim]Skipped — PR already {existing_state}: {existing_url}[/]")
            return result

        if args.dry_run:
            # Show what would happen without making changes
            dry_run_preview(gh, owner, result)
        else:
            # Ask for confirmation
            answer = confirm_pr_creation(owner, result)
            if answer == "quit":
                console.print("  [dim]Quitting...[/]")
                quit_requested = True
                return result
            if answer == "no":
                console.print("  [dim]Skipped by user.[/]")
                result.skipped = "skipped by user"
                return result

            try:
                pr_url = create_pr(gh, owner, result)
                if pr_url:
                    console.print(f"  [green]PR created: {pr_url}[/]")
                elif result.skipped:
                    console.print(f"  [dim]{result.skipped}[/]")
                elif result.error:
                    console.print(f"  [red]Error: {result.error}[/]")
            except Exception as e:
                result.error = str(e)
                console.print(f"  [red]Error creating PR: {e}[/]")

        return result

    def process_page(nodes: list[dict]) -> bool:
        """Process a page of repo nodes. Returns True if should stop."""
        nonlocal checked, quit_requested
        for node in nodes:
            if args.max_num and checked >= args.max_num:
                return True
            if quit_requested:
                return True
            result = process_node(node)
            if result:
                results.append(result)
        return False

    if pmc_filter:
        # Use targeted search per PMC prefix — much faster than paging all repos
        seen_repos: set[str] = set()
        for pmc_prefix in sorted(pmc_filter):
            cursor = None
            while True:
                if args.max_num and checked >= args.max_num:
                    break
                if quit_requested:
                    break
                nodes, has_next, cursor = gh.search_repos_page(
                    owner, pmc_prefix, args.batch_size, cursor,
                )
                if not nodes:
                    break
                # Deduplicate across PMC prefixes
                unique_nodes = []
                for node in nodes:
                    name = node.get("name", "")
                    if name not in seen_repos:
                        seen_repos.add(name)
                        unique_nodes.append(node)
                if process_page(unique_nodes):
                    break
                if not has_next:
                    break
    else:
        cursor = None
        while True:
            nodes, has_next, cursor = gh.get_repos_page(owner, args.batch_size, cursor)
            if not nodes:
                break
            if process_page(nodes):
                break
            if not has_next:
                break

    # Summary table
    console.print()
    table = Table(title=f"Audit Summary ({checked} repos checked)")
    table.add_column("Repo", style="bold")
    table.add_column("Dependabot")
    table.add_column("CodeQL")
    table.add_column("Zizmor")
    table.add_column("Allowlist")
    table.add_column("Status")

    for r in results:
        if r.skipped and not r.has_workflows:
            continue  # Don't show repos without workflows

        def status_icon(missing: bool) -> str:
            return "[red]MISSING[/]" if missing else "[green]OK[/]"

        dep_missing = r.missing_dependabot or r.missing_dependabot_actions or r.missing_dependabot_cooldown
        dep_status = status_icon(dep_missing) if r.has_workflows else "[dim]—[/]"

        if r.error:
            status = f"[red]Error: {r.error[:40]}[/]"
        elif r.pr_url:
            status = f"[green]PR: {r.pr_url}[/]"
        elif r.skipped:
            status = f"[dim]{r.skipped}[/]"
        elif r.needs_pr:
            status = "[yellow]Dry run[/]" if args.dry_run else "[yellow]Needs PR[/]"
        else:
            status = "[green]Compliant[/]"

        table.add_row(
            f"apache/{r.repo}",
            dep_status,
            status_icon(r.missing_codeql) if r.has_workflows else "[dim]—[/]",
            status_icon(r.missing_zizmor) if r.has_workflows else "[dim]—[/]",
            status_icon(r.missing_allowlist) if r.has_workflows else "[dim]—[/]",
            status,
        )

    console.print(table)

    # Print totals
    total_with_workflows = sum(1 for r in results if r.has_workflows)
    total_compliant = sum(1 for r in results if r.has_workflows and not r.needs_pr and not r.error)
    total_needs_pr = sum(1 for r in results if r.needs_pr)
    total_prs_created = sum(1 for r in results if r.pr_url)
    total_errors = sum(1 for r in results if r.error)

    console.print(f"\n[bold]Totals:[/]")
    console.print(f"  Repos with workflows: {total_with_workflows}")
    console.print(f"  Compliant: [green]{total_compliant}[/]")
    console.print(f"  Needing updates: [yellow]{total_needs_pr}[/]")
    if total_prs_created:
        console.print(f"  PRs created: [green]{total_prs_created}[/]")
    if total_errors:
        console.print(f"  Errors: [red]{total_errors}[/]")


if __name__ == "__main__":
    main()
