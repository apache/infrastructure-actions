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

from verify_action_build.diff_source import is_source_file


class TestIsSourceFile:
    def test_js_and_ts_sources(self):
        for name in ("src/main.ts", "index.js", "bundle.mjs", "helper.cjs"):
            assert is_source_file(Path(name)), name

    def test_metadata_files(self):
        for name in ("action.yml", "action.yaml", "package.json"):
            assert is_source_file(Path(name)), name

    def test_shell_script_is_source(self):
        # uraimo/run-on-arch-action ships src/run-on-arch.sh, which
        # src/run-on-arch.js exec()s.  Skipping it hid a +12/-1 change to the
        # docker run invocation from the reviewer.
        assert is_source_file(Path("src/run-on-arch.sh"))
        assert is_source_file(Path("scripts/install.bash"))
        assert is_source_file(Path("setup.ps1"))

    def test_interpreter_scripts_are_source(self):
        assert is_source_file(Path("scripts/publish.py"))
        assert is_source_file(Path("bin/release.rb"))
        assert is_source_file(Path("tools/gen.pl"))

    def test_plain_dockerfile(self):
        assert is_source_file(Path("Dockerfile"))

    def test_suffixed_dockerfile(self):
        # Dockerfile.aarch64.trixie has a .trixie suffix, so only the name
        # prefix identifies it.
        assert is_source_file(Path("Dockerfiles/Dockerfile.aarch64.trixie"))
        assert is_source_file(Path("Dockerfile.loongarch64.alpine_latest"))

    def test_non_source_files_excluded(self):
        for name in ("README.md", "LICENSE", "logo.png", "notes.txt", "core_bg.wasm"):
            assert not is_source_file(Path(name)), name

    def test_name_containing_dockerfile_not_prefixed(self):
        # Only a leading "Dockerfile" counts; a mention elsewhere does not.
        assert not is_source_file(Path("docs/about-Dockerfile.md"))
