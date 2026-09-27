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

/**
 * Branches the publisher never touches, whatever pull requests point at them.
 * `main` and `publish` serve the site; `preview/*` is reaped by planActions
 * (tombstone first, because deleting it does not unstage); `asf-*` is ASF
 * infrastructure's.
 */
const KEEP = new Set(["main", "publish"]);
const KEEP_PREFIXES = ["preview/", "asf-"];

/**
 * Pure decision step: which head branches in this repository belong to pull
 * requests that are all closed, and can be deleted.
 *
 * A branch is deleted only when every one of these holds:
 *   - it is not protected, kept by name, or kept by prefix;
 *   - at least one pull request was opened FROM it, in this repository — a
 *     branch that never had a PR is someone's work in progress;
 *   - none of those pull requests is still open;
 *   - its tip is still the head commit of one of them. A push after the PR
 *     closed means someone is working on it again, so it is left alone.
 *
 * `pullsByBranch` maps a branch name to the pull requests (state "all") whose
 * head is that branch; a branch with no entry was not looked up and is kept.
 */
export function staleHeadBranches({ branches, pullsByBranch, repo }) {
  const out = [];

  for (const branch of branches) {
    const name = branch?.name;
    if (!name || branch.protected) continue;
    if (KEEP.has(name) || KEEP_PREFIXES.some((p) => name.startsWith(p))) continue;

    const pulls = (pullsByBranch.get(name) ?? []).filter(
      (p) => p?.head?.ref === name && p?.head?.repo?.full_name === repo,
    );
    if (pulls.length === 0) continue;
    if (pulls.some((p) => p.state !== "closed")) continue;

    const tip = branch?.commit?.sha;
    if (!tip || !pulls.some((p) => p.head.sha === tip)) continue;

    out.push(name);
  }

  return out;
}
