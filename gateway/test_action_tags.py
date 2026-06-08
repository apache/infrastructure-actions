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

import os
import pytest
import re
import ruyaml
from datetime import date
from unittest import mock

from action_tags import (
    ApiResponse,
    re_docker_image,
    re_git_sha,
    re_github_actions_repo,
    verify_actions,
    # Need to explicitly import these:
    _gh_compare,
    _gh_get_branch,
    _gh_get_commit_object,
    _gh_get_tag,
    _gh_matching_tags,
)
from gateway import ActionsYAML

DTOLNAY_RUST_TOOLCHAIN_SHA = "29eef336d9b2848a0b548edc03f92a220660cdb8"
ACTIONS_CHECKOUT_V4_2_2_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"
ACTIONS_CHECKOUT_V2_BETA_TAG_SHA = "95784fc5bbede4a44d9abcfbde7a64f16e6dbedd"
LIVE_GITHUB_API_SKIP_REASON = "GH_TOKEN environment variable should be set for this test as it issues GitHub API requests."


#
# Live-API tests only run when the GH_TOKEN environment variable is set
#

def _response_json(response: ApiResponse):
    return ruyaml.YAML().load(response.body)

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason=LIVE_GITHUB_API_SKIP_REASON)
def test_live_gh_get_commit_object_actions_checkout():
    response = _gh_get_commit_object("actions/checkout", ACTIONS_CHECKOUT_V4_2_2_SHA)

    assert response.status == 200
    response_json = _response_json(response)
    assert response_json["sha"] == ACTIONS_CHECKOUT_V4_2_2_SHA
    assert re.match(re_git_sha, response_json["tree"]["sha"])

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason=LIVE_GITHUB_API_SKIP_REASON)
def test_live_gh_matching_tags_actions_checkout():
    response = _gh_matching_tags("actions/checkout", "v4.2.2")

    assert response.status == 200
    response_json = _response_json(response)
    assert len(response_json) == 1
    assert response_json[0]["ref"] == "refs/tags/v4.2.2"
    assert response_json[0]["object"]["type"] == "commit"
    assert response_json[0]["object"]["sha"] == ACTIONS_CHECKOUT_V4_2_2_SHA

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason=LIVE_GITHUB_API_SKIP_REASON)
def test_live_gh_get_tag_actions_checkout():
    response = _gh_get_tag("actions/checkout", ACTIONS_CHECKOUT_V2_BETA_TAG_SHA)

    assert response.status == 200
    response_json = _response_json(response)
    assert response_json["sha"] == ACTIONS_CHECKOUT_V2_BETA_TAG_SHA
    assert response_json["object"]["type"] == "commit"
    assert re.match(re_git_sha, response_json["object"]["sha"])

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason=LIVE_GITHUB_API_SKIP_REASON)
def test_live_gh_get_branch_actions_checkout():
    response = _gh_get_branch("actions/checkout", "main")

    assert response.status == 200
    response_json = _response_json(response)
    assert response_json["name"] == "main"
    assert re.match(re_git_sha, response_json["commit"]["sha"])

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason=LIVE_GITHUB_API_SKIP_REASON)
def test_live_gh_compare_actions_checkout():
    response = _gh_compare("actions/checkout", "main", ACTIONS_CHECKOUT_V4_2_2_SHA)

    assert response.status == 200
    response_json = _response_json(response)
    assert response_json["base_commit"]["sha"] == ACTIONS_CHECKOUT_V4_2_2_SHA
    assert re.match(re_git_sha, response_json["merge_base_commit"]["sha"])

#
# Unit tests, no live-API calls
#

def test_patterns():
    assert re.match(re_github_actions_repo, "foo/bar")
    assert not re.match(re_github_actions_repo, "foo/*")
    assert re.match(re_github_actions_repo, "foo/bar/.github/actions/*")
    assert re.match(re_github_actions_repo, "foo/bar/.github/actions/some.yml")
    assert re.match(re_docker_image, "docker://foo/bar")

def test_sha_without_tag():
    with mock.patch("action_tags._gh_get_commit_object", return_value=_api_response(200)):
        # noinspection PyTypeChecker
        result = verify_actions({
            "sbt/setup-sbt": {
              "3e125ece5c3e5248e18da9ed8d2cce3d335ec8dd": {
              },
            },
        })

    assert result.failures == []
    assert result.warnings == [
        "GitHub action sbt/setup-sbt references existing commit SHA '3e125ece5c3e5248e18da9ed8d2cce3d335ec8dd' but does not specify the tag name for it."
    ]

def test_sha_non_existent():
    with mock.patch("action_tags._gh_get_commit_object", return_value=_api_response(404, reason="Not Found")):
        # noinspection PyTypeChecker
        result = verify_actions({
            "sbt/setup-sbt": {
              "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef": {
              },
            },
        })

    assert result.failures == [
        "GitHub action sbt/setup-sbt references non existing commit SHA 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef': HTTP/404: Not Found, API URL: https://api.github.test"
    ]
    assert result.warnings == []

def test_invalid_sha_records_failure_without_crashing():
    # noinspection PyTypeChecker
    result = verify_actions({
        "dtolnay/rust-toolchain": {
            "stable": {
            },
        },
    })

    assert result.failures == [
        "GitHub action dtolnay/rust-toolchain references an invalid Git SHA 'stable'"
    ]
    assert result.warnings == []

def test_tag_sha_vs_commit_sha():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_tag_ref_response("v3.0.0", "e4feb4d8a7cd938b64370099b1893e05c58c3a84")),
        mock.patch("action_tags._gh_get_tag", return_value=_tag_object_response("13f58eec611f8e5db52ec16247f58c508398f3e6")),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "1Password/load-secrets-action": {
              "e4feb4d8a7cd938b64370099b1893e05c58c3a84": {
                  "tag": "v3.0.0"
              },
            },
        })

    assert "      .. GH yields tag SHA 'e4feb4d8a7cd938b64370099b1893e05c58c3a84' for 'refs/tags/v3.0.0'" in result.logs
    assert "        .. GH returns commit SHA '13f58eec611f8e5db52ec16247f58c508398f3e6' for previous tag SHA" in result.logs
    assert result.failures == []
    assert result.warnings == []

def test_tag_sha_eq_commit_sha():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_tag_ref_response("v3.0.0", "e4feb4d8a7cd938b64370099b1893e05c58c3a84")),
        mock.patch("action_tags._gh_get_tag", return_value=_tag_object_response("13f58eec611f8e5db52ec16247f58c508398f3e6")),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "1Password/load-secrets-action": {
              "13f58eec611f8e5db52ec16247f58c508398f3e6": {
                  "tag": "v3.0.0"
              },
            },
        })

    assert "      .. GH yields tag SHA 'e4feb4d8a7cd938b64370099b1893e05c58c3a84' for 'refs/tags/v3.0.0'" in result.logs
    assert "        .. GH returns commit SHA '13f58eec611f8e5db52ec16247f58c508398f3e6' for previous tag SHA" in result.logs
    assert result.failures == []
    assert result.warnings == []

def test_non_existing_tag():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(404, "missing", "Not Found")),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "1Password/load-secrets-action": {
              "13f58eec611f8e5db52ec16247f58c508398f3e6": {
                  "tag": "v_ne_3.0.0"
              },
            },
        })

    assert result.failures == [
        "GitHub action 1Password/load-secrets-action references Git tag 'v_ne_3.0.0' via SHAs '{'13f58eec611f8e5db52ec16247f58c508398f3e6'}' but no SHAs for tag could be found - does the Git tag exist?"
    ]
    assert result.warnings == []

def test_non_existing_tag_sha():
    with mock.patch("action_tags._gh_matching_tags", return_value=_commit_ref_response("v7.1.2", "85856786d1ce8acfbcc2f13a5f3fbd6b938f9f41")):
        # noinspection PyTypeChecker
        result = verify_actions({
            "astral-sh/setup-uv": {
              "b75a909f75acd358c2196fb9a5f1299a9a8868a4": {
                  "tag": "v7.1.2"
              },
            },
        })

    assert result.failures == [
        "GitHub action astral-sh/setup-uv references Git tag 'v7.1.2' via SHAs '{'b75a909f75acd358c2196fb9a5f1299a9a8868a4'}' but none of those matches the valid SHAs '{'85856786d1ce8acfbcc2f13a5f3fbd6b938f9f41'}'"
    ]
    assert result.warnings == []

def test_branch_contains_sha():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(200, "{}")),
        mock.patch(
            "action_tags._gh_compare",
            return_value=_api_response(200, f'{{"merge_base_commit": {{"sha": "{DTOLNAY_RUST_TOOLCHAIN_SHA}"}}}}'),
        ),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "dtolnay/rust-toolchain": {
                DTOLNAY_RUST_TOOLCHAIN_SHA: {
                    "tag": "stable",
                },
            },
        })

    assert result.failures == []
    assert result.warnings == [
        f"GitHub action dtolnay/rust-toolchain references Git tag 'stable' via SHAs '{{'{DTOLNAY_RUST_TOOLCHAIN_SHA}'}}' but that references a Git branch"
    ]

def test_branch_does_not_contain_sha():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(200, "{}")),
        mock.patch(
            "action_tags._gh_compare",
            return_value=_api_response(200, '{"merge_base_commit": {"sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"}}'),
        ),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "dtolnay/rust-toolchain": {
                DTOLNAY_RUST_TOOLCHAIN_SHA: {
                    "tag": "stable",
                },
            },
        })

    assert result.failures == [
        f"GitHub action dtolnay/rust-toolchain references Git branch 'stable' via SHAs '{{'{DTOLNAY_RUST_TOOLCHAIN_SHA}'}}' but none of those SHAs are ancestors of that branch"
    ]
    assert result.warnings == []

def test_branch_compare_api_failure():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(200, "{}")),
        mock.patch(
            "action_tags._gh_compare",
            return_value=_api_response(500, "compare failed", "Internal Server Error"),
        ),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "dtolnay/rust-toolchain": {
                DTOLNAY_RUST_TOOLCHAIN_SHA: {
                    "tag": "stable",
                },
            },
        })

    assert result.failures == [
        "Failed to find Git SHA "
        f"'{DTOLNAY_RUST_TOOLCHAIN_SHA}' on Git branch 'stable' in GitHub repo "
        "'https://github.com/dtolnay/rust-toolchain': HTTP/500: Internal Server Error, "
        "API URL: https://api.github.test\ncompare failed"
    ]
    assert result.warnings == []

def test_branch_compare_api_failure_can_be_ignored():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(200, "{}")),
        mock.patch(
            "action_tags._gh_compare",
            return_value=_api_response(500, "compare failed", "Internal Server Error"),
        ),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "dtolnay/rust-toolchain": {
                DTOLNAY_RUST_TOOLCHAIN_SHA: {
                    "tag": "stable",
                    "ignore_gh_api_errors": True,
                },
            },
        })

    assert result.failures == []
    assert result.warnings == [
        "ignore_gh_api_errors is set to true: will ignore GH API errors for action "
        f"dtolnay/rust-toolchain ref '{DTOLNAY_RUST_TOOLCHAIN_SHA}'",
        "Failed to find Git SHA "
        f"'{DTOLNAY_RUST_TOOLCHAIN_SHA}' on Git branch 'stable' in GitHub repo "
        "'https://github.com/dtolnay/rust-toolchain': HTTP/500: Internal Server Error, "
        "API URL: https://api.github.test\ncompare failed",
    ]

def test_branch_lookup_api_failure_can_be_ignored():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(500, "branch failed", "Internal Server Error")),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "dtolnay/rust-toolchain": {
                DTOLNAY_RUST_TOOLCHAIN_SHA: {
                    "tag": "stable",
                    "ignore_gh_api_errors": True,
                },
            },
        })

    assert result.failures == []
    assert result.warnings == [
        "ignore_gh_api_errors is set to true: will ignore GH API errors for action "
        f"dtolnay/rust-toolchain ref '{DTOLNAY_RUST_TOOLCHAIN_SHA}'",
        "Failed to check Git branch 'stable' against GitHub repo "
        "'https://github.com/dtolnay/rust-toolchain': HTTP/500: Internal Server Error, "
        "API URL: https://api.github.test\nbranch failed",
    ]

def test_missing_branch_falls_back_to_missing_tag_failure():
    with (
        mock.patch("action_tags._gh_matching_tags", return_value=_api_response(200, "[]")),
        mock.patch("action_tags._gh_get_branch", return_value=_api_response(404, "missing", "Not Found")),
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "dtolnay/rust-toolchain": {
                DTOLNAY_RUST_TOOLCHAIN_SHA: {
                    "tag": "stable",
                },
            },
        })

    assert result.failures == [
        f"GitHub action dtolnay/rust-toolchain references Git tag 'stable' via SHAs '{{'{DTOLNAY_RUST_TOOLCHAIN_SHA}'}}' but no SHAs for tag could be found - does the Git tag exist?"
    ]
    assert result.warnings == []

def test_repo_multiple_actions_repo_works():
    with mock.patch(
        "action_tags._gh_matching_tags",
        side_effect=[
            _commit_ref_response("v5.0.0", "4d9f0ba0025fe599b4ebab900eb7f3a1d93ef4c2"),
            _commit_ref_response("v4.4.4", "748248ddd2a24f49513d8f472f81c3a07d4d50e1"),
        ],
    ):
        # noinspection PyTypeChecker
        result = verify_actions({
            "gradle/actions/setup-gradle": {
              "4d9f0ba0025fe599b4ebab900eb7f3a1d93ef4c2": {
                  "tag": "v5.0.0"
              },
            },
            "gradle/actions/wrapper-validation": {
              "748248ddd2a24f49513d8f472f81c3a07d4d50e1": {
                  "tag": "v4.4.4"
              },
            },
        })

    assert result.failures == []
    assert result.warnings == []
    assert "  ✅ GitHub action gradle/actions/setup-gradle definition for tag 'v5.0.0' is good!" in result.logs
    assert "  ✅ GitHub action gradle/actions/wrapper-validation definition for tag 'v4.4.4' is good!" in result.logs

def test_wildcard_warnings_1():
    with mock.patch("action_tags._gh_matching_tags", side_effect=_mock_wildcard_tags):
        # noinspection PyTypeChecker
        _test_wildcard_warnings({
            "sbt/setup-sbt": {
              '*': {
                "expires_at": date(2026, 2,28),
              },
              "17575ea4e18dd928fe5968dbe32294b97923d65b": {
                "expires_at": date(2025, 12,29),
                "tag": "v1.1.13"
              },
              "3e125ece5c3e5248e18da9ed8d2cce3d335ec8dd": {
                "tag": "v1.1.14"
              },
            },
        })

def test_wildcard_warnings_2():
    """
    Similar to test_wildcard_warnings_1, but with the wildcard SHA at the end.
    """
    with mock.patch("action_tags._gh_matching_tags", side_effect=_mock_wildcard_tags):
        # noinspection PyTypeChecker
        _test_wildcard_warnings({
            "sbt/setup-sbt": {
              "17575ea4e18dd928fe5968dbe32294b97923d65b": {
                "expires_at": date(2025, 12,29),
                "tag": "v1.1.13"
              },
              "3e125ece5c3e5248e18da9ed8d2cce3d335ec8dd": {
                "tag": "v1.1.14"
              },
              '*': {
                "expires_at": date(2026, 2,28),
              },
            },
        })

def _mock_wildcard_tags(tag: str) -> ApiResponse:
    tags = {
        "v1.1.13": "17575ea4e18dd928fe5968dbe32294b97923d65b",
        "v1.1.14": "3e125ece5c3e5248e18da9ed8d2cce3d335ec8dd",
    }
    return _commit_ref_response(tag, tags[tag])

def _test_wildcard_warnings(refs: ActionsYAML):
    result = verify_actions(refs, today=date(2025, 12, 21))
    assert not "  .. ref '*' is expired, skipping" in result.logs
    assert result.failures == []
    assert result.warnings == [
        "GitHub action sbt/setup-sbt references a wildcard SHA but also has specific SHAs",
    ]

    # wildcard expired
    result = verify_actions(refs, today=date(2026, 3, 1))
    assert "  .. ref '*' is expired, skipping" in result.logs
    assert result.failures == []
    assert result.warnings == []

def _api_response(status: int, body: str = "", reason: str = "OK") -> ApiResponse:
    return ApiResponse("https://api.github.test", status, reason, {}, body)

def _commit_ref_response(tag: str, sha: str) -> ApiResponse:
    return _api_response(200, f'[{{"ref": "refs/tags/{tag}", "object": {{"type": "commit", "sha": "{sha}"}}}}]')

def _tag_ref_response(tag: str, sha: str) -> ApiResponse:
    return _api_response(200, f'[{{"ref": "refs/tags/{tag}", "object": {{"type": "tag", "sha": "{sha}"}}}}]')

def _tag_object_response(sha: str) -> ApiResponse:
    return _api_response(200, f'{{"object": {{"sha": "{sha}"}}}}')

