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
import { diffAnchor, addedLineRanges, buildAnchors } from "./anchors.mjs";

test("diffAnchor is sha256 of the path, as GitHub computes it", () => {
  // Verified against https://github.com/apache/magpie-site/pull/186/files
  assert.equal(
    diffAnchor("scripts/preview/publish.mjs"),
    "diff-ce7de5b1b8a1930e34a20d50991cf9ec8592b07a535ea8389690a4eeeff5ac64",
  );
});

test("addedLineRanges reads the new-file line numbers a hunk touches", () => {
  const patch = [
    "@@ -1,3 +1,4 @@",
    " context",
    "+added one",
    "+added two",
    " context",
    "@@ -20,2 +21,2 @@",
    "-removed",
    "+changed",
  ].join("\n");

  assert.deepEqual(addedLineRanges(patch), [[2, 3], [21, 21]]);
});

test("addedLineRanges is empty for a patch with no additions", () => {
  assert.deepEqual(addedLineRanges("@@ -1,2 +1,1 @@\n context\n-gone"), []);
  assert.deepEqual(addedLineRanges(""), []);
  assert.deepEqual(addedLineRanges(undefined), []);
});

test("buildAnchors keys by path and carries anchor plus ranges", () => {
  const built = buildAnchors([
    { filename: "a.tsx", patch: "@@ -1,1 +1,2 @@\n context\n+new" },
    { filename: "b.png" },
  ]);

  assert.equal(built["a.tsx"].anchor, diffAnchor("a.tsx"));
  assert.deepEqual(built["a.tsx"].ranges, [[2, 2]]);
  assert.deepEqual(built["b.png"].ranges, [], "a binary file has no line ranges");
});

test("deletions never advance the new-file line number", () => {
  const patch = [
    "@@ -10,6 +10,4 @@",
    " context",       // new line 10
    "-gone one",
    "-gone two",
    "-gone three",
    "+replacement",   // new line 11
    " context",       // new line 12
  ].join("\n");

  assert.deepEqual(
    addedLineRanges(patch),
    [[11, 11]],
    "three deletions must not push the replacement to line 14",
  );
});
