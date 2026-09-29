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
import { previewMeta } from "./write-meta.mjs";
import { diffAnchor } from "./anchors.mjs";

test("the build's metadata carries the PR, the head SHA and the anchor manifest", async () => {
  const meta = await previewMeta({
    pr: 5,
    headSha: "a".repeat(40),
    files: [{ filename: "src/x.astro", patch: "@@ -1,1 +1,2 @@\n a\n+b" }],
  });
  assert.deepEqual(meta, {
    pr: 5,
    headSha: "a".repeat(40),
    anchors: { "src/x.astro": { anchor: diffAnchor("src/x.astro"), ranges: [[2, 2]] } },
  });
});
