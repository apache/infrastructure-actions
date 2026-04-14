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
"""Download a stash artifact with retries.

Required env vars:
  STASH_RUN_ID     - workflow run ID the artifact was produced by
  STASH_NAME       - artifact name
  STASH_DIR        - destination directory
  REPO             - owner/name of the repository
  RETRY_COUNT      - max download attempts (retries on gh exit code 1)
  FAIL_ON_DOWNLOAD - "true" to exit 1 on download failure, else "false"
  CLEAN            - "true" to remove STASH_DIR before downloading
  GITHUB_OUTPUT    - file to write the `download` output to
"""

import os
import shutil
import subprocess
import sys
from typing import Callable, Mapping


def run_gh_download(run_id: str, name: str, dest: str, repo: str) -> int:
    """Invoke ``gh run download`` and return its exit code."""
    return subprocess.run(
        [
            "gh", "run", "download", run_id,
            "--name", name,
            "--dir", dest,
            "-R", repo,
        ],
        check=False,
    ).returncode


def download_stash(
    env: Mapping[str, str],
    run_download: Callable[[str, str, str, str], int] = run_gh_download,
) -> int:
    """Run the clean/retry/fail-on-download logic.

    Returns the desired process exit code (0 for success or tolerated
    failure, 1 when the download failed and ``FAIL_ON_DOWNLOAD`` is
    ``"true"``). The ``run_download`` hook exists so tests can stub out
    the real ``gh`` call.
    """
    stash_run_id = env["STASH_RUN_ID"]
    stash_name = env["STASH_NAME"]
    stash_dir = env["STASH_DIR"]
    repo = env["REPO"]
    retry_count = int(env.get("RETRY_COUNT", "1"))
    fail_on_download = env.get("FAIL_ON_DOWNLOAD", "false").lower() == "true"
    clean = env.get("CLEAN", "false").lower() == "true"
    github_output = env["GITHUB_OUTPUT"]

    if clean and os.path.isdir(stash_dir):
        print(f"Removing existing stash directory: {stash_dir}")
        shutil.rmtree(stash_dir, ignore_errors=True)

    download = "failed"
    for attempt in range(1, retry_count + 1):
        print(f"Downloading stash (attempt {attempt} of {retry_count})...", flush=True)
        rc = run_download(stash_run_id, stash_name, stash_dir, repo)
        if rc == 0:
            download = "success"
            break
        if rc != 1:
            print(
                f"::warning ::gh run download failed with exit code {rc}; "
                "not retrying."
            )
            break
        print(
            f"::warning ::gh run download failed with exit code 1 on "
            f"attempt {attempt}."
        )

    with open(github_output, "a", encoding="utf-8") as f:
        f.write(f"download={download}\n")

    if download != "success" and fail_on_download:
        print(
            f"::error ::Stash artifact download failed after {retry_count} "
            "attempt(s) and fail-on-download is true."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(download_stash(os.environ))
