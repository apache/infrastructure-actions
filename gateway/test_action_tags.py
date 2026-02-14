import pytest
from action_tags import *

def test_patterns():
    assert re.match(re_github_actions_repo, "foo/bar")
    assert not re.match(re_github_actions_repo, "foo/*")
    assert re.match(re_github_actions_repo, "foo/bar/.github/actions/*")
    assert re.match(re_github_actions_repo, "foo/bar/.github/actions/some.yml")
    assert re.match(re_docker_image, "docker://foo/bar")

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_sha_without_tag():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_sha_non_existent():
    # noinspection PyTypeChecker
    result = verify_actions({
        "sbt/setup-sbt": {
          "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef": {
          },
        },
    })
    assert result.failures == [
        "GitHub action sbt/setup-sbt references non existing commit SHA 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef': HTTP/404: Not Found, API URL: https://api.github.com/repos/sbt/setup-sbt/git/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    ]
    assert result.warnings == []

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_tag_sha_vs_commit_sha():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_tag_sha_eq_commit_sha():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_non_existing_tag():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_non_existing_tag_sha():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_repo_multiple_actions_repo_works():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_wildcard_warnings_1():
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

@pytest.mark.skipif(os.environ.get('GH_TOKEN') is None, reason="GH_TOKEN environment variable should be set for this test as it issues GitHub API requests.")
def test_wildcard_warnings_2():
    """
    Similar to test_wildcard_warnings_1, but with the wildcard SHA at the end.
    """
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
