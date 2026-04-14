#!/usr/bin/env bash
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
#
# Download a stash artifact with retries.
#
# Required env vars:
#   STASH_RUN_ID     - workflow run ID the artifact was produced by
#   STASH_NAME       - artifact name
#   STASH_DIR        - destination directory
#   REPO             - owner/name of the repository
#   RETRY_COUNT      - max download attempts (retries on gh exit code 1)
#   FAIL_ON_DOWNLOAD - "true" to exit 1 on download failure, else "false"
#   CLEAN            - "true" to remove STASH_DIR before downloading
#   GITHUB_OUTPUT    - file to write the `download` output to

# Disable errexit explicitly so a single failing command (gh run download,
# rm -rf) cannot abort the step — failures are handled via $? below.
set +e

if [[ "${CLEAN}" == "true" ]]; then
    if [[ -d "$STASH_DIR" ]]; then
        echo "Removing existing stash directory: $STASH_DIR"
        rm -rf "$STASH_DIR"
    fi
fi

download="failed"
attempt=1
while (( attempt <= RETRY_COUNT )); do
    echo "Downloading stash (attempt $attempt of $RETRY_COUNT)..."
    gh run download "$STASH_RUN_ID" \
                    --name "$STASH_NAME" \
                    --dir "$STASH_DIR" \
                    -R "$REPO"
    rc=$?
    if (( rc == 0 )); then
        download="success"
        break
    fi
    if (( rc != 1 )); then
        echo "::warning ::gh run download failed with exit code $rc; not retrying."
        break
    fi
    echo "::warning ::gh run download failed with exit code 1 on attempt $attempt."
    attempt=$(( attempt + 1 ))
done

echo "download=$download" >> "$GITHUB_OUTPUT"

if [[ "$download" != "success" && "$FAIL_ON_DOWNLOAD" == "true" ]]; then
    echo "::error ::Stash artifact download failed after $RETRY_COUNT attempt(s) and fail-on-download is true."
    exit 1
fi
exit 0
