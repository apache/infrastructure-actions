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

import { writeFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { overlayFiles, injectIntoTree } from "./overlay-files.mjs";

/**
 * Put the review overlay on the published site, built from the default branch.
 *
 * Run after an annotated build, so a marked region resolves to its source
 * line. In this mode the overlay shows no preview banner — this is the
 * published site — and a comment opens a new issue linking the source on
 * `branch`.
 *
 * `generated` lists source-path prefixes whose files are not in this
 * repository (pages synced from elsewhere, say): a line there cannot be linked
 * here, so the issue carries only the page URL.
 */
export async function injectMain({ dir, repo, branch = "main", sha, generated = [] }) {
  const files = await overlayFiles({
    repo,
    mode: "main",
    branch,
    sha: String(sha ?? "").slice(0, 7),
    generated,
  });
  for (const [path, content] of Object.entries(files)) {
    const full = join(dir, path);
    await mkdir(dirname(full), { recursive: true });
    await writeFile(full, content);
  }
  await injectIntoTree(dir);
}

/** One prefix per line or comma; blanks dropped. */
export const parseGenerated = (raw) =>
  String(raw ?? "").split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

if (import.meta.url === `file://${process.argv[1]}`) {
  const dir = process.argv[2];
  const { GITHUB_REPOSITORY: repo, GITHUB_SHA: sha, PREVIEW_BRANCH, PREVIEW_GENERATED } = process.env;
  if (!dir || !repo) {
    console.error("usage: GITHUB_REPOSITORY=owner/repo node inject-main.mjs <site dir>");
    process.exit(1);
  }
  await injectMain({
    dir,
    repo,
    sha,
    branch: PREVIEW_BRANCH || "main",
    generated: parseGenerated(PREVIEW_GENERATED),
  });
  console.log(`review overlay injected into ${dir} for ${repo}`);
}
