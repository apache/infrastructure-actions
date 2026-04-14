# Copyright (c) The stash contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for download_stash.download_stash."""

import tempfile
import unittest
from pathlib import Path

from download_stash import download_stash


class FakeGh:
    """Emits successive exit codes for ``gh run download`` invocations.

    After the provided list of codes is exhausted, the last code is
    reused for any further calls. Each call is recorded so tests can
    assert how many times ``gh`` was invoked.
    """

    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = []

    def __call__(self, run_id, name, dest, repo):
        self.calls.append((run_id, name, dest, repo))
        idx = min(len(self.calls) - 1, len(self.codes) - 1)
        return self.codes[idx]


class TestDownloadStash(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.stash_dir = self.tmp / "target"
        self.stash_dir.mkdir()
        self.output_file = self.tmp / "github_output"
        self.output_file.touch()

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, **overrides):
        base = {
            "STASH_RUN_ID": "42",
            "STASH_NAME": "fake-stash",
            "STASH_DIR": str(self.stash_dir),
            "REPO": "test/repo",
            "RETRY_COUNT": "3",
            "FAIL_ON_DOWNLOAD": "false",
            "CLEAN": "false",
            "GITHUB_OUTPUT": str(self.output_file),
        }
        base.update(overrides)
        return base

    def read_output(self):
        return self.output_file.read_text()

    def test_success_first_attempt(self):
        gh = FakeGh([0])
        rc = download_stash(self.env(), run_download=gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=success", self.read_output())
        self.assertEqual(len(gh.calls), 1)

    def test_retry_on_exit_1_until_success(self):
        gh = FakeGh([1, 1, 0])
        rc = download_stash(self.env(), run_download=gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=success", self.read_output())
        self.assertEqual(len(gh.calls), 3)

    def test_all_retries_fail_tolerated(self):
        gh = FakeGh([1])
        rc = download_stash(self.env(), run_download=gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 3)

    def test_all_retries_fail_fail_on_download(self):
        gh = FakeGh([1])
        rc = download_stash(
            self.env(RETRY_COUNT="2", FAIL_ON_DOWNLOAD="true"),
            run_download=gh,
        )
        self.assertEqual(rc, 1)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 2)

    def test_non_transient_exit_not_retried(self):
        gh = FakeGh([2])
        rc = download_stash(self.env(RETRY_COUNT="5"), run_download=gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 1)

    def test_clean_removes_stash_dir(self):
        (self.stash_dir / "leftover").touch()
        gh = FakeGh([0])
        rc = download_stash(
            self.env(CLEAN="true", RETRY_COUNT="1"), run_download=gh
        )
        self.assertEqual(rc, 0)
        self.assertFalse((self.stash_dir / "leftover").exists())
        self.assertIn("download=success", self.read_output())

    def test_clean_false_preserves_stash_dir(self):
        (self.stash_dir / "leftover").touch()
        gh = FakeGh([0])
        rc = download_stash(
            self.env(CLEAN="false", RETRY_COUNT="1"), run_download=gh
        )
        self.assertEqual(rc, 0)
        self.assertTrue((self.stash_dir / "leftover").exists())

    def test_stops_on_first_non_transient(self):
        gh = FakeGh([1, 2, 0])
        rc = download_stash(self.env(RETRY_COUNT="5"), run_download=gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 2)


if __name__ == "__main__":
    unittest.main()
