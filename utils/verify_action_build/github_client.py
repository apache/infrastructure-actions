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
"""GitHub API client — supports both ``gh`` CLI and the REST API via requests."""

import json
import re
import subprocess
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"


def _detect_repo() -> str:
    """Detect the GitHub repo from the git remote origin URL."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parent.parent.parent,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        if match:
            return match.group(1)
    return "apache/infrastructure-actions"


class GitHubClient:
    """Abstraction over GitHub API — uses either gh CLI or requests with a token."""

    def __init__(self, token: str | None = None, repo: str | None = None):
        self.repo = repo or _detect_repo()
        self.token = token
        self._use_requests = token is not None

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _gh_api(self, endpoint: str) -> dict | list | None:
        """Call gh api and return parsed JSON, or None on failure."""
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None

    def _get(self, endpoint: str) -> dict | list | None:
        """GET from GitHub API using requests or gh CLI."""
        if self._use_requests:
            resp = requests.get(f"{GITHUB_API}/{endpoint}", headers=self._headers())
            if resp.ok:
                return resp.json()
            return None
        return self._gh_api(endpoint)

    def get_commit_pulls(self, owner: str, repo: str, commit_sha: str) -> list[dict]:
        """Get PRs associated with a commit."""
        data = self._get(f"repos/{owner}/{repo}/commits/{commit_sha}/pulls")
        return data if isinstance(data, list) else []

    def compare_commits(self, owner: str, repo: str, base: str, head: str) -> list[dict]:
        """Get commits between two refs."""
        data = self._get(f"repos/{owner}/{repo}/compare/{base}...{head}")
        if isinstance(data, dict):
            return data.get("commits", [])
        return []

    def get_pr_diff(self, pr_number: int) -> str | None:
        """Get the diff for a PR."""
        if self._use_requests:
            resp = requests.get(
                f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}",
                headers={**self._headers(), "Accept": "application/vnd.github.v3.diff"},
            )
            return resp.text if resp.ok else None
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True, text=True,
        )
        return result.stdout if result.returncode == 0 else None

    def get_authenticated_user(self) -> str:
        """Get the login of the authenticated user."""
        if self._use_requests:
            resp = requests.get(f"{GITHUB_API}/user", headers=self._headers())
            if resp.ok:
                return resp.json().get("login", "unknown")
            return "unknown"
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    def list_open_prs(self, author: str = "app/dependabot") -> list[dict]:
        """List open PRs by author with status check info."""
        if self._use_requests:
            prs = []
            page = 1
            while True:
                resp = requests.get(
                    f"{GITHUB_API}/repos/{self.repo}/pulls",
                    headers=self._headers(),
                    params={"state": "open", "per_page": 50, "page": page},
                )
                if not resp.ok:
                    break
                batch = resp.json()
                if not batch:
                    break
                for pr in batch:
                    pr_login = pr.get("user", {}).get("login", "")
                    if author.startswith("app/"):
                        expected = author.split("/", 1)[1] + "[bot]"
                        if pr_login != expected:
                            continue
                    elif pr_login != author:
                        continue
                    prs.append({
                        "number": pr["number"],
                        "title": pr["title"],
                        "headRefName": pr["head"]["ref"],
                        "url": pr["html_url"],
                        "reviewDecision": self._get_review_decision(pr["number"]),
                        "statusCheckRollup": self._get_status_checks(pr["head"]["sha"]),
                    })
                page += 1
            return prs
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--author", author,
                "--state", "open",
                "--json", "number,title,headRefName,url,reviewDecision,statusCheckRollup",
                "--limit", "50",
            ],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return []

    def _get_review_decision(self, pr_number: int) -> str | None:
        """Get the review decision for a PR via GraphQL."""
        resp = requests.post(
            f"{GITHUB_API}/graphql",
            headers=self._headers(),
            json={
                "query": """query($owner:String!, $repo:String!, $number:Int!) {
                    repository(owner:$owner, name:$repo) {
                        pullRequest(number:$number) { reviewDecision }
                    }
                }""",
                "variables": {
                    "owner": self.repo.split("/")[0],
                    "repo": self.repo.split("/")[1],
                    "number": pr_number,
                },
            },
        )
        if resp.ok:
            data = resp.json()
            return (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewDecision")
            )
        return None

    def _get_status_checks(self, sha: str) -> list[dict]:
        """Get combined status checks for a commit SHA."""
        data = self._get(f"repos/{self.repo}/commits/{sha}/check-runs")
        if isinstance(data, dict):
            return [
                {
                    "name": cr.get("name"),
                    "conclusion": (cr.get("conclusion") or "").upper(),
                    "status": (cr.get("status") or "").upper(),
                }
                for cr in data.get("check_runs", [])
            ]
        return []

    def approve_pr(self, pr_number: int, comment: str) -> bool:
        """Approve a PR with a review comment."""
        if self._use_requests:
            resp = requests.post(
                f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}/reviews",
                headers=self._headers(),
                json={"body": comment, "event": "APPROVE"},
            )
            return resp.ok
        result = subprocess.run(
            ["gh", "pr", "review", str(pr_number), "--approve", "--body", comment],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def merge_pr(self, pr_number: int) -> tuple[bool, str]:
        """Merge a PR and delete the branch. Returns (success, error_msg)."""
        if self._use_requests:
            resp = requests.put(
                f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}/merge",
                headers=self._headers(),
                json={"merge_method": "merge"},
            )
            if not resp.ok:
                return False, resp.text
            pr_data = self._get(f"repos/{self.repo}/pulls/{pr_number}")
            if isinstance(pr_data, dict):
                branch = pr_data.get("head", {}).get("ref")
                if branch:
                    requests.delete(
                        f"{GITHUB_API}/repos/{self.repo}/git/refs/heads/{branch}",
                        headers=self._headers(),
                    )
            return True, ""
        result = subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--merge", "--delete-branch"],
            capture_output=True, text=True,
        )
        return result.returncode == 0, result.stderr.strip()
