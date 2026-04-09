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
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 1
