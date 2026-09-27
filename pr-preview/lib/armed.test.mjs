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
import { resolveArmed } from "./armed.mjs";

const comment = (body, login) => ({ body, user: { login } });
const writers = (...logins) => async (login) => logins.includes(login);

test("arms when a writer posts the command", async () => {
  const result = await resolveArmed({
    comments: [comment("/show-preview", "maintainer")],
    hasWriteAccess: writers("maintainer"),
  });
  assert.deepEqual(result, { armed: true, by: "maintainer" });
});

test("does not arm when a non-writer posts the command", async () => {
  const result = await resolveArmed({
    comments: [comment("/show-preview", "drive-by")],
    hasWriteAccess: writers("maintainer"),
  });
  assert.deepEqual(result, { armed: false, by: null });
});

test("reports the first writer who armed it", async () => {
  const result = await resolveArmed({
    comments: [
      comment("looks good", "maintainer"),
      comment("/show-preview", "second"),
      comment("/show-preview", "maintainer"),
    ],
    hasWriteAccess: writers("maintainer", "second"),
  });
  assert.equal(result.by, "second");
});

test("checks each author at most once", async () => {
  let calls = 0;
  const result = await resolveArmed({
    comments: [
      comment("/show-preview", "drive-by"),
      comment("/show-preview", "drive-by"),
      comment("/show-preview", "drive-by"),
    ],
    hasWriteAccess: async () => {
      calls += 1;
      return false;
    },
  });
  assert.equal(result.armed, false);
  assert.equal(calls, 1);
});

test("is unarmed with no comments", async () => {
  const result = await resolveArmed({ comments: [], hasWriteAccess: writers("x") });
  assert.deepEqual(result, { armed: false, by: null });
});
