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

import { test } from "node:test";
import assert from "node:assert/strict";
import { staleHeadBranches } from "./stale.mjs";

const REPO = "apache/magpie-site";
const TIP = "a".repeat(40);

const branch = (name, { sha = TIP, protectedBranch = false } = {}) => ({
  name,
  protected: protectedBranch,
  commit: { sha },
});
const pr = (ref, { state = "closed", sha = TIP, repo = REPO } = {}) => ({
  state,
  head: { ref, sha, repo: repo === null ? null : { full_name: repo } },
});

const decide = (branches, pulls) =>
  staleHeadBranches({ branches, pullsByBranch: new Map(Object.entries(pulls)), repo: REPO });

test("deletes the head branch of a closed pull request", () => {
  assert.deepEqual(decide([branch("fix-counts")], { "fix-counts": [pr("fix-counts")] }), [
    "fix-counts",
  ]);
});

test("keeps a branch while any of its pull requests is open", () => {
  const pulls = { topic: [pr("topic"), pr("topic", { state: "open" })] };
  assert.deepEqual(decide([branch("topic")], pulls), []);
});

test("keeps a branch that never had a pull request", () => {
  assert.deepEqual(decide([branch("wip")], { wip: [] }), []);
  assert.deepEqual(decide([branch("unlooked")], {}), []);
});

test("keeps a branch pushed to after its pull request closed", () => {
  const pulls = { topic: [pr("topic", { sha: "b".repeat(40) })] };
  assert.deepEqual(decide([branch("topic")], pulls), []);
});

test("ignores pull requests from a fork with the same branch name", () => {
  const pulls = { topic: [pr("topic", { repo: "someone/magpie-site" }), pr("topic", { repo: null })] };
  assert.deepEqual(decide([branch("topic")], pulls), []);
});

test("never deletes protected, serving, preview or ASF branches", () => {
  const names = ["main", "publish", "preview/pr9-staging", "asf-staging", "guarded"];
  const branches = names.map((n) => branch(n, { protectedBranch: n === "guarded" }));
  const pulls = Object.fromEntries(names.map((n) => [n, [pr(n)]]));
  assert.deepEqual(decide(branches, pulls), []);
});
