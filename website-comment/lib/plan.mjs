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

const BRANCH_RE = /^preview\/pr(\d+)-staging$/;

/**
 * Pure decision step: what this run should publish, tombstone and delete.
 *
 * Tombstone and delete are separate runs on purpose. Deleting a branch does
 * not unstage the site, so the tombstone must be pushed and allowed to
 * propagate before the branch goes away.
 */
export function planActions({ openPulls, armedByPr, previewBranches, tombstoned }) {
  const open = new Set(openPulls);
  const publish = openPulls.filter((n) => armedByPr.get(n) === true);

  const tombstone = [];
  const remove = [];

  for (const branch of previewBranches) {
    const match = BRANCH_RE.exec(branch);
    if (!match) continue;

    const pr = Number(match[1]);
    // An open PR whose armed state was never resolved is UNKNOWN, not disarmed.
    // Retiring overwrites live content, so missing data must never be more
    // dangerous than an explicit `false`: leave the branch alone this run and
    // let a later run decide once the state is known.
    if (open.has(pr) && !armedByPr.has(pr)) continue;
    const retired = !open.has(pr) || armedByPr.get(pr) !== true;
    if (!retired) continue;

    if (tombstoned.has(branch)) remove.push(branch);
    else tombstone.push(pr);
  }

  return { publish, tombstone, delete: remove };
}
