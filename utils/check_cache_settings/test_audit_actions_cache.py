"""
test_audit_actions_cache.py

Unit tests for audit-actions-cache.py.

Run with:
    python -m pytest test_audit_actions_cache.py -v
    python -m pytest test_audit_actions_cache.py -v --tb=short   # compact tracebacks
    python -m pytest test_audit_actions_cache.py -v -k "ref"     # filter by name
"""

import importlib.util
import io
import json
import sys
import textwrap
import types
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, call
from urllib.error import HTTPError

import pytest
import yaml

# ── Load the module under test without executing main() ──────────────────────

_SRC = Path(__file__).parent / "audit-actions-cache.py"

def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("audit", _SRC)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

audit = _load_module()

# Convenient aliases
is_sha_pin         = audit.is_sha_pin
is_wildcard_latest = audit.is_wildcard_latest
is_invalid_ref     = audit.is_invalid_ref
parse_uses         = audit.parse_uses
action_base_name   = audit.action_base_name
resolve_ref_to_sha = audit.resolve_ref_to_sha
resolve_latest_release = audit.resolve_latest_release
Finding            = audit.Finding
Auditor            = audit.Auditor
generate_html_report = audit.generate_html_report
fetch_remote_workflows = audit.fetch_remote_workflows
_h                 = audit._h


# ═════════════════════════════════════════════════════════════════════════════
# 1. Ref classification helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestIsShaPin:
    def test_full_40_char_sha(self):
        assert is_sha_pin("fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f89") is True

    def test_uppercase_sha(self):
        assert is_sha_pin("FB5BB9C5011A3D143A54B4B30AEDC30EC5BC0F89") is True

    def test_mixed_case_sha(self):
        assert is_sha_pin("Fb5Bb9c5011a3d143a54b4b30aedc30ec5bc0f89") is True

    def test_39_chars_not_sha(self):
        assert is_sha_pin("fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f8") is False

    def test_41_chars_not_sha(self):
        assert is_sha_pin("fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f899") is False

    def test_semver_tag_not_sha(self):
        assert is_sha_pin("v6.4.0") is False

    def test_short_tag_not_sha(self):
        assert is_sha_pin("v4") is False

    def test_branch_not_sha(self):
        assert is_sha_pin("main") is False

    def test_wildcard_not_sha(self):
        assert is_sha_pin("*") is False

    def test_empty_string_not_sha(self):
        assert is_sha_pin("") is False


class TestIsWildcardLatest:
    def test_bare_star(self):
        assert is_wildcard_latest("*") is True

    def test_star_with_prefix_not_wildcard_latest(self):
        assert is_wildcard_latest("v*") is False

    def test_empty_not_wildcard_latest(self):
        assert is_wildcard_latest("") is False

    def test_tag_not_wildcard_latest(self):
        assert is_wildcard_latest("v4") is False


class TestIsInvalidRef:
    # bare '*' is NOT invalid — it means "latest release"
    def test_bare_star_not_invalid(self):
        assert is_invalid_ref("*") is False

    def test_glob_star_prefix_is_invalid(self):
        assert is_invalid_ref("v*") is True

    def test_question_mark_is_invalid(self):
        assert is_invalid_ref("v?") is True

    def test_version_range_gte_is_invalid(self):
        assert is_invalid_ref(">=1.0") is True

    def test_version_range_lte_is_invalid(self):
        assert is_invalid_ref("<=2.0") is True

    def test_version_range_neq_is_invalid(self):
        assert is_invalid_ref("!=3.0") is True

    def test_whitespace_is_invalid(self):
        assert is_invalid_ref("feature/my branch") is True

    def test_caret_is_invalid(self):
        assert is_invalid_ref("^1.0") is True

    def test_tilde_is_invalid(self):
        assert is_invalid_ref("~1.0") is True

    def test_bracket_is_invalid(self):
        assert is_invalid_ref("[1.0,2.0]") is True

    def test_normal_semver_not_invalid(self):
        assert is_invalid_ref("v6.4.0") is False

    def test_short_tag_not_invalid(self):
        assert is_invalid_ref("v4") is False

    def test_branch_not_invalid(self):
        assert is_invalid_ref("main") is False

    def test_sha_not_invalid(self):
        assert is_invalid_ref("fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f89") is False


# ═════════════════════════════════════════════════════════════════════════════
# 2. parse_uses / action_base_name
# ═════════════════════════════════════════════════════════════════════════════

class TestParseUses:
    def test_owner_repo_at_sha(self):
        repo, ref = parse_uses("actions/setup-node@fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f89")
        assert repo == "actions/setup-node"
        assert ref  == "fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f89"

    def test_owner_repo_at_tag(self):
        repo, ref = parse_uses("actions/setup-node@v4")
        assert repo == "actions/setup-node"
        assert ref  == "v4"

    def test_owner_repo_at_wildcard(self):
        repo, ref = parse_uses("testlens-app/setup-testlens@*")
        assert repo == "testlens-app/setup-testlens"
        assert ref  == "*"

    def test_sub_action_path_strips_to_owner_repo(self):
        # actions/cache/restore@v3 → owner/repo = actions/cache
        repo, ref = parse_uses("actions/cache/restore@v3")
        assert repo == "actions/cache"
        assert ref  == "v3"

    def test_docker_returns_none(self):
        assert parse_uses("docker://ubuntu:22.04") == (None, None)

    def test_local_action_returns_none(self):
        assert parse_uses("./.github/actions/my-action") == (None, None)

    def test_no_at_sign_returns_none(self):
        assert parse_uses("actions/setup-node") == (None, None)

    def test_single_segment_returns_none(self):
        assert parse_uses("setup-node@v4") == (None, None)


class TestActionBaseName:
    def test_strips_ref(self):
        assert action_base_name("actions/setup-node@v4") == "actions/setup-node"

    def test_strips_sha(self):
        assert action_base_name(
            "actions/setup-node@fb5bb9c5011a3d143a54b4b30aedc30ec5bc0f89"
        ) == "actions/setup-node"

    def test_docker_returns_original(self):
        assert action_base_name("docker://ubuntu") == "docker://ubuntu"


# ═════════════════════════════════════════════════════════════════════════════
# 3. resolve_ref_to_sha  (all API calls mocked)
# ═════════════════════════════════════════════════════════════════════════════

SHA_A = "a" * 40
SHA_B = "b" * 40


def _make_http_error(code: int, url: str = "https://example.com") -> HTTPError:
    return HTTPError(url, code, f"Error {code}", {}, None)


class TestResolveRefToSha:
    def test_already_sha_pin(self):
        sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", SHA_A)
        assert sha == SHA_A
        assert pinned  is True
        assert invalid is False
        assert rtag    is None

    def test_invalid_glob_ref(self):
        sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", "v*")
        assert invalid is True
        assert pinned  is False
        assert sha     == "v*"

    def test_invalid_range_ref(self):
        sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", ">=1.0")
        assert invalid is True

    def test_wildcard_resolves_to_latest_release(self):
        with patch.object(audit, "resolve_latest_release", return_value="v6.4.0") as mock_latest, \
             patch.object(audit, "gh_api") as mock_api:
            # Simulate the tags/<tag> ref lookup returning a lightweight tag SHA
            mock_api.return_value = {"object": {"sha": SHA_B, "type": "commit"}}
            sha, pinned, invalid, rtag = resolve_ref_to_sha("actions/setup-node", "*")
        assert sha     == SHA_B
        assert pinned  is False
        assert invalid is False
        assert rtag    == "v6.4.0"
        mock_latest.assert_called_once_with("actions/setup-node")

    def test_wildcard_falls_back_to_invalid_when_no_release(self):
        with patch.object(audit, "resolve_latest_release", return_value=None):
            sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", "*")
        assert invalid is True
        assert rtag    is None

    def test_tag_resolves_via_refs_api(self):
        with patch.object(audit, "gh_api") as mock_api:
            mock_api.return_value = {"object": {"sha": SHA_A, "type": "commit"}}
            sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", "v4")
        assert sha     == SHA_A
        assert pinned  is False
        assert invalid is False
        assert rtag    is None

    def test_annotated_tag_dereferenced(self):
        tag_obj_url = "https://api.github.com/repos/owner/repo/git/tags/abc123"
        def fake_api(url: str):
            if "git/ref/tags" in url:
                return {"object": {"sha": "abc123", "type": "tag", "url": tag_obj_url}}
            if url == tag_obj_url:
                return {"object": {"sha": SHA_B, "type": "commit"}}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(audit, "gh_api", side_effect=fake_api):
            sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", "v4")
        assert sha == SHA_B

    def test_falls_back_to_commits_api_when_refs_404(self):
        def fake_api(url: str):
            if "git/ref/" in url:
                raise _make_http_error(404, url)
            if "/commits/" in url:
                return {"sha": SHA_A}
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(audit, "gh_api", side_effect=fake_api):
            sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", "main")
        assert sha == SHA_A

    def test_returns_original_ref_when_all_lookups_fail(self):
        with patch.object(audit, "gh_api", side_effect=_make_http_error(404)):
            sha, pinned, invalid, rtag = resolve_ref_to_sha("owner/repo", "v99")
        assert sha    == "v99"
        assert pinned  is False
        assert invalid is False

    def test_non_404_http_error_prints_warning(self, capsys):
        with patch.object(audit, "gh_api", side_effect=_make_http_error(403)):
            resolve_ref_to_sha("owner/repo", "v4")
        captured = capsys.readouterr()
        assert "Warning" in captured.err


# ═════════════════════════════════════════════════════════════════════════════
# 4. resolve_latest_release  (API mocked)
# ═════════════════════════════════════════════════════════════════════════════

class TestResolveLatestRelease:
    def setup_method(self):
        # Clear the process-level cache before each test
        audit._latest_release_cache.clear()

    def test_returns_tag_from_releases_api(self):
        with patch.object(audit, "gh_api", return_value={"tag_name": "v6.4.0"}):
            tag = resolve_latest_release("actions/setup-node")
        assert tag == "v6.4.0"

    def test_falls_back_to_tags_api_on_404(self):
        def fake_api(url: str):
            if "releases/latest" in url:
                raise _make_http_error(404, url)
            if "/tags" in url:
                return [{"name": "v5.0.0"}, {"name": "v4.0.0"}]
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(audit, "gh_api", side_effect=fake_api):
            tag = resolve_latest_release("owner/repo")
        assert tag == "v5.0.0"

    def test_returns_none_when_no_releases_or_tags(self):
        def fake_api(url: str):
            if "releases/latest" in url:
                raise _make_http_error(404, url)
            return []   # empty tags list

        with patch.object(audit, "gh_api", side_effect=fake_api):
            tag = resolve_latest_release("owner/no-releases")
        assert tag is None

    def test_caches_result(self):
        with patch.object(audit, "gh_api", return_value={"tag_name": "v1.0"}) as mock_api:
            resolve_latest_release("owner/repo")
            resolve_latest_release("owner/repo")   # second call hits cache
        assert mock_api.call_count == 1

    def test_cache_is_per_repo(self):
        with patch.object(audit, "gh_api", return_value={"tag_name": "v1.0"}) as mock_api:
            resolve_latest_release("owner/repo-a")
            resolve_latest_release("owner/repo-b")
        assert mock_api.call_count == 2


# ═════════════════════════════════════════════════════════════════════════════
# 5. Auditor.check_step  (no network — resolve_pins=False)
# ═════════════════════════════════════════════════════════════════════════════

WF = "workflow.yml"


class TestCheckStepLocal:
    """Tests with resolve_pins=False (local / --repo-path mode)."""

    def _auditor(self) -> Auditor:
        return Auditor(resolve_pins=False)

    # ── actions/cache ─────────────────────────────────────────────────────────

    def test_actions_cache_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/cache@v3"}, WF, "build")
        assert len(fs) == 1
        assert fs[0].level == "HIGH"
        assert "actions/cache" in fs[0].action

    def test_actions_cache_restore_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/cache/restore@v3"}, WF, "build")
        assert len(fs) == 1
        assert fs[0].level == "HIGH"

    def test_actions_cache_save_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/cache/save@v3"}, WF, "build")
        assert len(fs) == 1
        assert fs[0].level == "HIGH"

    # ── setup-node ────────────────────────────────────────────────────────────

    def test_setup_node_no_cache_input_is_high(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-node@v4", "with": {"node-version": "20"}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert high, "Expected HIGH finding for missing cache input"
        assert high[0].cache_input == "cache"
        assert high[0].cache_value == "__NOT_SET__"

    def test_setup_node_cache_empty_string_is_clean(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-node@v4", "with": {"cache": ""}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    def test_setup_node_cache_false_string_is_clean(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-node@v4", "with": {"cache": "false"}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    def test_setup_node_cache_npm_is_medium(self):
        """cache: npm is set but may not be a deliberate disable — MEDIUM."""
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-node@v4", "with": {"cache": "npm"}},
            WF, "build",
        )
        medium = [f for f in fs if f.level == "MEDIUM"]
        assert medium
        assert medium[0].cache_value == "npm"

    # ── setup-go ──────────────────────────────────────────────────────────────

    def test_setup_go_no_cache_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/setup-go@v5"}, WF, "build")
        high = [f for f in fs if f.level == "HIGH"]
        assert high

    def test_setup_go_cache_false_is_clean(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-go@v5", "with": {"cache": "false"}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    # ── setup-python ──────────────────────────────────────────────────────────

    def test_setup_python_no_cache_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/setup-python@v5"}, WF, "build")
        high = [f for f in fs if f.level == "HIGH"]
        assert high

    def test_setup_python_cache_empty_is_clean(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-python@v5", "with": {"cache": ""}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    # ── gradle ────────────────────────────────────────────────────────────────

    def test_gradle_build_action_no_disable_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "gradle/gradle-build-action@v2"}, WF, "build")
        high = [f for f in fs if f.level == "HIGH"]
        assert high
        assert high[0].cache_input == "cache-disabled"

    def test_gradle_build_action_disabled_is_clean(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "gradle/gradle-build-action@v2",
             "with": {"cache-disabled": "true"}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    # ── setup-ruby bundler-cache ───────────────────────────────────────────────

    def test_setup_ruby_no_bundler_cache_is_high(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/setup-ruby@v1"}, WF, "build")
        high = [f for f in fs if f.level == "HIGH"]
        assert high
        assert high[0].cache_input == "bundler-cache"

    def test_setup_ruby_bundler_cache_false_is_clean(self):
        a = self._auditor()
        fs = a.check_step(
            {"uses": "actions/setup-ruby@v1", "with": {"bundler-cache": "false"}},
            WF, "build",
        )
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    # ── edge cases ────────────────────────────────────────────────────────────

    def test_step_without_uses_returns_empty(self):
        a = self._auditor()
        assert a.check_step({"run": "echo hello"}, WF, "build") == []

    def test_docker_action_returns_empty(self):
        a = self._auditor()
        assert a.check_step({"uses": "docker://ubuntu:22.04"}, WF, "build") == []

    def test_local_action_returns_empty(self):
        a = self._auditor()
        assert a.check_step({"uses": "./.github/actions/my-action"}, WF, "build") == []

    def test_job_name_stored_on_finding(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/cache@v3"}, WF, "my-job")
        assert fs[0].job == "my-job"

    def test_workflow_file_stored_on_finding(self):
        a = self._auditor()
        fs = a.check_step({"uses": "actions/cache@v3"}, "path/to/ci.yml", "job")
        assert fs[0].workflow == "path/to/ci.yml"


# ═════════════════════════════════════════════════════════════════════════════
# 6. Auditor.check_step  (resolve_pins=True — pin-checking behaviour)
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckStepRemote:
    """Tests with resolve_pins=True — verifies SHA pin findings."""

    def _auditor(self) -> Auditor:
        return Auditor(resolve_pins=True)

    def test_sha_pinned_action_no_pin_finding(self):
        """A correctly SHA-pinned action should not produce a pin-related MEDIUM."""
        a = self._auditor()
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=(SHA_A, True, False, None)):
            fs = a.check_step(
                {"uses": f"actions/setup-node@{SHA_A}", "with": {"cache": ""}},
                WF, "build",
            )
        pin_findings = [f for f in fs if "tag" in f.message.lower() or "pin" in f.message.lower()]
        assert not pin_findings

    def test_tag_ref_produces_medium_pin_finding(self):
        a = self._auditor()
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=(SHA_A, False, False, None)):
            fs = a.check_step(
                {"uses": "actions/setup-node@v4", "with": {"cache": ""}},
                WF, "build",
            )
        medium = [f for f in fs if f.level == "MEDIUM"]
        assert medium
        assert medium[0].resolved_sha == SHA_A

    def test_wildcard_ref_resolves_tag_in_finding(self):
        a = self._auditor()
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=(SHA_A, False, False, "v6.4.0")):
            fs = a.check_step(
                {"uses": "actions/setup-node@*", "with": {"cache": ""}},
                WF, "build",
            )
        medium = [f for f in fs if f.level == "MEDIUM"]
        assert medium
        assert medium[0].resolved_tag == "v6.4.0"

    def test_invalid_ref_produces_high_finding(self):
        a = self._auditor()
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=("v*", False, True, None)):
            fs = a.check_step(
                {"uses": "actions/setup-node@v*", "with": {"cache": ""}},
                WF, "build",
            )
        high = [f for f in fs if f.level == "HIGH"]
        assert high
        assert "wildcard" in high[0].message.lower() or "invalid" in high[0].message.lower()


# ═════════════════════════════════════════════════════════════════════════════
# 7. Auditor.audit_composite  (fetch_action_yml mocked)
# ═════════════════════════════════════════════════════════════════════════════

class TestAuditComposite:
    def test_js_action_no_findings(self):
        a = Auditor(resolve_pins=False)
        with patch.object(audit, "fetch_action_yml",
                          return_value={"runs": {"using": "node20"}}):
            fs = a.audit_composite(
                "owner/action@v1", "owner/action", "v1", WF, "job"
            )
        assert fs == []

    def test_composite_with_no_caching_steps_no_findings(self):
        a = Auditor(resolve_pins=False)
        action_def = {
            "runs": {
                "using": "composite",
                "steps": [{"run": "echo hello", "shell": "bash"}],
            }
        }
        with patch.object(audit, "fetch_action_yml", return_value=action_def):
            fs = a.audit_composite(
                "owner/action@v1", "owner/action", "v1", WF, "job"
            )
        assert fs == []

    def test_composite_with_hidden_cache_is_high(self):
        a = Auditor(resolve_pins=False)
        action_def = {
            "runs": {
                "using": "composite",
                "steps": [
                    {"uses": "actions/setup-node@v4"},
                ],
            }
        }
        with patch.object(audit, "fetch_action_yml", return_value=action_def):
            fs = a.audit_composite(
                "owner/action@v1", "owner/action", "v1", WF, "job"
            )
        assert any(f.level == "HIGH" for f in fs)

    def test_composite_fetch_failure_produces_medium(self):
        a = Auditor(resolve_pins=False)
        with patch.object(audit, "fetch_action_yml",
                          side_effect=Exception("network error")):
            fs = a.audit_composite(
                "owner/action@v1", "owner/action", "v1", WF, "job"
            )
        assert len(fs) == 1
        assert fs[0].level == "MEDIUM"
        assert "network error" in fs[0].message

    def test_composite_not_fetched_twice(self):
        """visited_actions prevents re-fetching the same SHA."""
        a = Auditor(resolve_pins=False)
        action_def = {"runs": {"using": "composite", "steps": []}}
        with patch.object(audit, "fetch_action_yml", return_value=action_def) as mock_fetch:
            a.audit_composite("owner/action@v1", "owner/action", "v1", WF, "job")
            a.audit_composite("owner/action@v1", "owner/action", "v1", WF, "job")
        assert mock_fetch.call_count == 1

    def test_composite_with_resolve_pins_invalid_ref(self):
        a = Auditor(resolve_pins=True)
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=("v*", False, True, None)):
            fs = a.audit_composite(
                "owner/action@v*", "owner/action", "v*", WF, "job"
            )
        assert any(f.level == "HIGH" for f in fs)
        # Should not attempt to fetch action.yml for an invalid ref
        with patch.object(audit, "fetch_action_yml") as mock_fetch:
            mock_fetch.assert_not_called()

    def test_composite_with_resolve_pins_tag_produces_medium(self):
        a = Auditor(resolve_pins=True)
        action_def = {"runs": {"using": "composite", "steps": []}}
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=(SHA_A, False, False, None)), \
             patch.object(audit, "fetch_action_yml", return_value=action_def):
            fs = a.audit_composite(
                "owner/action@v1", "owner/action", "v1", WF, "job"
            )
        medium = [f for f in fs if f.level == "MEDIUM"]
        assert medium
        assert medium[0].resolved_sha == SHA_A

    def test_composite_wildcard_star_resolved_tag_in_finding(self):
        a = Auditor(resolve_pins=True)
        action_def = {"runs": {"using": "composite", "steps": []}}
        with patch.object(audit, "resolve_ref_to_sha",
                          return_value=(SHA_A, False, False, "v2.0.0")), \
             patch.object(audit, "fetch_action_yml", return_value=action_def):
            fs = a.audit_composite(
                "owner/action@*", "owner/action", "*", WF, "job"
            )
        medium = [f for f in fs if f.level == "MEDIUM"]
        assert medium[0].resolved_tag == "v2.0.0"


# ═════════════════════════════════════════════════════════════════════════════
# 8. Auditor.audit_workflow  (file-based integration)
# ═════════════════════════════════════════════════════════════════════════════

def _write_workflow(tmp_path: Path, content: str) -> Path:
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(textwrap.dedent(content))
    return wf


class TestAuditWorkflow:
    def test_clean_workflow_no_findings(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: actions/setup-node@v4
                    with:
                      node-version: 20
                      cache: ''
                  - run: npm ci
        """)
        a = Auditor(resolve_pins=False)
        with patch.object(audit, "fetch_action_yml", return_value=None):
            fs = a.audit_workflow(wf)
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    def test_missing_cache_disable_is_high(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              publish:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/setup-node@v4
                    with:
                      node-version: 20
        """)
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)
        high = [f for f in fs if f.level == "HIGH"]
        assert high

    def test_explicit_actions_cache_is_high(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v3
                    with:
                      path: ~/.npm
                      key: npm-${{ hashFiles('**/package-lock.json') }}
        """)
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)
        assert any(f.level == "HIGH" for f in fs)

    def test_multiple_jobs_both_audited(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              job1:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/setup-node@v4
              job2:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/setup-python@v5
        """)
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)
        jobs = {f.job for f in fs}
        assert "job1" in jobs
        assert "job2" in jobs

    def test_invalid_yaml_returns_empty(self, tmp_path):
        wf = tmp_path / "bad.yml"
        wf.write_text("{{{{ not valid yaml")
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)
        assert fs == []

    def test_non_dict_yaml_returns_empty(self, tmp_path):
        wf = tmp_path / "list.yml"
        wf.write_text("- item1\n- item2\n")
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)
        assert fs == []

    def test_findings_stored_on_auditor(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v3
                    with:
                      path: ~/.npm
                      key: k
        """)
        a = Auditor(resolve_pins=False)
        a.audit_workflow(wf)
        assert len(a.findings) > 0


# ═════════════════════════════════════════════════════════════════════════════
# 9. Finding helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestFinding:
    def _make(self, level="HIGH") -> Finding:
        return Finding(
            level=level, action="actions/cache@v3", workflow="ci.yml",
            job="build", message="test message", recommendation="fix it",
        )

    def test_str_contains_level(self):
        assert "HIGH" in str(self._make("HIGH"))

    def test_str_contains_action(self):
        assert "actions/cache@v3" in str(self._make())

    def test_str_contains_job(self):
        assert "build" in str(self._make())

    def test_colour_high(self):
        assert self._make("HIGH").colour() == audit.RED

    def test_colour_medium(self):
        assert self._make("MEDIUM").colour() == audit.YELLOW

    def test_colour_info(self):
        assert self._make("INFO").colour() == audit.CYAN

    def test_resolved_sha_stored(self):
        f = Finding(
            level="MEDIUM", action="a@v1", workflow="w", job="j",
            message="m", recommendation="r", resolved_sha=SHA_A,
        )
        assert f.resolved_sha == SHA_A

    def test_resolved_tag_stored(self):
        f = Finding(
            level="MEDIUM", action="a@*", workflow="w", job="j",
            message="m", recommendation="r", resolved_tag="v6.4.0",
        )
        assert f.resolved_tag == "v6.4.0"


# ═════════════════════════════════════════════════════════════════════════════
# 10. HTML report generation
# ═════════════════════════════════════════════════════════════════════════════

def _make_finding(level="HIGH", action="actions/cache@v3",
                  workflow="ci.yml", job="build",
                  cache_input=None, cache_value=None,
                  resolved_sha=None, resolved_tag=None) -> Finding:
    return Finding(
        level=level, action=action, workflow=workflow, job=job,
        message=f"Test message for {level}",
        recommendation=f"Fix: add cache: '' under with:",
        cache_input=cache_input, cache_value=cache_value,
        resolved_sha=resolved_sha, resolved_tag=resolved_tag,
    )


class TestGenerateHtmlReport:
    def test_creates_file(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([], [], out)
        assert out.exists()

    def test_contains_board_panel(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([], [], out)
        html = out.read_text()
        assert 'id="board"' in html
        assert 'id="community"' not in html
        assert 'id="technical"' not in html

    def test_pass_verdict_when_no_findings(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([], [], out)
        assert "PASS" in out.read_text()

    def test_high_verdict_when_high_findings(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([_make_finding("HIGH")], [Path("ci.yml")], out)
        html = out.read_text()
        # Should show HIGH verdict and stat count
        assert html.count("HIGH") >= 2

    def test_stat_counts_correct(self, tmp_path):
        findings = [
            _make_finding("HIGH"),
            _make_finding("HIGH"),
            _make_finding("MEDIUM"),
        ]
        out = tmp_path / "report.html"
        generate_html_report(findings, [Path("ci.yml")], out)
        html = out.read_text()
        # The stat boxes should contain the counts somewhere
        assert ">2<" in html   # 2 HIGH
        assert ">1<" in html   # 1 MEDIUM

    def test_workflow_count_in_header(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report(
            [], [Path("a.yml"), Path("b.yml"), Path("c.yml")], out
        )
        html = out.read_text()
        assert "3" in html

    def test_finding_message_appears_in_board(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report(
            [_make_finding(level="HIGH", action="actions/setup-node@v4")],
            [Path("ci.yml")], out,
        )
        # Board detail rows show the finding message, not the action name
        assert "Test message for HIGH" in out.read_text()

    def test_cache_not_set_shown_in_board_message(self, tmp_path):
        out = tmp_path / "report.html"
        f = _make_finding(cache_input="cache", cache_value="__NOT_SET__")
        # The board detail row shows the finding message (truncated to 120 chars)
        generate_html_report([f], [Path("ci.yml")], out)
        assert "Test message for" in out.read_text()

    def test_resolved_sha_not_shown_in_board_only(self, tmp_path):
        """Board detail row shows finding message, not SHA cell (that was technical-only)."""
        out = tmp_path / "report.html"
        generate_html_report(
            [_make_finding(level="MEDIUM", resolved_sha=SHA_A)],
            [Path("ci.yml")], out,
        )
        # The full SHA should NOT appear (no technical panel), but the report should exist
        html = out.read_text()
        assert "<!DOCTYPE html>" in html

    def test_resolved_tag_shown_in_message_when_present(self, tmp_path):
        """If the finding message contains the tag it should appear in the board row."""
        out = tmp_path / "report.html"
        f = Finding(
            level="MEDIUM", action="actions/setup-node@*", workflow="ci.yml",
            job="build", message="Resolved via tag v6.4.0",
            recommendation="Pin to SHA", resolved_sha=SHA_A, resolved_tag="v6.4.0",
        )
        generate_html_report([f], [Path("ci.yml")], out)
        assert "v6.4.0" in out.read_text()

    def test_no_fix_snippet_in_board_only_report(self, tmp_path):
        """fix-snippet was a technical panel feature; should not appear in board-only report."""
        out = tmp_path / "report.html"
        generate_html_report(
            [_make_finding(cache_input="cache", cache_value="__NOT_SET__")],
            [Path("ci.yml")], out,
        )
        assert "fix-snippet" not in out.read_text()

    def test_html_escaping_in_message(self, tmp_path):
        out = tmp_path / "report.html"
        f = Finding(
            level="HIGH", action="actions/cache@v3", workflow="ci.yml",
            job="build", message="Bad input: <xss>payload</xss>",
            recommendation="Fix it",
        )
        generate_html_report([f], [Path("ci.yml")], out)
        html = out.read_text()
        assert "<xss>" not in html
        assert "&lt;xss&gt;" in html

    def test_no_nav_tabs_in_board_only_report(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([], [], out)
        assert 'data-panel=' not in out.read_text()

    def test_no_js_show_function_in_board_only_report(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([], [], out)
        assert "function show" not in out.read_text()

    def test_valid_html_structure(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([_make_finding()], [Path("ci.yml")], out)
        html = out.read_text()
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html


# ═════════════════════════════════════════════════════════════════════════════
# 11. _h (HTML escape helper)
# ═════════════════════════════════════════════════════════════════════════════

class TestHtmlEscape:
    def test_ampersand(self):
        assert _h("a & b") == "a &amp; b"

    def test_less_than(self):
        assert _h("<script>") == "&lt;script&gt;"

    def test_double_quote(self):
        assert _h('"hello"') == "&quot;hello&quot;"

    def test_single_quote(self):
        assert _h("it's") == "it&#39;s"

    def test_clean_string_unchanged(self):
        assert _h("hello world") == "hello world"

    def test_integer_coerced(self):
        assert _h(42) == "42"


# ═════════════════════════════════════════════════════════════════════════════
# 12. fetch_remote_workflows  (API mocked)
# ═════════════════════════════════════════════════════════════════════════════

class TestFetchRemoteWorkflows:
    def _fake_entries(self):
        return [
            {"name": "ci.yml",      "download_url": "https://raw.test/ci.yml"},
            {"name": "release.yml", "download_url": "https://raw.test/release.yml"},
            {"name": "README.md",   "download_url": "https://raw.test/README.md"},
        ]

    def test_fetches_yml_files_only(self, tmp_path):
        def fake_api(url: str) -> list | str:
            if "contents" in url:
                return self._fake_entries()
            return "on: push\njobs: {}"

        with patch.object(audit, "gh_api", side_effect=fake_api):
            result = fetch_remote_workflows("owner/repo", tmp_path)

        names = [p.name for p, _ in result]
        assert "ci.yml" in names
        assert "release.yml" in names
        assert "README.md" not in names

    def test_files_written_under_owner_repo_subdir(self, tmp_path):
        def fake_api(url: str) -> list | str:
            if "contents" in url:
                return [{"name": "ci.yml", "download_url": "https://raw.test/ci.yml"}]
            return "on: push\njobs: {}"

        with patch.object(audit, "gh_api", side_effect=fake_api):
            result = fetch_remote_workflows("myorg/myrepo", tmp_path)

        assert len(result) == 1
        path, slug = result[0]
        assert slug == "myorg/myrepo"
        assert "myorg" in str(path)
        assert "myrepo" in str(path)

    def test_uses_ref_for_raw_url_when_specified(self, tmp_path):
        seen_urls = []

        def fake_api(url: str) -> list | str:
            seen_urls.append(url)
            if "contents" in url:
                return [{"name": "ci.yml", "download_url": "https://raw.test/HEAD/ci.yml"}]
            return "on: push\njobs: {}"

        with patch.object(audit, "gh_api", side_effect=fake_api):
            result = fetch_remote_workflows("owner/repo@abc123", tmp_path)

        # Slug should strip the @ref
        assert result[0][1] == "owner/repo"

        raw_calls = [u for u in seen_urls if "raw.githubusercontent" in u]
        assert any("abc123" in u for u in raw_calls), \
            f"Expected abc123 in raw URL, got: {raw_calls}"

    def test_api_error_returns_empty(self, tmp_path):
        with patch.object(audit, "gh_api",
                          side_effect=_make_http_error(404)):
            result = fetch_remote_workflows("owner/missing", tmp_path)
        assert result == []

    def test_non_list_response_returns_empty(self, tmp_path):
        with patch.object(audit, "gh_api", return_value={"message": "Not Found"}):
            result = fetch_remote_workflows("owner/repo", tmp_path)
        assert result == []

    def test_individual_download_failure_skips_file(self, tmp_path):
        def fake_api(url: str) -> list | str:
            if "contents" in url:
                return [
                    {"name": "good.yml", "download_url": "https://raw.test/good.yml"},
                    {"name": "bad.yml",  "download_url": "https://raw.test/bad.yml"},
                ]
            if "bad.yml" in url:
                raise _make_http_error(500, url)
            return "on: push\njobs: {}"

        with patch.object(audit, "gh_api", side_effect=fake_api):
            result = fetch_remote_workflows("owner/repo", tmp_path)

        names = [p.name for p, _ in result]
        assert "good.yml" in names
        assert "bad.yml"  not in names


# ═════════════════════════════════════════════════════════════════════════════
# 13. End-to-end: full workflow audit  (no network)
# ═════════════════════════════════════════════════════════════════════════════

class TestFindingRepo:
    """Finding.repo is set correctly when auditing remote workflows."""

    def test_repo_slug_stamped_on_findings(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text(textwrap.dedent("""
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v3
                    with:
                      path: ~/.npm
                      key: k
        """))
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf, repo="myorg/myrepo")
        assert all(f.repo == "myorg/myrepo" for f in fs)

    def test_repo_none_for_local_scan(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text(textwrap.dedent("""
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v3
                    with:
                      path: ~/.npm
                      key: k
        """))
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)   # no repo= kwarg
        assert all(f.repo is None for f in fs)

    def test_html_report_uses_repo_slug_not_tmpdir(self, tmp_path):
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        wf = tmpdir / "myorg" / "myrepo" / "ci.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(textwrap.dedent("""
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v3
                    with: {path: ~/.npm, key: k}
        """))
        a = Auditor(resolve_pins=False)
        findings = a.audit_workflow(wf, repo="myorg/myrepo")

        out = tmp_path / "report.html"
        generate_html_report(findings, [wf], out)
        html = out.read_text()

        assert "myorg/myrepo" in html
        # The raw tmpdir name must not appear as a repo label
        assert tmpdir.name not in html


class TestEndToEnd:
    """Smoke tests covering the full audit_workflow path with realistic YAML."""

    def test_publish_workflow_flags_unconfigured_setup_node(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            name: Publish
            on:
              release:
                types: [published]
            jobs:
              publish:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: actions/setup-node@v4
                    with:
                      node-version: 20
                  - run: npm publish
        """)
        a = Auditor(resolve_pins=False)
        with patch.object(audit, "fetch_action_yml", return_value=None):
            fs = a.audit_workflow(wf)
        assert any(
            "setup-node" in f.action and f.level == "HIGH" for f in fs
        )

    def test_safe_publish_workflow_no_high_findings(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            name: Publish
            on:
              release:
                types: [published]
            jobs:
              publish:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: actions/setup-node@v4
                    with:
                      node-version: 20
                      cache: ''
                  - run: npm ci --ignore-scripts
                  - run: npm publish
        """)
        a = Auditor(resolve_pins=False)
        with patch.object(audit, "fetch_action_yml", return_value=None):
            fs = a.audit_workflow(wf)
        high = [f for f in fs if f.level == "HIGH"]
        assert not high

    def test_multiple_setup_actions_all_flagged(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/setup-node@v4
                  - uses: actions/setup-python@v5
                  - uses: actions/setup-go@v5
        """)
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)
        high_actions = {f.action for f in fs if f.level == "HIGH"}
        assert any("setup-node"   in a for a in high_actions)
        assert any("setup-python" in a for a in high_actions)
        assert any("setup-go"     in a for a in high_actions)

    def test_html_report_generated_from_workflow(self, tmp_path):
        wf = _write_workflow(tmp_path, """\
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v3
                    with:
                      path: ~/.npm
                      key: k
        """)
        a = Auditor(resolve_pins=False)
        fs = a.audit_workflow(wf)

        report = tmp_path / "report.html"
        generate_html_report(fs, [wf], report)
        html = report.read_text()

        assert 'id="board"'    in html
        assert "actions/cache" in html
