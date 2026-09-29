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
import { mkdtemp, mkdir, writeFile, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { validateMeta, findUnsafeEntries } from "./validate.mjs";

const expected = { number: 42, headSha: "a".repeat(40) };

test("accepts metadata matching the PR", () => {
  const meta = { pr: 42, headSha: "a".repeat(40) };
  assert.deepEqual(validateMeta(meta, expected), { ok: true });
});

test("accepts a PR number given as a digit string", () => {
  const meta = { pr: "42", headSha: "a".repeat(40) };
  assert.deepEqual(validateMeta(meta, expected), { ok: true });
});

test("rejects a PR number that is not digits", () => {
  const meta = { pr: "42; rm -rf /", headSha: "a".repeat(40) };
  assert.equal(validateMeta(meta, expected).ok, false);
});

test("rejects a PR number claiming a different PR", () => {
  const meta = { pr: 7, headSha: "a".repeat(40) };
  const result = validateMeta(meta, expected);
  assert.equal(result.ok, false);
  assert.match(result.reason, /pr/i);
});

test("rejects a head SHA that is not the PR's current head", () => {
  const meta = { pr: 42, headSha: "b".repeat(40) };
  const result = validateMeta(meta, expected);
  assert.equal(result.ok, false);
  assert.match(result.reason, /sha/i);
});

test("rejects a malformed or missing SHA", () => {
  assert.equal(validateMeta({ pr: 42, headSha: "abc" }, expected).ok, false);
  assert.equal(validateMeta({ pr: 42 }, expected).ok, false);
  assert.equal(validateMeta(null, expected).ok, false);
});

test("finds symlinks in an extracted tree", async () => {
  const root = await mkdtemp(join(tmpdir(), "preview-"));
  await mkdir(join(root, "sub"), { recursive: true });
  await writeFile(join(root, "sub", "index.html"), "<h1>ok</h1>");
  await symlink("/etc/passwd", join(root, "sub", "leak"));

  const unsafe = await findUnsafeEntries(root);
  assert.deepEqual(unsafe, ["sub/leak"]);
});

test("flags a symlinked directory without walking into it", async () => {
  // A symlink nested inside the target: if the walk wrongly descends through
  // `escape`, this surfaces as "escape/nested-link" and the assertion fails.
  // A regular file here would prove nothing, since only symlinks are ever
  // reported.
  const outside = await mkdtemp(join(tmpdir(), "preview-outside-"));
  await symlink("/etc/passwd", join(outside, "nested-link"));

  const root = await mkdtemp(join(tmpdir(), "preview-"));
  await writeFile(join(root, "index.html"), "<h1>ok</h1>");
  await symlink(outside, join(root, "escape"));

  const unsafe = await findUnsafeEntries(root);
  assert.deepEqual(unsafe, ["escape"]);
});

test("reports nothing for a clean tree", async () => {
  const root = await mkdtemp(join(tmpdir(), "preview-"));
  await writeFile(join(root, "index.html"), "<h1>ok</h1>");
  assert.deepEqual(await findUnsafeEntries(root), []);
});
