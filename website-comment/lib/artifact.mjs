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

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const run = promisify(execFile);

const MAX_ZIP_BYTES = 200 * 1024 * 1024;
const MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024;

/**
 * Archive entry names that must never be extracted.
 *
 * This runs BEFORE extraction on purpose. An entry that escapes the root lands
 * outside the extracted tree, where the post-extraction symlink walk can never
 * see it — so a pre-extraction name screen is the only place this defence can
 * exist.
 */
export function unsafeArchiveEntries(listing) {
  return String(listing ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .filter(
      (name) =>
        name.startsWith("/") ||
        name.startsWith("~") ||
        name.split("/").includes("..") ||
        name.includes("\\"),
    );
}

/**
 * True when the archive contains a symlink member.
 *
 * Name screening alone is not enough: members `a -> ../..` followed by `a/evil`
 * both have clean names, but unzip creates the link and then writes THROUGH it,
 * outside the tree — before any post-extraction check can run. Refusing symlink
 * members outright closes that ordering entirely.
 */
export function archiveHasSymlinkMembers(longListing) {
  return String(longListing ?? "")
    .split("\n")
    .some((line) => /^l[rwxsStT-]{9}/.test(line.trim()));
}

/** Total uncompressed size from `unzip -Z` trailer output, or null. */
export function uncompressedBytes(longListing) {
  const m = /([0-9]+)\s+bytes uncompressed/.exec(String(longListing ?? ""));
  return m ? Number(m[1]) : null;
}

export function createArtifactFetcher({ gh, token, fetchImpl = fetch }) {
  return async function fetchArtifact(runId) {
    const artifact = (await gh.listRunArtifacts(runId)).find((a) => a.name === "preview-site");
    if (!artifact || artifact.expired) return null;

    const dir = await mkdtemp(join(tmpdir(), "preview-artifact-"));
    try {
      // fetch rather than curl: the token never reaches a process argument
      // list, a non-2xx response is an error instead of a downloaded error
      // page, and the body size can be capped.
      const res = await fetchImpl(gh.artifactZipUrl(artifact.id), {
        headers: {
          authorization: `Bearer ${token}`,
          accept: "application/vnd.github+json",
          "x-github-api-version": "2022-11-28",
        },
        signal: AbortSignal.timeout(120_000),
      });
      if (!res.ok) throw new Error(`artifact download failed with ${res.status}`);

      // Check the advertised length before allocating: fetch buffers the whole
      // body in memory (curl -o streamed to disk instead), so the size cap is
      // only useful if it runs before, not just after, that allocation. The
      // header is advisory and may be absent or wrong, so the post-buffer
      // check below still runs too.
      const advertised = Number(res.headers.get("content-length") ?? "0");
      if (advertised > MAX_ZIP_BYTES) {
        throw new Error(`artifact advertises ${advertised} bytes, over the ${MAX_ZIP_BYTES} cap`);
      }

      const body = Buffer.from(await res.arrayBuffer());
      if (body.length > MAX_ZIP_BYTES) {
        throw new Error(`artifact is ${body.length} bytes, over the ${MAX_ZIP_BYTES} cap`);
      }

      const zip = join(dir, "artifact.zip");
      await writeFile(zip, body);

      const { stdout: names } = await run("unzip", ["-Z1", zip]);
      const unsafe = unsafeArchiveEntries(names);
      if (unsafe.length) {
        throw new Error(`archive contains unsafe entry names: ${unsafe.slice(0, 5).join(", ")}`);
      }

      const { stdout: longListing } = await run("unzip", ["-Z", zip]);
      if (archiveHasSymlinkMembers(longListing)) {
        throw new Error("archive contains symlink members");
      }
      const total = uncompressedBytes(longListing);
      if (total === null) {
        throw new Error("could not read the archive's uncompressed size");
      }
      if (total > MAX_UNCOMPRESSED_BYTES) {
        throw new Error(`archive expands to ${total} bytes, over the ${MAX_UNCOMPRESSED_BYTES} cap`);
      }

      const out = join(dir, "site");
      await run("unzip", ["-q", "-o", zip, "-d", out]);
      await rm(zip, { force: true });

      let meta = null;
      try {
        meta = JSON.parse(await readFile(join(out, "preview-meta.json"), "utf8"));
      } catch {
        meta = null;
      }
      return { dir: out, meta };
    } catch (err) {
      await rm(dir, { recursive: true, force: true });
      throw err;
    }
  };
}
