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

import os
import shutil
import tempfile
import textwrap
import unittest

from check_asf_allowlist import (
    collect_action_refs,
    find_action_refs,
    is_allowed,
    load_allowlist,
)


class TestFindActionRefs(unittest.TestCase):
    """Tests for recursive uses: extraction from parsed YAML trees."""

    def test_simple_step(self):
        tree = {"jobs": {"build": {"steps": [{"uses": "actions/checkout@v4"}]}}}
        self.assertEqual(list(find_action_refs(tree)), ["actions/checkout@v4"])

    def test_multiple_steps(self):
        tree = {
            "jobs": {
                "build": {
                    "steps": [
                        {"uses": "actions/checkout@v4"},
                        {"run": "echo hello"},
                        {"uses": "actions/setup-python@v5"},
                    ]
                }
            }
        }
        refs = list(find_action_refs(tree))
        self.assertEqual(refs, ["actions/checkout@v4", "actions/setup-python@v5"])

    def test_multiple_jobs(self):
        tree = {
            "jobs": {
                "build": {"steps": [{"uses": "actions/checkout@v4"}]},
                "test": {"steps": [{"uses": "actions/setup-java@v4"}]},
            }
        }
        refs = list(find_action_refs(tree))
        self.assertIn("actions/checkout@v4", refs)
        self.assertIn("actions/setup-java@v4", refs)

    def test_no_uses(self):
        tree = {"jobs": {"build": {"steps": [{"run": "echo hello"}]}}}
        self.assertEqual(list(find_action_refs(tree)), [])

    def test_empty_tree(self):
        self.assertEqual(list(find_action_refs({})), [])
        self.assertEqual(list(find_action_refs([])), [])
        self.assertEqual(list(find_action_refs(None)), [])

    def test_reusable_workflow(self):
        tree = {
            "jobs": {
                "call-workflow": {
                    "uses": "org/repo/.github/workflows/reusable.yml@main"
                }
            }
        }
        refs = list(find_action_refs(tree))
        self.assertEqual(refs, ["org/repo/.github/workflows/reusable.yml@main"])

    def test_deeply_nested(self):
        tree = {"a": {"b": {"c": {"d": [{"uses": "deep/action@v1"}]}}}}
        self.assertEqual(list(find_action_refs(tree)), ["deep/action@v1"])

    def test_uses_non_string_ignored(self):
        """uses: with a non-string value (e.g., int) should be ignored."""
        tree = {"jobs": {"build": {"steps": [{"uses": 42}]}}}
        self.assertEqual(list(find_action_refs(tree)), [])


class TestIsAllowed(unittest.TestCase):
    """Tests for allowlist matching logic."""

    def setUp(self):
        self.allowlist = [
            "astral-sh/setup-uv@681c641aba71e4a1c380be3ab5e12ad51f415867",
            "codecov/codecov-action@*",
            "golangci/*@*",
        ]

    def test_trusted_owner_actions(self):
        self.assertTrue(is_allowed("actions/checkout@v4", self.allowlist))

    def test_trusted_owner_github(self):
        self.assertTrue(is_allowed("github/codeql-action/init@v3", self.allowlist))

    def test_trusted_owner_apache(self):
        self.assertTrue(
            is_allowed("apache/infrastructure-actions/stash@main", self.allowlist)
        )

    def test_exact_sha_match(self):
        self.assertTrue(
            is_allowed(
                "astral-sh/setup-uv@681c641aba71e4a1c380be3ab5e12ad51f415867",
                self.allowlist,
            )
        )

    def test_exact_sha_no_match(self):
        self.assertFalse(
            is_allowed(
                "astral-sh/setup-uv@0000000000000000000000000000000000000000",
                self.allowlist,
            )
        )

    def test_wildcard_ref(self):
        self.assertTrue(
            is_allowed("codecov/codecov-action@v4", self.allowlist)
        )
        self.assertTrue(
            is_allowed(
                "codecov/codecov-action@abc123def456",
                self.allowlist,
            )
        )

    def test_wildcard_repo_and_ref(self):
        self.assertTrue(
            is_allowed("golangci/golangci-lint-action@abc123", self.allowlist)
        )
        self.assertTrue(
            is_allowed("golangci/some-other-action@v1", self.allowlist)
        )

    def test_not_allowed(self):
        self.assertFalse(
            is_allowed("evil-org/evil-action@v1", self.allowlist)
        )

    def test_empty_allowlist(self):
        self.assertFalse(is_allowed("some/action@v1", []))

    def test_owner_only_no_slash(self):
        """An action ref that is just an owner name (edge case) should still work."""
        self.assertFalse(is_allowed("random", self.allowlist))


class TestLoadAllowlist(unittest.TestCase):
    """Tests for loading allowlist from a YAML file."""

    def test_load_valid_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("- owner/action@abc123\n- other/action@*\n")
            f.flush()
            result = load_allowlist(f.name)
        os.unlink(f.name)
        self.assertEqual(result, ["owner/action@abc123", "other/action@*"])

    def test_load_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("")
            f.flush()
            result = load_allowlist(f.name)
        os.unlink(f.name)
        self.assertEqual(result, [])


class TestCollectActionRefs(unittest.TestCase):
    """Tests for collecting action refs from workflow files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.github_dir = os.path.join(self.tmpdir, ".github", "workflows")
        os.makedirs(self.github_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_workflow(self, filename, content):
        filepath = os.path.join(self.github_dir, filename)
        with open(filepath, "w") as f:
            f.write(textwrap.dedent(content))
        return filepath

    def test_collects_refs(self):
        self._write_workflow(
            "ci.yml",
            """\
            name: CI
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: codecov/codecov-action@v4
            """,
        )
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        refs = collect_action_refs(scan_glob)
        self.assertIn("actions/checkout@v4", refs)
        self.assertIn("codecov/codecov-action@v4", refs)

    def test_skips_local_refs(self):
        self._write_workflow(
            "ci.yml",
            """\
            name: CI
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: ./local-action
                  - uses: actions/checkout@v4
            """,
        )
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        refs = collect_action_refs(scan_glob)
        self.assertNotIn("./local-action", refs)
        self.assertIn("actions/checkout@v4", refs)

    def test_skips_docker_refs(self):
        self._write_workflow(
            "ci.yml",
            """\
            name: CI
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: docker://alpine:3.18
                  - uses: actions/checkout@v4
            """,
        )
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        refs = collect_action_refs(scan_glob)
        self.assertNotIn("docker://alpine:3.18", refs)
        self.assertIn("actions/checkout@v4", refs)

    def test_empty_yaml(self):
        self._write_workflow("empty.yml", "")
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        refs = collect_action_refs(scan_glob)
        self.assertEqual(refs, {})

    def test_invalid_yaml_errors(self):
        self._write_workflow("bad.yml", ":\n  - :\n  invalid: [")
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        with self.assertRaises(SystemExit):
            collect_action_refs(scan_glob)

    def test_tracks_multiple_files(self):
        self._write_workflow(
            "ci.yml",
            """\
            name: CI
            on: push
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
            """,
        )
        self._write_workflow(
            "release.yml",
            """\
            name: Release
            on: push
            jobs:
              release:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
            """,
        )
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        refs = collect_action_refs(scan_glob)
        self.assertEqual(len(refs["actions/checkout@v4"]), 2)

    def test_no_matching_files(self):
        scan_glob = os.path.join(self.tmpdir, ".github/**/*.yml")
        # no files written — github_dir exists but is empty
        refs = collect_action_refs(scan_glob)
        self.assertEqual(refs, {})


if __name__ == "__main__":
    unittest.main()
