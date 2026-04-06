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
import subprocess
from unittest import mock

import pytest

from verify_action_build.console import link, UserQuit, ask_confirm, run


class TestLink:
    def test_ci_mode_returns_plain_text(self):
        with mock.patch("verify_action_build.console._is_ci", True):
            # Re-import link to pick up patched value
            from verify_action_build import console as mod
            original = mod._is_ci
            mod._is_ci = True
            try:
                result = mod.link("https://example.com", "Example")
                assert result == "Example"
                assert "link=" not in result
            finally:
                mod._is_ci = original

    def test_non_ci_returns_rich_link(self):
        from verify_action_build import console as mod
        original = mod._is_ci
        mod._is_ci = False
        try:
            result = mod.link("https://example.com", "Example")
            assert "[link=https://example.com]Example[/link]" == result
        finally:
            mod._is_ci = original


class TestAskConfirm:
    def test_yes_answer(self):
        with mock.patch.object(
            __import__("verify_action_build.console", fromlist=["console"]).console,
            "input",
            return_value="y",
        ):
            assert ask_confirm("Continue?") is True

    def test_no_answer(self):
        with mock.patch.object(
            __import__("verify_action_build.console", fromlist=["console"]).console,
            "input",
            return_value="n",
        ):
            assert ask_confirm("Continue?") is False

    def test_quit_raises(self):
        with mock.patch.object(
            __import__("verify_action_build.console", fromlist=["console"]).console,
            "input",
            return_value="q",
        ):
            with pytest.raises(UserQuit):
                ask_confirm("Continue?")

    def test_empty_returns_default_true(self):
        with mock.patch.object(
            __import__("verify_action_build.console", fromlist=["console"]).console,
            "input",
            return_value="",
        ):
            assert ask_confirm("Continue?", default=True) is True

    def test_empty_returns_default_false(self):
        with mock.patch.object(
            __import__("verify_action_build.console", fromlist=["console"]).console,
            "input",
            return_value="",
        ):
            assert ask_confirm("Continue?", default=False) is False

    def test_eof_raises_user_quit(self):
        with mock.patch.object(
            __import__("verify_action_build.console", fromlist=["console"]).console,
            "input",
            side_effect=EOFError,
        ):
            with pytest.raises(UserQuit):
                ask_confirm("Continue?")


class TestRun:
    def test_success(self):
        result = run(["echo", "hello"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_failure_raises(self):
        with pytest.raises(subprocess.CalledProcessError):
            run(["false"])
