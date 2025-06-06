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

import unittest
import json
import os

from get_stash import ensure_json, gh_api, jq


class TestGetStash(unittest.TestCase):
    def test_jq(self):
        self.assertEqual(jq('{"a": 1}', ".a", ["-j"]).stdout, "1")

    def test_jq_file(self):
        this_dir = os.path.dirname(os.path.realpath(__file__))
        self.assertEqual(jq(this_dir + "/test.json", ".a").stdout, "1\n")

    def test_jq_error(self):
        with self.assertRaises(ValueError):
            jq("not_found.json", ".a")

    def test_gh_api(self):
        self.assertTrue(
            int(gh_api("rate_limit", options=["-q", ".resources.core.limit"]).stdout) >= 5000
        )

    def test_ensure_json(self):
        # use the actual response from gh_api to guard against changes in the API
        res = gh_api("rate_limit", options=["-q", ".resources.cre.limit"])
        res2 = '{"archive_download_url":"https://api.github.com/repos/assignUser/stash/actions/artifacts/1300409360/zip","created_at":"2024-03-06T00:01:41Z","expired":false,"expires_at":"2024-06-04T00:01:23Z","id":1300409360,"name":"test-stash-cross-macos-13-8_merge","node_id":"MDg6QXJ0aWZhY3QxMzAwNDA5MzYw","size_in_bytes":1303,"updated_at":"2024-03-06T00:01:41Z","url":"https://api.github.com/repos/assignUser/stash/actions/artifacts/1300409360","workflow_run":{"head_branch":"improve-filtering","head_repository_id":759960986,"head_sha":"ebaf714efc7535cdd13283e160dbb68fa446e39f","id":8164694143,"repository_id":759960986}}'
        count = ensure_json(res.stdout)
        self.assertDictEqual(count, {})
        self.assertDictEqual(ensure_json(res2), json.loads(res2))

if __name__ == "__main__":
    unittest.main()
