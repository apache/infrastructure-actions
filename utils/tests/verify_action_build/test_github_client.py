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
import subprocess
from unittest import mock

from verify_action_build.github_client import GitHubClient, gh_auth_token


class TestGhAuthToken:
    def test_returns_none_when_gh_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            assert gh_auth_token() is None

    def test_returns_stripped_token(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ghp_test123\n", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/gh"):
            with mock.patch("subprocess.run", return_value=completed):
                assert gh_auth_token() == "ghp_test123"

    def test_returns_none_when_gh_is_not_logged_in(self):
        with mock.patch("shutil.which", return_value="/usr/bin/gh"):
            with mock.patch(
                "subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")
            ):
                assert gh_auth_token() is None

    def test_returns_none_on_empty_output(self):
        completed = subprocess.CompletedProcess([], 0, stdout="\n", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/gh"):
            with mock.patch("subprocess.run", return_value=completed):
                assert gh_auth_token() is None


class TestGitHubClient:
    def test_init_with_token_uses_requests(self):
        client = GitHubClient(token="ghp_test123", repo="owner/repo")
        assert client._use_requests is True
        assert client.token == "ghp_test123"
        assert client.repo == "owner/repo"

    def test_init_without_token_uses_gh(self):
        client = GitHubClient(repo="owner/repo")
        assert client._use_requests is False
        assert client.token is None

    def test_headers(self):
        client = GitHubClient(token="ghp_test", repo="owner/repo")
        headers = client._headers()
        assert headers["Authorization"] == "token ghp_test"
        assert "application/vnd.github" in headers["Accept"]

    def test_get_commit_pulls_returns_empty_on_failure(self):
        client = GitHubClient(repo="owner/repo")
        with mock.patch.object(client, "_get", return_value=None):
            result = client.get_commit_pulls("owner", "repo", "abc123")
        assert result == []

    def test_get_commit_pulls_returns_list(self):
        client = GitHubClient(repo="owner/repo")
        mock_data = [{"number": 1, "title": "Test PR"}]
        with mock.patch.object(client, "_get", return_value=mock_data):
            result = client.get_commit_pulls("owner", "repo", "abc123")
        assert result == mock_data

    def test_compare_commits_returns_commits(self):
        client = GitHubClient(repo="owner/repo")
        mock_data = {"commits": [{"sha": "abc"}]}
        with mock.patch.object(client, "_get", return_value=mock_data):
            result = client.compare_commits("owner", "repo", "base", "head")
        assert result == [{"sha": "abc"}]

    def test_compare_commits_returns_empty_on_failure(self):
        client = GitHubClient(repo="owner/repo")
        with mock.patch.object(client, "_get", return_value=None):
            result = client.compare_commits("owner", "repo", "base", "head")
        assert result == []

    def test_get_status_checks_parses_check_runs(self):
        client = GitHubClient(repo="owner/repo")
        mock_data = {
            "check_runs": [
                {"name": "test", "conclusion": "success", "status": "completed"},
                {"name": "lint", "conclusion": None, "status": "in_progress"},
            ]
        }
        with mock.patch.object(client, "_get", return_value=mock_data):
            result = client._get_status_checks("abc123")
        assert len(result) == 2
        assert result[0]["name"] == "test"
        assert result[0]["conclusion"] == "SUCCESS"

    def test_get_status_checks_empty_on_failure(self):
        client = GitHubClient(repo="owner/repo")
        with mock.patch.object(client, "_get", return_value=None):
            result = client._get_status_checks("abc123")
        assert result == []
