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
from pathlib import Path
from unittest import mock

from verify_action_build.verification import (
    SECURITY_CHECKLIST_URL,
    show_verification_summary,
    verify_single_action,
)


class TestSecurityChecklistUrl:
    def test_url_is_valid(self):
        assert "github.com" in SECURITY_CHECKLIST_URL
        assert "apache/infrastructure-actions" in SECURITY_CHECKLIST_URL


class TestShowVerificationSummary:
    def test_basic_summary_no_crash(self):
        """Smoke test: ensure show_verification_summary runs without errors."""
        checks = [
            ("Action type detection", "info", "node20"),
            ("JS build verification", "pass", "compiled JS matches rebuild"),
        ]
        # Should not raise
        show_verification_summary(
            org="test",
            repo="repo",
            commit_hash="a" * 40,
            sub_path="",
            action_type="node20",
            is_js_action=True,
            all_match=True,
            non_js_warnings=None,
            checked_actions=None,
            checks_performed=checks,
        )

    def test_summary_with_nested_actions(self):
        checks = [
            ("Nested action analysis", "pass", "3 action(s) inspected"),
        ]
        nested = [
            {"action": "actions/checkout", "type": "composite", "pinned": True, "approved": True},
            {"action": "local-action", "type": "local", "pinned": True, "approved": True},
        ]
        show_verification_summary(
            org="test",
            repo="repo",
            commit_hash="a" * 40,
            sub_path="",
            action_type="composite",
            is_js_action=False,
            all_match=True,
            non_js_warnings=[],
            checked_actions=nested,
            checks_performed=checks,
        )

    def test_summary_with_warnings(self):
        checks = [
            ("Dockerfile analysis", "warn", "2 warning(s)"),
        ]
        show_verification_summary(
            org="test",
            repo="repo",
            commit_hash="a" * 40,
            sub_path="sub",
            action_type="docker",
            is_js_action=False,
            all_match=True,
            non_js_warnings=["warning 1", "warning 2"],
            checked_actions=None,
            checks_performed=checks,
        )


def _build_in_docker_result(action_type: str = "node20") -> tuple:
    """Return a fake build_in_docker return tuple with the right shape."""
    return (
        Path("/tmp/fake/original-dist"),
        Path("/tmp/fake/rebuilt-dist"),
        action_type,
        "dist",
        False,
        Path("/tmp/fake/original-node-modules"),
        Path("/tmp/fake/rebuilt-node-modules"),
    )


class TestVerifySingleActionLockFileRetry:
    """Regression tests for the approved-lock-file retry path.

    The retry path is a hard failure by design: if a clean rebuild from the
    current lock files does not reproduce the committed dist/, the action is
    not reproducible from its own tree — even if an older lock file would.
    """

    def _patch_stack(
        self,
        *,
        approved: list[dict],
        diff_js_side_effect,
        action_type: str = "node20",
        extra_patches: list | None = None,
    ):
        """Start a set of patches and return the started mocks keyed by short name."""
        patches = {
            "parse_action_ref": mock.patch(
                "verify_action_build.verification.parse_action_ref",
                return_value=("org", "repo", "", "c" * 40),
            ),
            "find_approved_versions": mock.patch(
                "verify_action_build.verification.find_approved_versions",
                return_value=approved,
            ),
            "build_in_docker": mock.patch(
                "verify_action_build.verification.build_in_docker",
                return_value=_build_in_docker_result(action_type=action_type),
            ),
            "diff_js_files": mock.patch(
                "verify_action_build.verification.diff_js_files",
                side_effect=diff_js_side_effect,
            ),
            "show_approved_versions": mock.patch(
                "verify_action_build.verification.show_approved_versions",
                return_value=None,
            ),
            "show_commits_between": mock.patch(
                "verify_action_build.verification.show_commits_between",
            ),
            "diff_approved_vs_new": mock.patch(
                "verify_action_build.verification.diff_approved_vs_new",
            ),
        }
        started = {name: p.start() for name, p in patches.items()}
        started["_patchers"] = list(patches.values())
        for extra in extra_patches or []:
            # extra is (name, patcher)
            name, patcher = extra
            started[name] = patcher.start()
            started["_patchers"].append(patcher)
        return started

    def _stop(self, started):
        for p in reversed(started["_patchers"]):
            p.stop()

    def test_clean_rebuild_passes(self):
        """Happy path: rebuild matches on the first attempt — no retry."""
        started = self._patch_stack(
            approved=[],
            diff_js_side_effect=[True],
        )
        try:
            result = verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
            assert started["build_in_docker"].call_count == 1
        finally:
            self._stop(started)
        assert result is True

    def test_mismatch_without_approved_versions_fails(self):
        """No approved history exists → no retry, hard failure."""
        started = self._patch_stack(
            approved=[],
            diff_js_side_effect=[False],
        )
        try:
            result = verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
            # No retry attempted — only the initial build ran.
            assert started["build_in_docker"].call_count == 1
        finally:
            self._stop(started)
        assert result is False

    def test_mismatch_with_approved_retries_and_still_fails_on_match(self):
        """Retry with approved lock files matches → reported as HARD FAILURE.

        Regression test for the policy change: previously this was a warning
        but still returned True; it must now return False so CI fails and the
        maintainer is forced to rebuild dist/ with the current lock files.
        """
        started = self._patch_stack(
            approved=[{"hash": "b" * 40, "version": "v1.0.0"}],
            # First diff: initial rebuild mismatch.  Second diff: retry with
            # approved lock files matches.
            diff_js_side_effect=[False, True],
        )
        try:
            result = verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
            build_mock = started["build_in_docker"]
            # Two docker builds: original + retry with approved_hash.
            assert build_mock.call_count == 2
            retry_call = build_mock.call_args_list[1]
            assert retry_call.kwargs.get("approved_hash") == "b" * 40
        finally:
            self._stop(started)
        assert result is False

    def test_mismatch_with_approved_retry_also_mismatches_fails(self):
        """Retry with approved lock files still differs → hard failure."""
        started = self._patch_stack(
            approved=[{"hash": "b" * 40, "version": "v1.0.0"}],
            diff_js_side_effect=[False, False],
        )
        try:
            result = verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
            assert started["build_in_docker"].call_count == 2
        finally:
            self._stop(started)
        assert result is False

    def test_js_check_reported_as_fail_when_only_lockfile_matches(self):
        """The summary row for JS build verification must carry status 'fail'."""
        captured: dict = {}

        def capture_summary(*args, **kwargs):
            # show_verification_summary has 10 positional params before ci_mode;
            # checks_performed is the 10th (index 9).
            captured["checks"] = kwargs.get("checks_performed") or args[9]

        summary_patch = mock.patch(
            "verify_action_build.verification.show_verification_summary",
            side_effect=capture_summary,
        )
        started = self._patch_stack(
            approved=[{"hash": "b" * 40, "version": "v1.0.0"}],
            diff_js_side_effect=[False, True],
            extra_patches=[("show_verification_summary", summary_patch)],
        )
        try:
            verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
        finally:
            self._stop(started)

        js_rows = [row for row in captured["checks"] if row[0] == "JS build verification"]
        assert len(js_rows) == 1
        assert js_rows[0][1] == "fail"
        assert "approved lock files" in js_rows[0][2]


class TestVerifySingleActionResultMessage:
    """The RESULT panel text must describe the actual failure cause."""

    def _patch_stack(
        self,
        *,
        approved: list[dict],
        diff_js_side_effect,
        binary_download_result,
        action_type: str = "node20",
    ):
        patches = {
            "parse_action_ref": mock.patch(
                "verify_action_build.verification.parse_action_ref",
                return_value=("org", "repo", "", "c" * 40),
            ),
            "find_approved_versions": mock.patch(
                "verify_action_build.verification.find_approved_versions",
                return_value=approved,
            ),
            "build_in_docker": mock.patch(
                "verify_action_build.verification.build_in_docker",
                return_value=_build_in_docker_result(action_type=action_type),
            ),
            "diff_js_files": mock.patch(
                "verify_action_build.verification.diff_js_files",
                side_effect=diff_js_side_effect,
            ),
            "show_approved_versions": mock.patch(
                "verify_action_build.verification.show_approved_versions",
                return_value=None,
            ),
            "show_commits_between": mock.patch(
                "verify_action_build.verification.show_commits_between",
            ),
            "diff_approved_vs_new": mock.patch(
                "verify_action_build.verification.diff_approved_vs_new",
            ),
            "analyze_binary_downloads_recursive": mock.patch(
                "verify_action_build.verification.analyze_binary_downloads_recursive",
                return_value=binary_download_result,
            ),
            "Panel": mock.patch("verify_action_build.verification.Panel"),
        }
        started = {name: p.start() for name, p in patches.items()}
        started["_patchers"] = list(patches.values())
        return started

    def _stop(self, started):
        for p in reversed(started["_patchers"]):
            p.stop()

    def _result_panel_message(self, panel_mock) -> str:
        result_calls = [
            c for c in panel_mock.call_args_list
            if c.kwargs.get("title") == "RESULT"
        ]
        assert len(result_calls) == 1, (
            f"expected exactly one RESULT panel, got {len(result_calls)}"
        )
        return result_calls[0].args[0]

    def test_js_action_with_unverified_download_reports_binary_cause(self):
        """Regression for the misleading RESULT panel seen on PR #724:

        A JS action that rebuilds cleanly but fails the binary-download
        check must surface the binary-download reason in the RESULT
        panel, not the default JS-mismatch message.
        """
        started = self._patch_stack(
            approved=[],
            diff_js_side_effect=[True],
            binary_download_result=(
                [],
                ["Dockerfile line 3: unverified download: curl -fsSL ..."],
            ),
        )
        try:
            result = verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
        finally:
            self._stop(started)

        assert result is False
        msg = self._result_panel_message(started["Panel"])
        assert "unverified binary download" in msg
        assert "Differences detected between published and rebuilt JS" not in msg

    def test_js_action_with_js_mismatch_reports_js_cause(self):
        """JS mismatch without binary-download failures still reports the
        JS-mismatch message (the original behaviour)."""
        started = self._patch_stack(
            approved=[],
            diff_js_side_effect=[False],
            binary_download_result=([], []),
        )
        try:
            result = verify_single_action("org/repo@" + "c" * 40, ci_mode=True)
        finally:
            self._stop(started)

        assert result is False
        msg = self._result_panel_message(started["Panel"])
        assert "Differences detected between published and rebuilt JS" in msg
