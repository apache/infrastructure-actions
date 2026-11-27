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
from verify_action_build.pr_extraction import (
    extract_action_refs_from_diff,
    extract_pattern_entries_from_diff,
)


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

    def test_hash_added_under_existing_key_via_context_line(self):
        # The most common shape of a real PR diff: the org/repo key is
        # already in main and shows up in the hunk as a context line
        # (leading single space, no ``+``); only the new hash entry below
        # it is an actual addition.  Pre-fix this dropped the hash because
        # the extractor only considered ``+`` lines for the key.
        diff = """\
 actions/checkout:
   def456789012345678901234567890abcd123456:
     tag: v4.1.0
+  abc123def456789012345678901234567890abcd:
+    tag: v4.2.0
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == [
            "actions/checkout@abc123def456789012345678901234567890abcd"
        ]

    def test_hash_added_under_existing_key_via_hunk_header(self):
        # When git's diff context window doesn't reach back to the key, the
        # key only appears in the hunk header's function-context tail
        # (``@@ ... @@ docker/build-push-action:``).  We must seed the key
        # from there or the extractor sees only a bare ``+  <hash>:`` and
        # has no way to attribute it.
        diff = """\
@@ -10,3 +10,6 @@ docker/build-push-action:
     expires_at: 2026-07-07
   bcafcacb16a39f128d818304e6c9c0c18556b85f:
     tag: v7.1.0
+  ca052bb54ab0790a636c9b5f226502c73d547a25:
+    tag: v5.4.0
+    expires_at: 2026-07-28
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == [
            "docker/build-push-action@ca052bb54ab0790a636c9b5f226502c73d547a25"
        ]

    def test_pr_803_shape_three_actions(self):
        # Reproduces the apache/infrastructure-actions#803 shape: one fully
        # new key plus two hash additions under pre-existing keys.  All
        # three refs should be extracted.  Pre-fix only the wholly-new key
        # was found.
        diff = """\
diff --git a/actions.yml b/actions.yml
--- a/actions.yml
+++ b/actions.yml
@@ -286,6 +286,9 @@ dependabot/fetch-metadata:
 dlang-community/setup-dlang:
   '*':
     keep: true
+docker/bake-action:
+  aefd381cbaa93c62a1e8b02194ae420cc36269d2:
+    tag: v4.7.0
 docker/build-push-action:
   10e90e3645eae34f1e60eeb005ba3a3d33f178e8:
     tag: v6.19.2
@@ -295,6 +298,9 @@ docker/build-push-action:
     expires_at: 2026-07-07
   bcafcacb16a39f128d818304e6c9c0c18556b85f:
     tag: v7.1.0
+  ca052bb54ab0790a636c9b5f226502c73d547a25:
+    tag: v5.4.0
+    expires_at: 2026-07-28
 docker/login-action:
   c94ce9fb468520275223c153574b00df6fe4bcc9:
     tag: v3.7.0
@@ -322,6 +328,9 @@ docker/setup-qemu-action:
     expires_at: 2026-06-14
   ce360397dd3f832beb865e1373c09c0e9f86d70a:
     tag: v4.0.0
+  c7c53464625b32c7a7e944ae62b3e17d2b600130:
+    tag: v3.7.0
+    expires_at: 2026-07-28
 docker://jekyll/jekyll:
"""
        refs = extract_action_refs_from_diff(diff)
        assert refs == [
            "docker/bake-action@aefd381cbaa93c62a1e8b02194ae420cc36269d2",
            "docker/build-push-action@ca052bb54ab0790a636c9b5f226502c73d547a25",
            "docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130",
        ]

    def test_removed_key_does_not_leak_context(self):
        # A removed key should never seed actions_yml_key — otherwise a
        # subsequent ``+  <hash>:`` (perhaps from an unrelated edit further
        # down the file) would be wrongly attributed to the removed key.
        diff = """\
-actions/checkout:
-  abc123def456789012345678901234567890abcd:
-    tag: v4.0.0
+  def456789012345678901234567890abcd123456:
+    tag: v9.9.9
"""
        # The bare hash is unattributable, so the extractor should yield
        # nothing — never the removed-key + new-hash combination.
        refs = extract_action_refs_from_diff(diff)
        assert refs == []

    def test_hunk_boundary_resets_key(self):
        # If a hunk ends inside one action's block and the next hunk has
        # no header context, the key from the previous hunk must not leak
        # — it would silently reattribute additions to the wrong action.
        diff = """\
@@ -1,3 +1,4 @@ actions/checkout:
   abc123def456789012345678901234567890abcd:
     tag: v4
@@ -50,2 +52,3 @@
+  def456789012345678901234567890abcd123456:
+    tag: v1.0.0
"""
        # Second hunk has no enclosing key context, so the bare ``+  <hash>:``
        # line is unattributable and dropped.  No ref returned.
        refs = extract_action_refs_from_diff(diff)
        assert refs == []


# apache/infrastructure-actions#1585, verbatim: a new action added straight to
# the generated approved_patterns.yml, pinned to a moving tag.  `verify`
# reported "No added action references ... nothing to verify" and passed.
PR_1585_DIFF = """\
diff --git a/approved_patterns.yml b/approved_patterns.yml
index fe5631903..e473d8a57 100644
--- a/approved_patterns.yml
+++ b/approved_patterns.yml
@@ -155,6 +155,7 @@
 - dawidd6/action-send-mail@47af4a10b540781705f283a2cf1a9650713ac795
 - dawidd6/action-send-mail@27bf7a883db6de205849f760badd38a1a3ce0dd0
 - dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98
+- DigitalPebble/aspen@v1
 - dlang-community/setup-dlang@*
 - docker/bake-action@d3418bd7d0e9324001bca92fa8ba175ea7e6dc9b
 - docker/bake-action@018cb6412ab401ebaa809aa5f85966b74628600f
"""

_EMPTY = {"sha": [], "wildcard": [], "unpinned": [], "unparsed": []}


class TestExtractPatternEntriesFromDiff:
    def test_pr_1585_tag_pin_is_unpinned(self):
        found = extract_pattern_entries_from_diff(PR_1585_DIFF)
        assert found == {**_EMPTY, "unpinned": ["DigitalPebble/aspen@v1"]}
        # Context lines (existing entries) are never reported.
        assert extract_action_refs_from_diff(PR_1585_DIFF) == []

    def test_sha_entry_is_verified_like_any_ref(self):
        sha = "933c530e9d91f5e6a43c893678d08abc248f399d"
        diff = PR_1585_DIFF.replace("+- DigitalPebble/aspen@v1", f"+- DigitalPebble/aspen@{sha}")
        assert extract_pattern_entries_from_diff(diff)["sha"] == [f"DigitalPebble/aspen@{sha}"]
        assert extract_action_refs_from_diff(diff) == [f"DigitalPebble/aspen@{sha}"]

    def test_wildcard_and_branch_are_told_apart(self):
        diff = PR_1585_DIFF.replace(
            "+- DigitalPebble/aspen@v1", "+- some-org/tool@*\n+- other-org/tool/sub@main"
        )
        found = extract_pattern_entries_from_diff(diff)
        assert found["wildcard"] == ["some-org/tool@*"]
        assert found["unpinned"] == ["other-org/tool/sub@main"]

    def test_same_shape_in_another_file_is_ignored(self):
        diff = PR_1585_DIFF.replace("approved_patterns.yml", "docs/example.yml")
        assert extract_pattern_entries_from_diff(diff) == _EMPTY

    def test_removed_entry_is_ignored(self):
        diff = PR_1585_DIFF.replace("+- DigitalPebble/aspen@v1", "-- DigitalPebble/aspen@v1")
        assert extract_pattern_entries_from_diff(diff) == _EMPTY

    def test_trailing_comment_indent_and_quotes_still_classify(self):
        """Shapes valid YAML allows must not slip a tag pin past the check."""
        diff = PR_1585_DIFF.replace(
            "+- DigitalPebble/aspen@v1",
            "+- DigitalPebble/aspen@v1  # carbon estimates\n"
            "+  - 'other-org/tool@main'\n"
            '+- "third-org/tool@*"',
        )
        found = extract_pattern_entries_from_diff(diff)
        assert found["unpinned"] == ["DigitalPebble/aspen@v1", "other-org/tool@main"]
        assert found["wildcard"] == ["third-org/tool@*"]
        assert found["unparsed"] == []

    def test_unrecognised_added_line_fails_closed(self):
        diff = PR_1585_DIFF.replace(
            "+- DigitalPebble/aspen@v1",
            "+[DigitalPebble/aspen@v1]\n+- 'mismatched/quote@v1\"\n+# a comment\n+",
        )
        found = extract_pattern_entries_from_diff(diff)
        assert found["unparsed"] == ["[DigitalPebble/aspen@v1]", "- 'mismatched/quote@v1\""]
        assert found["unpinned"] == []

    def test_hash_touching_the_ref_is_not_a_comment(self):
        """``@<sha>#v1`` is one YAML scalar, not a SHA plus a comment."""
        sha = "933c530e9d91f5e6a43c893678d08abc248f399d"
        diff = PR_1585_DIFF.replace("+- DigitalPebble/aspen@v1", f"+- DigitalPebble/aspen@{sha}#v1")
        found = extract_pattern_entries_from_diff(diff)
        assert found["sha"] == []
        assert found["unparsed"] == [f"- DigitalPebble/aspen@{sha}#v1"]
