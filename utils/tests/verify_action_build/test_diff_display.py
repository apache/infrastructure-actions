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

from rich.text import Text

from verify_action_build.diff_display import format_diff_text, show_colored_diff


class TestFormatDiffText:
    def test_colors_additions(self):
        lines = ["+added line\n"]
        result = format_diff_text(lines)
        assert isinstance(result, Text)
        assert "+added line" in result.plain

    def test_colors_removals(self):
        lines = ["-removed line\n"]
        result = format_diff_text(lines)
        assert "-removed line" in result.plain

    def test_colors_headers(self):
        lines = ["--- a/file.js\n", "+++ b/file.js\n"]
        result = format_diff_text(lines)
        assert "--- a/file.js" in result.plain
        assert "+++ b/file.js" in result.plain

    def test_colors_hunk_markers(self):
        lines = ["@@ -1,3 +1,4 @@\n"]
        result = format_diff_text(lines)
        assert "@@ -1,3 +1,4 @@" in result.plain

    def test_context_lines(self):
        lines = [" unchanged line\n"]
        result = format_diff_text(lines)
        assert "unchanged line" in result.plain

    def test_empty_input(self):
        result = format_diff_text([])
        assert isinstance(result, Text)
        assert result.plain == ""


class TestShowColoredDiff:
    def test_identical_returns_continue(self):
        result = show_colored_diff(Path("test.js"), "same", "same")
        assert result == "continue"

    def test_different_content_returns_continue_in_ci(self):
        result = show_colored_diff(
            Path("test.js"), "line1\n", "line2\n", ci_mode=True
        )
        assert result == "continue"

    def test_small_diff_not_paged(self):
        result = show_colored_diff(
            Path("test.js"),
            "original content\n",
            "modified content\n",
            ci_mode=True,
        )
        assert result == "continue"

    def test_new_file(self):
        result = show_colored_diff(
            Path("new.js"), "", "new content\n", ci_mode=True
        )
        assert result == "continue"

    def test_deleted_file(self):
        result = show_colored_diff(
            Path("old.js"), "old content\n", "", ci_mode=True
        )
        assert result == "continue"
