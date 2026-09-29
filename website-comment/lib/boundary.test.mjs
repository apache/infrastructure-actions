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
 * The privilege boundary of the preview publisher, checked deterministically.
 *
 * The publisher holds a write token. It may learn exactly these things about a
 * pull request: its number, its label names, its head SHA, its author's login
 * and type, and a comment's body only when that is the exact /show-preview
 * command or bot-authored — plus the built site, as the unprivileged build's
 * artifact. Never its code, diff, title, description, branch name or commit
 * messages, and never human discussion.
 *
 * Three checks:
 *   1. the publish action: no pull_request_target, no checkout, and no event
 *      data in any step beyond what the event gate reads;
 *   2. a full publisher run against GitHub responses in which every forbidden
 *      field carries a BAIT marker: no value the client hands the publisher,
 *      and nothing the publisher writes, may contain it;
 *   3. the API endpoints that run touches are all on a fixed allowlist.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createClient, projectPull, projectComment } from "./github.mjs";
import { run } from "./publish.mjs";

process.env.PREVIEW_SITE_NAME = "magpie";

const REPO = "apache/magpie-site";
const BAIT = "BAIT";
const SHA = "d".repeat(40);

// ---------------------------------------------------------------------------
// 1. The publish action

const action = async (name) =>
  (await readFile(new URL(`../${name}/action.yml`, import.meta.url), "utf8"))
    .split("\n")
    // Drop comment lines and trailing comments; the prose names the forbidden
    // things on purpose.
    .map((line) => line.replace(/(^|\s)#.*$/, ""))
    .join("\n");

test("preview-publish runs only on signals that run default-branch code", async () => {
  const text = await action("preview-publish");
  assert.doesNotMatch(text, /pull_request_target/);
  // The gate's case arms: exactly these events run, anything else fails.
  const arms = [...text.matchAll(/^ {10}([a-z_]+)\)/gm)].map((m) => m[1]).sort();
  assert.deepEqual(arms, ["issue_comment", "workflow_dispatch", "workflow_run"]);
});

test("preview-publish never checks out or fetches a pull request's code", async () => {
  const text = await action("preview-publish");
  assert.doesNotMatch(text, /actions\/checkout/, "the publisher needs no checkout");
  assert.doesNotMatch(text, /download-artifact/, "the artifact is fetched and screened by artifact.mjs only");
  assert.doesNotMatch(text, /\bgit\s+(fetch|checkout|clone|pull)\b/, "no git fetch of pull request refs");
});

test("no step in the publisher receives event data beyond the gate's checks", async () => {
  const text = await action("preview-publish");
  const contexts = new Set([...text.matchAll(/\bgithub\.[A-Za-z_.*]+/g)].map((m) => m[0]));
  assert.deepEqual([...contexts].sort(), [
    "github.action_path",
    "github.event.issue.pull_request",
    "github.event.workflow_run.event",
    "github.event_name",
    "github.token",
  ]);
});

// ---------------------------------------------------------------------------
// 2 and 3. A full run against baited GitHub responses

const pullPayload = (number, labels = []) => ({
  number,
  state: "open",
  title: `${BAIT} title`,
  body: `${BAIT} description`,
  labels: labels.map((name) => ({ name, description: `${BAIT} label description` })),
  head: {
    sha: SHA,
    ref: `${BAIT}-branch`,
    label: `fork:${BAIT}-branch`,
    repo: { full_name: `${BAIT}/fork`, description: `${BAIT} repo` },
  },
  base: { ref: "main", sha: "e".repeat(40) },
  user: { login: "contributor", type: "User", bio: BAIT },
  merge_commit_sha: "f".repeat(40),
  diff_url: `https://example.invalid/${BAIT}.diff`,
});

const human = (id, body) => ({ id, body, user: { login: "drive-by", type: "User" } });

function baitedGitHub() {
  const routes = [
    [/^GET \/repos\/R\/pulls\?state=open&per_page=100&page=1$/, () => [pullPayload(5, ["preview"]), pullPayload(6)]],
    [/^GET \/repos\/R\/pulls\?state=open&per_page=100&page=\d+$/, () => []],
    [/^GET \/repos\/R\/issues\/5\/comments\?per_page=100&page=1$/, () => [
      human(1, `${BAIT} discussion`),
      { id: 2, body: "/show-preview", user: { login: "maintainer", type: "User" } },
    ]],
    [/^GET \/repos\/R\/issues\/6\/comments\?per_page=100&page=1$/, () => [human(3, `${BAIT} more`)]],
    [/^GET \/repos\/R\/issues\/\d+\/comments\?per_page=100&page=\d+$/, () => []],
    [/^GET \/repos\/R\/collaborators\/[^/]+\/permission$/, (url) => ({
      permission: url.includes("/maintainer/") ? "write" : "read",
      user: { bio: BAIT },
    })],
    [/^GET \/repos\/R\/issues\/comments\/\d+\/reactions\?content=rocket&per_page=100&page=\d+$/, () => []],
    [/^GET \/repos\/R\/issues\/\d+\/events\?per_page=100&page=1$/, () => [
      { event: "labeled", label: { name: "preview" }, actor: { login: "maintainer" }, commit_id: BAIT },
      { event: "renamed", rename: { from: BAIT, to: BAIT } },
    ]],
    [/^GET \/repos\/R\/issues\/\d+\/events\?per_page=100&page=\d+$/, () => []],
    [/^GET \/repos\/R\/labels\/preview$/, () => ({ name: "preview" })],
    [/^GET \/repos\/R\/git\/matching-refs\/heads\/preview\/\?per_page=100&page=\d+$/, () => []],
    [/^GET \/repos\/R\/actions\/workflows\/build\.yml\/runs\?head_sha=[0-9a-f]{40}&status=success&per_page=1$/, () => ({
      workflow_runs: [{
        id: 77,
        head_branch: `${BAIT}-branch`,
        display_title: `${BAIT} title`,
        head_commit: { message: `${BAIT} commit` },
      }],
    })],
    [/^GET \/repos\/R\/branches\?per_page=100&page=1$/, () => [
      { name: "main", protected: true, commit: { sha: "1".repeat(40) } },
    ]],
    [/^GET \/repos\/R\/branches\?per_page=100&page=\d+$/, () => []],
    [/^POST \/repos\/R\/issues\/\d+\/comments$/, () => ({ id: 900 })],
    [/^PATCH \/repos\/R\/issues\/comments\/\d+$/, () => ({})],
    [/^POST \/repos\/R\/issues\/\d+\/labels$/, () => []],
    [/^POST \/repos\/R\/issues\/comments\/\d+\/reactions$/, () => ({})],
  ];

  const seen = [];
  const writes = [];
  const fetchImpl = async (url, options = {}) => {
    const method = options.method ?? "GET";
    const key = `${method} ${url.replace("https://api.github.com", "").replace(`/repos/${REPO}`, "/repos/R")}`;
    seen.push(key);
    if (method !== "GET") writes.push(options.body ?? "");
    const route = routes.find(([re]) => re.test(key));
    if (!route) throw new Error(`endpoint not on the allowlist: ${key}`);
    const value = route[1](key);
    return { ok: true, status: 200, json: async () => value };
  };
  return { fetchImpl, seen, writes, allow: routes.map(([re]) => re) };
}

/** Fails the moment any client method hands the publisher a BAIT value. */
function guarded(client) {
  return new Proxy(client, {
    get(target, prop) {
      const value = target[prop];
      if (typeof value !== "function") return value;
      return async (...args) => {
        const result = await value.apply(target, args);
        const seen = JSON.stringify(result ?? null);
        assert.ok(!seen.includes(BAIT), `gh.${String(prop)} leaked pull request data: ${seen}`);
        return result;
      };
    },
  });
}

test("a full run sees and writes no pull request data beyond the allowlist", async () => {
  const gh = baitedGitHub();
  const client = guarded(createClient({ repo: REPO, token: "t", fetchImpl: gh.fetchImpl, workflow: "build.yml" }));

  const dir = await mkdtemp(join(tmpdir(), "boundary-"));
  await writeFile(join(dir, "index.html"), "<body>site</body>");
  const pushed = [];

  const logs = [];
  const log = console.log;
  const error = console.error;
  console.log = (...a) => logs.push(a.join(" "));
  console.error = (...a) => logs.push(a.join(" "));
  try {
    await run({
      gh: client,
      git: { pushTree: async (branch, files, message) => pushed.push({ branch, files, message }) },
      repo: REPO,
      fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA, anchors: {} } }),
    });
  } finally {
    console.log = log;
    console.error = error;
  }

  assert.equal(pushed.length, 1, "the armed PR must still publish");
  const out = JSON.stringify({ pushed: pushed.map((p) => [p.branch, p.message, Object.keys(p.files)]), writes: gh.writes, logs });
  assert.ok(!out.includes(BAIT), `the publisher wrote pull request data: ${out}`);
  assert.equal(process.exitCode ?? 0, 0, logs.join("\n"));

  // 3. Every request was on the allowlist (unlisted ones throw above), and
  // none of the endpoints that return PR code or prose was touched.
  for (const key of gh.seen) {
    assert.doesNotMatch(key, /\/pulls\/\d+(\/|$)|\/contents\/|\/git\/(blobs|trees|commits)|\/compare\/|\/issues\/\d+$/, key);
  }
});

test("projectPull keeps exactly the allowed fields", () => {
  assert.deepEqual(projectPull(pullPayload(5, ["preview"])), {
    number: 5,
    state: "open",
    labels: [{ name: "preview" }],
    head: { sha: SHA },
    user: { login: "contributor", type: "User" },
  });
});

test("projectComment drops human discussion and keeps only commands and bot text", () => {
  assert.equal(projectComment(human(1, `${BAIT} discussion`)).body, null);
  assert.equal(projectComment(human(1, "/show-preview")).body, "/show-preview");
  assert.equal(
    projectComment({ id: 2, body: "status <!-- m -->", user: { login: "github-actions[bot]", type: "Bot" } }).body,
    "status <!-- m -->",
  );
});
