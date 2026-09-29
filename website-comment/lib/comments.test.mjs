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
import { isShowPreviewComment } from "./comments.mjs";

test("matches the bare command", () => {
  assert.equal(isShowPreviewComment("/show-preview"), true);
});

test("tolerates surrounding whitespace and a trailing newline", () => {
  assert.equal(isShowPreviewComment("  /show-preview  \n"), true);
});

test("rejects the command quoted inside a sentence", () => {
  assert.equal(isShowPreviewComment("you can run /show-preview here"), false);
  assert.equal(isShowPreviewComment("`/show-preview`"), false);
  assert.equal(isShowPreviewComment("> /show-preview"), false);
});

test("rejects a command with trailing arguments", () => {
  assert.equal(isShowPreviewComment("/show-preview now"), false);
});

test("rejects multi-line bodies that merely contain it", () => {
  assert.equal(isShowPreviewComment("please:\n/show-preview"), false);
});

test("rejects empty and non-string input", () => {
  assert.equal(isShowPreviewComment(""), false);
  assert.equal(isShowPreviewComment(undefined), false);
  assert.equal(isShowPreviewComment(null), false);
});
