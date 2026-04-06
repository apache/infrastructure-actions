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
from verify_action_build.verification import (
    SECURITY_CHECKLIST_URL,
    show_verification_summary,
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
