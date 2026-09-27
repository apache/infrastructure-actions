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
import { createClient, buildWorkflow } from "./github.mjs";

function fakeFetch(routes) {
  const calls = [];
  const impl = async (url, options = {}) => {
    calls.push({ url, method: options.method ?? "GET", body: options.body, headers: options.headers });
    const key = `${options.method ?? "GET"} ${url.replace("https://api.github.com", "")}`;
    if (!(key in routes)) throw new Error(`unexpected request: ${key}`);
    const value = routes[key];
    return { ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value) };
  };
  return { impl, calls };
}

test("hasWriteAccess is true for write and admin", async () => {
  const { impl } = fakeFetch({
    "GET /repos/apache/magpie-site/collaborators/alice/permission": { permission: "write" },
    "GET /repos/apache/magpie-site/collaborators/bob/permission": { permission: "admin" },
    "GET /repos/apache/magpie-site/collaborators/carol/permission": { permission: "read" },
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });

  assert.equal(await gh.hasWriteAccess("alice"), true);
  assert.equal(await gh.hasWriteAccess("bob"), true);
  assert.equal(await gh.hasWriteAccess("carol"), false);
});

test("hasWriteAccess is false when the lookup 404s", async () => {
  const impl = async () => ({ ok: false, status: 404, json: async () => ({}), text: async () => "" });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });
  assert.equal(await gh.hasWriteAccess("stranger"), false);
});

test("sends the token and the api version header", async () => {
  const { impl, calls } = fakeFetch({
    "GET /repos/apache/magpie-site/pulls/1": { number: 1 },
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "secret", fetchImpl: impl });
  await gh.getPull(1);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].headers.authorization, "Bearer secret");
  assert.equal(calls[0].headers["x-github-api-version"], "2022-11-28");
});

test("listPreviewBranches returns only preview refs", async () => {
  const { impl } = fakeFetch({
    "GET /repos/apache/magpie-site/git/matching-refs/heads/preview/?per_page=100&page=1": [
      { ref: "refs/heads/preview/pr1-staging" },
      { ref: "refs/heads/preview/pr2-staging" },
    ],
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });
  assert.deepEqual(await gh.listPreviewBranches(), ["preview/pr1-staging", "preview/pr2-staging"]);
});

test("listComments follows pagination past the first page", async () => {
  const page1 = Array.from({ length: 100 }, (_, i) => ({ id: i, body: "x" }));
  const { impl } = fakeFetch({
    "GET /repos/apache/magpie-site/issues/7/comments?per_page=100&page=1": page1,
    "GET /repos/apache/magpie-site/issues/7/comments?per_page=100&page=2": [{ id: 100, body: "/show-preview" }],
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });

  const all = await gh.listComments(7);
  assert.equal(all.length, 101);
  assert.equal(all.at(-1).body, "/show-preview");
});

test("upsertComment never edits a human comment that contains the marker", async () => {
  const planted = { id: 1, user: { type: "User" }, body: "look: <!-- asf-preview-status -->" };
  const { impl, calls } = fakeFetch({
    "GET /repos/apache/magpie-site/issues/7/comments?per_page=100&page=1": [planted],
    "POST /repos/apache/magpie-site/issues/7/comments": { id: 2 },
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });

  await gh.upsertComment(7, "asf-preview-status", "status");

  assert.ok(
    calls.every((c) => c.method !== "PATCH"),
    "must not PATCH a comment authored by a human",
  );
});

test("branchHeadMessage returns the commit message for a found branch", async () => {
  const { impl } = fakeFetch({
    "GET /repos/apache/magpie-site/commits/preview%2Fpr9-staging": {
      commit: { message: "Retire preview for #9 [tombstone]" },
    },
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });

  assert.equal(
    await gh.branchHeadMessage("preview/pr9-staging"),
    "Retire preview for #9 [tombstone]",
  );
});

test("branchHeadMessage returns empty string for a branch that does not exist", async () => {
  const impl = async () => ({ ok: false, status: 404, json: async () => ({}), text: async () => "" });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });

  assert.equal(await gh.branchHeadMessage("preview/pr9-staging"), "");
});

test("upsertComment edits the bot's own marked comment in place", async () => {
  const mine = { id: 9, user: { type: "Bot" }, body: "old\n\n<!-- asf-preview-status -->" };
  const { impl, calls } = fakeFetch({
    "GET /repos/apache/magpie-site/issues/7/comments?per_page=100&page=1": [mine],
    "PATCH /repos/apache/magpie-site/issues/comments/9": { id: 9 },
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });

  await gh.upsertComment(7, "asf-preview-status", "new status");

  const patch = calls.find((c) => c.method === "PATCH");
  assert.ok(patch, "expected a PATCH to the existing bot comment");
  assert.match(JSON.parse(patch.body).body, /new status/);
});

test("deleteBranch accepts GitHub's empty 204 response", async () => {
  const impl = async () => ({
    ok: true,
    status: 204,
    json: async () => {
      throw new SyntaxError("Unexpected end of JSON input");
    },
    text: async () => "",
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });
  assert.equal(await gh.deleteBranch("fix-counts"), null);
});

test("listPullsForHead asks for every state, qualified by the repository owner", async () => {
  const { impl, calls } = fakeFetch({
    "GET /repos/apache/magpie-site/pulls?state=all&head=apache%3Afix%2Fx&per_page=100&page=1": [],
  });
  const gh = createClient({ repo: "apache/magpie-site", token: "t", fetchImpl: impl });
  assert.deepEqual(await gh.listPullsForHead("fix/x"), []);
  assert.equal(calls.length, 1);
});

test("buildWorkflow defaults to build.yml and accepts another file name", () => {
  assert.equal(buildWorkflow({}), "build.yml");
  assert.equal(buildWorkflow({ PREVIEW_BUILD_WORKFLOW: "jekyll.yaml" }), "jekyll.yaml");
});

test("buildWorkflow refuses paths and non-workflow names", () => {
  for (const bad of ["../x.yml", "a/b.yml", "build", ".yml", "x.yml?y=1"]) {
    assert.throws(() => buildWorkflow({ PREVIEW_BUILD_WORKFLOW: bad }), /workflow file name/);
  }
});

test("latestSuccessfulBuild queries the configured workflow", async () => {
  const { impl, calls } = fakeFetch({
    "GET /repos/apache/x-site/actions/workflows/jekyll.yml/runs?head_sha=abc&status=success&per_page=1":
      { workflow_runs: [{ id: 7 }] },
  });
  const gh = createClient({ repo: "apache/x-site", token: "t", fetchImpl: impl, workflow: "jekyll.yml" });
  assert.deepEqual(await gh.latestSuccessfulBuild("abc"), { id: 7 });
  assert.equal(calls.length, 1);
});
