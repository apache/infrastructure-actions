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
from verify_action_build.pr_extraction import extract_action_refs_from_diff


class TestExtractActionRefsFromDiff:
    def test_workflow_uses_line(self):
        diff = """\
+      - uses: actions/checkout@abc123def456789012345678901234567890abcd
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == ["actions/checkout@abc123def456789012345678901234567890abcd"]

    def test_actions_yml_format(self):
        diff = """\
+actions/checkout:
+  abc123def456789012345678901234567890abcd:
+    tag: v4.2.0
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == ["actions/checkout@abc123def456789012345678901234567890abcd"]

    def test_multiple_refs(self):
        diff = """\
+      - uses: actions/checkout@abc123def456789012345678901234567890abcd
+      - uses: actions/setup-node@def456789012345678901234567890abcd123456
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 2
        assert "actions/checkout@abc123def456789012345678901234567890abcd" in refs
        assert "actions/setup-node@def456789012345678901234567890abcd123456" in refs

    def test_deduplication(self):
        diff = """\
+      - uses: actions/checkout@abc123def456789012345678901234567890abcd
+      - uses: actions/checkout@abc123def456789012345678901234567890abcd
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 1

    def test_ignores_removed_lines(self):
        diff = """\
-      - uses: actions/checkout@abc123def456789012345678901234567890abcd
+      - uses: actions/checkout@def456789012345678901234567890abcd123456
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 1
        assert refs[0] == "actions/checkout@def456789012345678901234567890abcd123456"

    def test_ignores_non_hash_refs(self):
        diff = """\
+      - uses: actions/checkout@v4
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == []

    def test_monorepo_sub_path(self):
        diff = """\
+      - uses: gradle/actions/setup-gradle@abc123def456789012345678901234567890abcd
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == ["gradle/actions/setup-gradle@abc123def456789012345678901234567890abcd"]

    def test_actions_yml_multiple_hashes(self):
        diff = """\
+actions/checkout:
+  abc123def456789012345678901234567890abcd:
+    tag: v4.2.0
+  def456789012345678901234567890abcd123456:
+    tag: v4.1.0
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 2

    def test_actions_yml_key_resets(self):
        diff = """\
+actions/checkout:
+  abc123def456789012345678901234567890abcd:
+    tag: v4
+dorny/test-reporter:
+  def456789012345678901234567890abcd123456:
+    tag: v1
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 2
        assert "actions/checkout@abc123def456789012345678901234567890abcd" in refs
        assert "dorny/test-reporter@def456789012345678901234567890abcd123456" in refs

    def test_empty_diff(self):
        refs = extract_action_refs_from_diff("")
        assert refs == []

    def test_use_typo(self):
        diff = """\
+      - use: actions/checkout@abc123def456789012345678901234567890abcd
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 1

    def test_quoted_hash_in_actions_yml(self):
        diff = """\
+actions/checkout:
+  'abc123def456789012345678901234567890abcd':
+    tag: v4
"""
        refs = extract_action_refs_from_diff(diff)
        assert len(refs) == 1
        assert "abc123def456789012345678901234567890abcd" in refs[0]
