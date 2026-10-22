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

import { readdir, lstat, realpath } from "node:fs/promises";
import { join, relative, sep } from "node:path";

const SHA_RE = /^[0-9a-f]{40}$/;

/**
 * The artifact was produced by a job that ran pull-request code, so its
 * metadata is attacker-controlled. Without the SHA equality check below, a
 * fork could claim another PR's number and publish content under it.
 */
export function validateMeta(meta, expected) {
  if (!meta || typeof meta !== "object") {
    return { ok: false, reason: "metadata is missing or not an object" };
  }

  // Typed before any coercion: String() on an object can throw or run code
  // the artifact chose ({"pr": {"toString": null}}).
  const pr = typeof meta.pr === "number" || typeof meta.pr === "string" ? String(meta.pr) : "";
  if (!/^\d+$/.test(pr)) {
    // The value is the artifact's, so it is not echoed into the PR's comment.
    return { ok: false, reason: "pr is not a plain number" };
  }
  if (Number(pr) !== expected.number) {
    // Not a defect of this PR's build: another PR with the same head commit
    // built it. The caller keeps looking rather than reporting a refusal.
    return { ok: false, otherPr: true, reason: `pr ${pr} does not match the PR being published (${expected.number})` };
  }

  const sha = typeof meta.headSha === "string" ? meta.headSha : "";
  if (!SHA_RE.test(sha)) {
    return { ok: false, reason: "headSha is not a 40-character hex sha" };
  }
  if (sha !== expected.headSha) {
    return { ok: false, reason: `headSha ${sha} is not the PR's current head (${expected.headSha})` };
  }

  return { ok: true };
}

/**
 * Paths in an extracted tree that must never be published: symlinks.
 *
 * This walks an already-extracted tree, so it cannot see an archive entry that
 * escaped the root during extraction — such an entry lands outside the tree and
 * no walk of it would find it. Screening archive entry names before extraction
 * is the defence for that, and it lives in the extraction step.
 */
export async function findUnsafeEntries(root) {
  const rootReal = await realpath(root);
  const unsafe = [];

  async function walk(dir) {
    for (const name of await readdir(dir)) {
      const full = join(dir, name);
      const stat = await lstat(full);

      if (stat.isSymbolicLink()) {
        unsafe.push(relative(rootReal, full).split(sep).join("/"));
        continue;
      }
      if (stat.isDirectory()) await walk(full);
    }
  }

  await walk(rootReal);
  return unsafe.sort();
}
