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
import pytest

from verify_action_build.action_ref import (
    parse_action_ref,
    extract_composite_uses,
    detect_action_type_from_yml,
)


class TestParseActionRef:
    def test_simple_ref(self):
        org, repo, sub, hash_ = parse_action_ref("dorny/test-reporter@abc123def456789012345678901234567890abcd")
        assert org == "dorny"
        assert repo == "test-reporter"
        assert sub == ""
        assert hash_ == "abc123def456789012345678901234567890abcd"

    def test_monorepo_sub_path(self):
        org, repo, sub, hash_ = parse_action_ref("gradle/actions/setup-gradle@abc123def456789012345678901234567890abcd")
        assert org == "gradle"
        assert repo == "actions"
        assert sub == "setup-gradle"
        assert hash_ == "abc123def456789012345678901234567890abcd"

    def test_deep_sub_path(self):
        org, repo, sub, hash_ = parse_action_ref("org/repo/a/b/c@deadbeef" * 1 + "org/repo/a/b/c@" + "a" * 40)
        # Reset: test clean
        org, repo, sub, hash_ = parse_action_ref("org/repo/a/b/c@" + "a" * 40)
        assert org == "org"
        assert repo == "repo"
        assert sub == "a/b/c"
        assert hash_ == "a" * 40

    def test_missing_at_sign_exits(self):
        with pytest.raises(SystemExit):
            parse_action_ref("dorny/test-reporter")

    def test_missing_org_repo_exits(self):
        with pytest.raises(SystemExit):
            parse_action_ref("singlepart@abc123")


class TestExtractCompositeUses:
    def test_standard_action_ref(self):
        yml = """
steps:
  - uses: actions/checkout@abc123def456789012345678901234567890abcd
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0]["org"] == "actions"
        assert results[0]["repo"] == "checkout"
        assert results[0]["is_hash_pinned"] is True
        assert results[0]["is_local"] is False

    def test_tag_ref_not_hash_pinned(self):
        yml = """
steps:
  - uses: actions/checkout@v4
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0]["is_hash_pinned"] is False
        assert results[0]["ref"] == "v4"

    def test_local_action(self):
        yml = """
steps:
  - uses: ./.github/actions/my-action
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0]["is_local"] is True
        assert results[0]["raw"] == "./.github/actions/my-action"

    def test_docker_reference(self):
        yml = """
steps:
  - uses: docker://alpine:3.18
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0].get("is_docker") is True

    def test_monorepo_sub_action(self):
        yml = """
steps:
  - uses: gradle/actions/setup-gradle@abc123def456789012345678901234567890abcd
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0]["org"] == "gradle"
        assert results[0]["repo"] == "actions"
        assert results[0]["sub_path"] == "setup-gradle"

    def test_comment_stripped(self):
        yml = """
steps:
  - uses: actions/checkout@abc123def456789012345678901234567890abcd  # v4
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0]["ref"] == "abc123def456789012345678901234567890abcd"

    def test_multiple_uses(self):
        yml = """
steps:
  - uses: actions/checkout@abc123def456789012345678901234567890abcd
  - uses: actions/setup-node@def456789012345678901234567890abcd123456
"""
        results = extract_composite_uses(yml)
        assert len(results) == 2

    def test_no_uses(self):
        yml = """
steps:
  - run: echo hello
"""
        results = extract_composite_uses(yml)
        assert len(results) == 0

    def test_quoted_uses(self):
        yml = """
steps:
  - uses: 'actions/checkout@abc123def456789012345678901234567890abcd'
"""
        results = extract_composite_uses(yml)
        assert len(results) == 1
        assert results[0]["org"] == "actions"

    def test_line_numbers(self):
        yml = """line1
line2
  - uses: actions/checkout@abc123def456789012345678901234567890abcd
line4
  - uses: actions/setup-node@def456789012345678901234567890abcd123456
"""
        results = extract_composite_uses(yml)
        assert results[0]["line_num"] == 3
        assert results[1]["line_num"] == 5


class TestDetectActionTypeFromYml:
    def test_node20(self):
        yml = """
name: Test
runs:
  using: node20
  main: dist/index.js
"""
        assert detect_action_type_from_yml(yml) == "node20"

    def test_composite(self):
        yml = """
name: Test
runs:
  using: composite
  steps: []
"""
        assert detect_action_type_from_yml(yml) == "composite"

    def test_docker(self):
        yml = """
name: Test
runs:
  using: docker
  image: Dockerfile
"""
        assert detect_action_type_from_yml(yml) == "docker"

    def test_quoted(self):
        yml = """
runs:
  using: 'node16'
"""
        assert detect_action_type_from_yml(yml) == "node16"

    def test_unknown_when_missing(self):
        yml = """
name: Test
"""
        assert detect_action_type_from_yml(yml) == "unknown"
