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

from verify_action_build.approved_actions import find_approved_versions


SAMPLE_ACTIONS_YML = """\
actions/checkout:
  abc123def456789012345678901234567890abcd:
    tag: v4.2.0
    expires_at: 2025-12-31
    keep: true
  def456789012345678901234567890abcd123456:
    tag: v4.1.0
    expires_at: 2025-06-30
dorny/test-reporter:
  1111111111111111111111111111111111111111:
    tag: v1.0.0
"""


class TestFindApprovedVersions:
    def test_finds_all_versions(self, tmp_path):
        actions_file = tmp_path / "actions.yml"
        actions_file.write_text(SAMPLE_ACTIONS_YML)

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", actions_file):
            result = find_approved_versions("actions", "checkout")

        assert len(result) == 2
        assert result[0]["hash"] == "abc123def456789012345678901234567890abcd"
        assert result[0]["tag"] == "v4.2.0"
        assert result[0]["expires_at"] == "2025-12-31"
        assert result[0]["keep"] == "true"
        assert result[1]["hash"] == "def456789012345678901234567890abcd123456"
        assert result[1]["tag"] == "v4.1.0"

    def test_finds_different_action(self, tmp_path):
        actions_file = tmp_path / "actions.yml"
        actions_file.write_text(SAMPLE_ACTIONS_YML)

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", actions_file):
            result = find_approved_versions("dorny", "test-reporter")

        assert len(result) == 1
        assert result[0]["hash"] == "1111111111111111111111111111111111111111"
        assert result[0]["tag"] == "v1.0.0"

    def test_returns_empty_for_unknown_action(self, tmp_path):
        actions_file = tmp_path / "actions.yml"
        actions_file.write_text(SAMPLE_ACTIONS_YML)

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", actions_file):
            result = find_approved_versions("unknown", "action")

        assert result == []

    def test_returns_empty_when_file_missing(self, tmp_path):
        missing_file = tmp_path / "nonexistent.yml"

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", missing_file):
            result = find_approved_versions("actions", "checkout")

        assert result == []

    def test_handles_quoted_hashes(self, tmp_path):
        yml = """\
actions/checkout:
  'abc123def456789012345678901234567890abcd':
    tag: v4
"""
        actions_file = tmp_path / "actions.yml"
        actions_file.write_text(yml)

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", actions_file):
            result = find_approved_versions("actions", "checkout")

        assert len(result) == 1
        assert result[0]["hash"] == "abc123def456789012345678901234567890abcd"

    def test_ignores_comments(self, tmp_path):
        yml = """\
# This is a comment
actions/checkout:
  abc123def456789012345678901234567890abcd:
    tag: v4
"""
        actions_file = tmp_path / "actions.yml"
        actions_file.write_text(yml)

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", actions_file):
            result = find_approved_versions("actions", "checkout")

        assert len(result) == 1

    def test_handles_missing_optional_fields(self, tmp_path):
        yml = """\
actions/checkout:
  abc123def456789012345678901234567890abcd:
    tag: v4
"""
        actions_file = tmp_path / "actions.yml"
        actions_file.write_text(yml)

        with mock.patch("verify_action_build.approved_actions.ACTIONS_YML", actions_file):
            result = find_approved_versions("actions", "checkout")

        assert len(result) == 1
        assert "expires_at" not in result[0]
        assert "keep" not in result[0]
