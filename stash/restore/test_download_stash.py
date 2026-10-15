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

from download_stash import MAX_RETRY_DELAY, compute_backoff, download_stash


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


class FakeSleep:
    """Records the backoff waits instead of actually sleeping."""

    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(seconds)


class TestDownloadStash(unittest.TestCase):
    def setUp(self):
        self.sleep = FakeSleep()
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
            "RETRY_DELAY": "0",
            "FAIL_ON_DOWNLOAD": "false",
            "CLEAN": "false",
            "GITHUB_OUTPUT": str(self.output_file),
        }
        base.update(overrides)
        return base

    def read_output(self):
        return self.output_file.read_text()

    def run_download_stash(self, env, gh, rand=lambda: 1.0):
        return download_stash(env, run_download=gh, sleep=self.sleep, rand=rand)

    def test_success_first_attempt(self):
        gh = FakeGh([0])
        rc = self.run_download_stash(self.env(), gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=success", self.read_output())
        self.assertEqual(len(gh.calls), 1)

    def test_retry_on_exit_1_until_success(self):
        gh = FakeGh([1, 1, 0])
        rc = self.run_download_stash(self.env(), gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=success", self.read_output())
        self.assertEqual(len(gh.calls), 3)

    def test_all_retries_fail_tolerated(self):
        gh = FakeGh([1])
        rc = self.run_download_stash(self.env(), gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 3)

    def test_all_retries_fail_fail_on_download(self):
        gh = FakeGh([1])
        rc = self.run_download_stash(self.env(RETRY_COUNT="2", FAIL_ON_DOWNLOAD="true"), gh)
        self.assertEqual(rc, 1)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 2)

    def test_non_transient_exit_not_retried(self):
        gh = FakeGh([2])
        rc = self.run_download_stash(self.env(RETRY_COUNT="5"), gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 1)

    def test_clean_removes_stash_dir(self):
        (self.stash_dir / "leftover").touch()
        gh = FakeGh([0])
        rc = self.run_download_stash(self.env(CLEAN="true", RETRY_COUNT="1"), gh)
        self.assertEqual(rc, 0)
        self.assertFalse((self.stash_dir / "leftover").exists())
        self.assertIn("download=success", self.read_output())

    def test_clean_false_preserves_stash_dir(self):
        (self.stash_dir / "leftover").touch()
        gh = FakeGh([0])
        rc = self.run_download_stash(self.env(CLEAN="false", RETRY_COUNT="1"), gh)
        self.assertEqual(rc, 0)
        self.assertTrue((self.stash_dir / "leftover").exists())

    def test_stops_on_first_non_transient(self):
        gh = FakeGh([1, 2, 0])
        rc = self.run_download_stash(self.env(RETRY_COUNT="5"), gh)
        self.assertEqual(rc, 0)
        self.assertIn("download=failed", self.read_output())
        self.assertEqual(len(gh.calls), 2)

    def test_backoff_between_transient_failures(self):
        gh = FakeGh([1, 1, 0])
        rc = self.run_download_stash(self.env(RETRY_DELAY="5"), gh)
        self.assertEqual(rc, 0)
        self.assertEqual(self.sleep.waits, [5.0, 10.0])

    def test_no_backoff_after_final_attempt(self):
        gh = FakeGh([1])
        self.run_download_stash(self.env(RETRY_COUNT="2", RETRY_DELAY="5"), gh)
        self.assertEqual(len(self.sleep.waits), 1)

    def test_no_backoff_on_non_transient_exit(self):
        gh = FakeGh([2])
        self.run_download_stash(self.env(RETRY_DELAY="5"), gh)
        self.assertEqual(self.sleep.waits, [])

    def test_zero_retry_delay_does_not_sleep(self):
        gh = FakeGh([1, 1, 0])
        self.run_download_stash(self.env(RETRY_DELAY="0"), gh)
        self.assertEqual(self.sleep.waits, [])

    def test_retry_delay_defaults_to_no_wait(self):
        gh = FakeGh([1, 1, 0])
        env = self.env()
        del env["RETRY_DELAY"]
        self.run_download_stash(env, gh)
        self.assertEqual(self.sleep.waits, [])

    def test_backoff_is_jittered(self):
        gh = FakeGh([1, 1, 0])
        self.run_download_stash(self.env(RETRY_DELAY="8"), gh, rand=lambda: 0.25)
        self.assertEqual(self.sleep.waits, [2.0, 4.0])

    def test_backoff_is_capped(self):
        self.assertEqual(compute_backoff(20, 5.0, lambda: 1.0), MAX_RETRY_DELAY)


if __name__ == "__main__":
    unittest.main()
