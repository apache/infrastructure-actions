// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

import { writeFile } from "node:fs/promises";
import { buildAnchors } from "./anchors.mjs";

/**
 * Write preview-meta.json in the UNPRIVILEGED pull-request build.
 *
 * The anchor manifest is derived from the PR's diff, which is pull-request
 * content the privileged publisher must never read. So it is computed here,
 * with this job's read-only token, and travels inside the artifact; the
 * publisher treats it as untrusted and sanitises it (anchors.mjs).
 */
export async function previewMeta({ pr, headSha, files }) {
  return { pr, headSha, anchors: buildAnchors(files) };
}

async function listFiles({ repo, pr, token }) {
  const out = [];
  for (let page = 1; ; page += 1) {
    const res = await fetch(
      `https://api.github.com/repos/${repo}/pulls/${pr}/files?per_page=100&page=${page}`,
      {
        headers: {
          authorization: `Bearer ${token}`,
          accept: "application/vnd.github+json",
          "x-github-api-version": "2022-11-28",
        },
      },
    );
    if (!res.ok) throw new Error(`listing PR files failed with ${res.status}`);
    const batch = await res.json();
    if (!Array.isArray(batch) || batch.length === 0) break;
    out.push(...batch);
    if (batch.length < 100) break;
  }
  return out;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { GITHUB_REPOSITORY: repo, GITHUB_TOKEN: token, PR_NUMBER, HEAD_SHA, OUT } = process.env;
  if (!/^\d+$/.test(String(PR_NUMBER)) || !/^[0-9a-f]{40}$/.test(String(HEAD_SHA)) || !OUT) {
    console.error("PR_NUMBER, HEAD_SHA (40-hex) and OUT are required");
    process.exit(1);
  }

  // The overlay degrades without anchors, so a failed listing must not fail
  // the build.
  let files = [];
  try {
    files = await listFiles({ repo, pr: PR_NUMBER, token });
  } catch (err) {
    console.error(`preview: no anchors, ${err.message}`);
  }

  const meta = await previewMeta({ pr: Number(PR_NUMBER), headSha: HEAD_SHA, files });
  await writeFile(OUT, `${JSON.stringify(meta)}\n`);
}
