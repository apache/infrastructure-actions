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
from verify_action_build.diff_js import _collect_compiled_js, beautify_js


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
