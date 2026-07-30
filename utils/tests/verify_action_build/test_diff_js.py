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

from verify_action_build.diff_js import _collect_compiled_js, beautify_js, diff_js_files


class TestBeautifyJs:
    def test_formats_minified(self):
        result = beautify_js("function foo(){return 1}")
        assert "function foo()" in result
        assert "return 1" in result

    def test_trailing_newline(self):
        result = beautify_js("var x = 1;")
        assert result.endswith("\n")

    def test_strips_trailing_spaces(self):
        result = beautify_js("var x = 1;   ")
        for line in result.splitlines():
            assert line == line.rstrip()

    def test_empty_input(self):
        result = beautify_js("")
        assert result == "\n"

    def test_preserves_string_content(self):
        result = beautify_js('var s = "hello world";')
        assert "hello world" in result

    def test_consistent_output(self):
        code = 'function add(a,b){return a+b}function sub(a,b){return a-b}'
        result1 = beautify_js(code)
        result2 = beautify_js(code)
        assert result1 == result2


class TestCollectCompiledJs:
    def test_picks_up_js_cjs_mjs(self, tmp_path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "index.js").write_text("// js\n")
        (tmp_path / "dist" / "index.cjs").write_text("// cjs\n")
        (tmp_path / "dist" / "index.mjs").write_text("// mjs\n")
        (tmp_path / "dist" / "index.cjs.map").write_text("{}\n")
        (tmp_path / "dist" / "readme.md").write_text("ignored\n")

        found = _collect_compiled_js(tmp_path)

        from pathlib import Path
        assert found == {
            Path("dist/index.js"),
            Path("dist/index.cjs"),
            Path("dist/index.mjs"),
        }

    def test_empty_dir_returns_empty(self, tmp_path):
        assert _collect_compiled_js(tmp_path) == set()

    def test_nested_dirs(self, tmp_path):
        (tmp_path / "dist" / "sub" / "main").mkdir(parents=True)
        (tmp_path / "dist" / "sub" / "main" / "index.cjs").write_text("// cjs\n")

        from pathlib import Path
        assert _collect_compiled_js(tmp_path) == {Path("dist/sub/main/index.cjs")}


class TestDiffJsKeptFiles:
    """Files passed in via kept_files are non-minified bundles that the
    Dockerfile intentionally did not delete. The original-vs-rebuilt
    comparison for them is replaced by a pointer to the approved-version
    diff section, so it must not fail the JS check even when content
    differs and must not consult the on-disk files."""

    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        original = tmp_path / "original-dist"
        rebuilt = tmp_path / "rebuilt-dist"
        original.mkdir()
        rebuilt.mkdir()
        # Original committed to the repo.
        (original / "post.js").write_text("// original committed bundle\n" + "x\n" * 50)
        # Rebuild produced different content (toolchain noise) — should
        # be ignored when the file is in kept_files.
        (rebuilt / "post.js").write_text("// rebuilt with newer toolchain\n" + "y\n" * 50)
        return original, rebuilt

    def test_kept_file_with_differing_rebuild_does_not_fail(self, tmp_path):
        original, rebuilt = self._setup(tmp_path)

        result = diff_js_files(
            original, rebuilt, "Org", "Repo", "deadbeef" * 5,
            out_dir_name="dist",
            kept_files={Path("post.js")},
        )

        assert result is True

    def test_default_kept_files_none_treats_diff_as_normal(self, tmp_path):
        original, rebuilt = self._setup(tmp_path)

        # Without kept_files, the same setup hits the non-minified branch
        # which prints a soft warning but still returns True (it isn't a
        # hard failure either way).  This guards against the kept-files
        # plumbing accidentally swallowing differences for *every* file.
        result = diff_js_files(
            original, rebuilt, "Org", "Repo", "deadbeef" * 5,
            out_dir_name="dist",
        )

        assert result is True


class TestDiffJsOnlyInRebuilt:
    """A file present only in the rebuild is not published by the action, so
    it never runs on a consumer's runner and cannot be a supply-chain
    vector.  It is reported but must not fail the JS check.

    The real-world shape is JetBrains/qodana-action: ``main:
    scan/dist/index.js`` makes OUT_DIR resolve to the whole ``scan/``
    sub-project (the Dockerfile takes the first path component), so the
    rebuild's stage-one ``tsc`` output ``scan/lib/*.js`` — gitignored
    upstream via ``**/lib``, never committed, and consumed only by the
    ``esbuild`` bundling step that writes ``scan/dist/index.js`` — shows up
    inside the compared tree.  Before this was informational it hard-failed
    the check on every qodana-action bump (apache/infrastructure-actions
    #960, #1123).
    """

    def _setup_qodana_shape(self, tmp_path: Path) -> tuple[Path, Path]:
        original = tmp_path / "original-dist"
        rebuilt = tmp_path / "rebuilt-dist"
        for base in (original, rebuilt):
            (base / "dist").mkdir(parents=True)
            # The published, non-minified esbuild bundle plus a config file:
            # identical on both sides here so the only difference under test
            # is the extra intermediate output.
            (base / "dist" / "index.js").write_text(
                "// esbuild bundle\n" + "var x = 1;\n" * 50
            )
            (base / "jest.config.js").write_text("module.exports = {};\n" * 12)
        # Stage-one `tsc --build` output (outDir: ./lib), gitignored upstream
        # and absent from the published tree.
        (rebuilt / "lib").mkdir()
        for name in ("annotations", "main", "output", "utils"):
            (rebuilt / "lib" / f"{name}.js").write_text(
                f'"use strict";\nexports.{name} = 1;\n' * 20
            )
        return original, rebuilt

    def test_intermediate_build_output_does_not_fail(self, tmp_path):
        original, rebuilt = self._setup_qodana_shape(tmp_path)

        result = diff_js_files(
            original, rebuilt, "JetBrains", "qodana-action", "b588768b6e7e" * 3,
            out_dir_name="scan",
            kept_files={Path("dist/index.js"), Path("jest.config.js")},
        )

        assert result is True

    def test_only_in_original_still_fails(self, tmp_path):
        """The inverse case is a real signal: the action publishes JS the
        rebuild does not produce, so we cannot account for what ships."""
        original, rebuilt = self._setup_qodana_shape(tmp_path)
        # Kept short so the rendered diff fits one page — show_colored_diff
        # pages interactively above ~20 diff lines and would read stdin.
        (original / "dist" / "extra.js").write_text("// unaccounted for\n")

        result = diff_js_files(
            original, rebuilt, "JetBrains", "qodana-action", "b588768b6e7e" * 3,
            out_dir_name="scan",
            kept_files={Path("dist/index.js"), Path("jest.config.js")},
        )

        assert result is False

    def test_published_tree_with_no_compiled_js_fails(self, tmp_path):
        """Guard against the informational handling letting a wholly
        unverifiable action pass: if the rebuild emits JS but the action
        publishes none, there is nothing to reconcile against."""
        original = tmp_path / "original-dist"
        rebuilt = tmp_path / "rebuilt-dist"
        original.mkdir()
        (rebuilt / "lib").mkdir(parents=True)
        (rebuilt / "lib" / "main.js").write_text("// rebuilt only\n" * 20)

        result = diff_js_files(
            original, rebuilt, "Org", "Repo", "deadbeef" * 5,
            out_dir_name="scan",
        )

        assert result is False

    def test_minified_content_mismatch_still_fails(self, tmp_path):
        """The downgrade must not weaken the check that matters: a published
        minified bundle whose content differs from the rebuild is still a
        hard failure."""
        original = tmp_path / "original-dist"
        rebuilt = tmp_path / "rebuilt-dist"
        (original / "dist").mkdir(parents=True)
        (rebuilt / "dist").mkdir(parents=True)
        # One long line, so is_minified() holds (avg line length > 500) while
        # the beautified diff still fits a single page.
        (original / "dist" / "index.js").write_text(f'var a = "{"a" * 600}";\n')
        (rebuilt / "dist" / "index.js").write_text(f'var a = "{"b" * 600}";\n')
        # Extra intermediate output alongside the real mismatch.
        (rebuilt / "lib").mkdir()
        (rebuilt / "lib" / "main.js").write_text("// intermediate\n" * 20)

        result = diff_js_files(
            original, rebuilt, "Org", "Repo", "deadbeef" * 5,
            out_dir_name="scan",
        )

        assert result is False
