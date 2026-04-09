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

from verify_action_build.docker_build import (
    detect_node_version,
    _read_dockerfile_template,
    _print_docker_build_steps,
)


class TestDetectNodeVersion:
    def test_detects_node20(self):
        response = mock.Mock()
        response.ok = True
        response.text = """\
name: Test
runs:
  using: node20
  main: dist/index.js
"""
        with mock.patch("verify_action_build.docker_build.requests.get", return_value=response):
            version = detect_node_version("org", "repo", "abc123")
        assert version == "20"

    def test_detects_node16(self):
        response = mock.Mock()
        response.ok = True
        response.text = """\
name: Test
runs:
  using: 'node16'
  main: dist/index.js
"""
        with mock.patch("verify_action_build.docker_build.requests.get", return_value=response):
            version = detect_node_version("org", "repo", "abc123")
        assert version == "16"

    def test_falls_back_to_20(self):
        response = mock.Mock()
        response.ok = False
        with mock.patch("verify_action_build.docker_build.requests.get", return_value=response):
            version = detect_node_version("org", "repo", "abc123")
        assert version == "20"

    def test_network_error_falls_back(self):
        import requests as req
        with mock.patch("verify_action_build.docker_build.requests.get", side_effect=req.RequestException):
            version = detect_node_version("org", "repo", "abc123")
        assert version == "20"

    def test_sub_path_tried_first(self):
        calls = []
        response_sub = mock.Mock()
        response_sub.ok = True
        response_sub.text = "  using: node22\n  main: dist/index.js\n"

        def track_get(url, **kwargs):
            calls.append(url)
            if "sub/action.yml" in url:
                return response_sub
            resp = mock.Mock()
            resp.ok = False
            return resp

        with mock.patch("verify_action_build.docker_build.requests.get", side_effect=track_get):
            version = detect_node_version("org", "repo", "abc123", sub_path="sub")
        assert version == "22"
        assert any("sub/action.yml" in c for c in calls)


class TestReadDockerfileTemplate:
    def test_reads_file(self):
        content = _read_dockerfile_template()
        assert "FROM node:" in content
        assert "WORKDIR /action" in content
        assert "ARG REPO_URL" in content
        assert "ARG COMMIT_HASH" in content

    def test_contains_build_steps(self):
        content = _read_dockerfile_template()
        assert "npm" in content or "yarn" in content or "pnpm" in content
        assert "/rebuilt-dist" in content
        assert "/original-dist" in content


class TestPrintDockerBuildSteps:
    def test_parses_build_output(self):
        result = mock.Mock()
        result.stdout = ""
        result.stderr = """\
#5 [3/12] RUN apt-get update
#5 DONE 1.2s
#6 [4/12] RUN git clone
#6 CACHED
"""
        # Just verify it doesn't crash
        _print_docker_build_steps(result)

    def test_handles_empty_output(self):
        result = mock.Mock()
        result.stdout = ""
        result.stderr = ""
        _print_docker_build_steps(result)
