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
from verify_action_build.diff_js import beautify_js


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
