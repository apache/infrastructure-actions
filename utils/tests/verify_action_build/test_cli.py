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
import os
from unittest import mock

import pytest

from verify_action_build.cli import main


class TestMain:
    def test_no_args_shows_help_and_exits(self):
        with mock.patch("sys.argv", ["verify-action-build"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_missing_docker_exits(self):
        with mock.patch("sys.argv", ["verify-action-build", "org/repo@" + "a" * 40]):
            with mock.patch("shutil.which", return_value=None):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1

    def test_no_gh_without_token_exits(self):
        with mock.patch("sys.argv", ["verify-action-build", "--no-gh", "org/repo@" + "a" * 40]):
            with mock.patch("shutil.which", return_value="/usr/bin/docker"):
                with mock.patch.dict("os.environ", {}, clear=True):
                    with mock.patch("verify_action_build.cli.gh_auth_token", return_value=None):
                        with pytest.raises(SystemExit) as exc_info:
                            main()
                        assert exc_info.value.code == 1

    def test_no_gh_borrows_token_from_gh_cli(self):
        """With no token given, --no-gh should fall back to `gh auth token`."""
        with mock.patch("sys.argv", ["verify-action-build", "--no-gh", "org/repo@" + "a" * 40]):
            with mock.patch("shutil.which", return_value="/usr/bin/docker"):
                with mock.patch.dict("os.environ", {}, clear=True):
                    with mock.patch(
                        "verify_action_build.cli.gh_auth_token", return_value="ghp_borrowed"
                    ) as borrow:
                        with mock.patch("verify_action_build.cli.GitHubClient") as gh_cls:
                            with mock.patch(
                                "verify_action_build.cli.verify_single_action", return_value=True
                            ):
                                with pytest.raises(SystemExit) as exc_info:
                                    main()
            assert exc_info.value.code == 0
            assert gh_cls.call_args.kwargs["token"] == "ghp_borrowed"
            # Primed for the raw api.github.com calls the checks make, and reused by the
            # --no-gh branch — so the CLI is only shelled out to once.
            assert borrow.call_count == 1

    def test_env_token_primed_for_raw_api_calls(self):
        """A borrowed token also lands in the environment the security checks read."""
        seen = {}
        with mock.patch("sys.argv", ["verify-action-build", "org/repo@" + "a" * 40]):
            with mock.patch("shutil.which", return_value="/usr/bin/docker"):
                with mock.patch.dict("os.environ", {}, clear=True):
                    with mock.patch(
                        "verify_action_build.cli.gh_auth_token", return_value="ghp_borrowed"
                    ):
                        with mock.patch("verify_action_build.cli.GitHubClient"):
                            with mock.patch(
                                "verify_action_build.cli.verify_single_action",
                                side_effect=lambda *a, **k: seen.update(
                                    token=os.environ.get("GITHUB_TOKEN")
                                )
                                or True,
                            ):
                                with pytest.raises(SystemExit):
                                    main()
                    assert seen["token"] == "ghp_borrowed"

    def test_from_pr_with_no_added_refs_passes(self):
        removal_only_diff = (
            "diff --git a/actions.yml b/actions.yml\n"
            "--- a/actions.yml\n"
            "+++ b/actions.yml\n"
            "@@ -10,5 +10,0 @@\n"
            "-some-org/some-action:\n"
            "-  " + "a" * 40 + ":\n"
            "-    tag: v1.0.0\n"
        )
        with mock.patch("sys.argv", ["verify-action-build", "--from-pr", "999"]):
            with mock.patch("shutil.which", return_value="/usr/bin/docker"):
                with mock.patch(
                    "verify_action_build.cli.GitHubClient"
                ) as gh_cls:
                    gh_cls.return_value.get_pr_diff.return_value = removal_only_diff
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 0

    def test_from_pr_when_diff_fetch_fails_errors(self):
        with mock.patch("sys.argv", ["verify-action-build", "--from-pr", "999"]):
            with mock.patch("shutil.which", return_value="/usr/bin/docker"):
                with mock.patch(
                    "verify_action_build.cli.GitHubClient"
                ) as gh_cls:
                    gh_cls.return_value.get_pr_diff.return_value = None
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 1
