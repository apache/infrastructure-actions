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
import { mkdtemp, writeFile, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { run, publishedShaFrom, isBotAuthored } from "./publish.mjs";

// Every URL below is for a site staged as magpie-pr<N>.staged.apache.org.
process.env.PREVIEW_SITE_NAME = "magpie";

const SHA = "c".repeat(40);
const pull = (number, sha = SHA, labels = []) => ({
  number,
  head: { sha },
  user: { type: "User", login: "contributor" },
  labels: labels.map((name) => ({ name })),
});
const labeledBy = (login) => ({ event: "labeled", label: { name: "preview" }, actor: { login } });
const botPull = (number, login = "dependabot[bot]", type = "Bot") => ({
  number,
  head: { sha: SHA },
  user: { type, login },
});

async function artifactDir({ withSymlink = false } = {}) {
  const dir = await mkdtemp(join(tmpdir(), "preview-art-"));
  await writeFile(join(dir, "index.html"), "<h1>site</h1>");
  if (withSymlink) await symlink("/etc/passwd", join(dir, "leak"));
  return dir;
}

const bot = (body, login = "github-actions[bot]") => ({ user: { type: "Bot", login }, body });
const human = (body, login = "drive-by") => ({ user: { type: "User", login }, body });
let nextId = 1;
const armCmd = (login = "maintainer") => ({ id: nextId++, user: { type: "User", login }, body: "/show-preview" });

function fakes({
  openPulls = [],
  comments = {},
  branches = [],
  headMessages = {},
  hasBuild = true,
  throwFor = [],
  repoBranches = [],
  pullsForHead = {},
  labelEvents = {},
  acknowledged = [],
} = {}) {
  const pushed = [];
  const deleted = [];
  const posted = [];
  const created = [];
  const labelled = [];
  const reacted = [];

  const gh = {
    listOpenPulls: async () => openPulls,
    listComments: async (n) => {
      if (throwFor.includes(n)) throw new Error("transient failure");
      return comments[n] ?? [];
    },
    hasWriteAccess: async (login) => login === "maintainer",
    upsertComment: async (n, marker, body) => {
      posted.push({ n, marker, body });
    },
    createComment: async (n, body) => {
      created.push({ n, body });
    },
    ensureLabel: async () => {},
    addLabel: async (n, name) => {
      labelled.push({ n, name });
    },
    listLabelEvents: async (n) => labelEvents[n] ?? [],
    hasReaction: async (id) => acknowledged.includes(id) || reacted.includes(id),
    addReaction: async (id) => {
      reacted.push(id);
    },
    listPreviewBranches: async () => branches,
    deleteBranch: async (name) => {
      deleted.push(name);
    },
    latestSuccessfulBuild: async () => (hasBuild ? { id: 1 } : null),
    branchHeadMessage: async (branch) => headMessages[branch] ?? "",
    listBranches: async () => repoBranches,
    listPullsForHead: async (name) => pullsForHead[name] ?? [],
  };

  const git = {
    pushTree: async (branch, files, message, contentDir) => {
      pushed.push({ branch, files, message, contentDir });
    },
  };

  return { gh, git, pushed, deleted, posted, created, labelled, reacted };
}

const go = (f, extra = {}) =>
  run({ gh: f.gh, git: f.git, repo: "apache/magpie-site", fetchArtifact: async () => null, ...extra });

test("announces on an open PR that has not been told about previews", async () => {
  const f = fakes({ openPulls: [pull(5)] });
  await go(f);

  const announce = f.posted.find((p) => p.marker === "asf-preview-howto");
  assert.ok(announce, "expected an explainer comment");
  assert.match(announce.body, /`preview` label/);
  assert.doesNotMatch(announce.body, /staged\.apache\.org/,
    "an unarmed PR has no preview; its link is posted only once published");
});

test("isBotAuthored reads the type, and falls back to the login suffix", () => {
  assert.equal(isBotAuthored({ user: { type: "Bot", login: "dependabot[bot]" } }), true);
  assert.equal(isBotAuthored({ user: { type: "User", login: "renovate[bot]" } }), true);
  assert.equal(isBotAuthored({ user: { type: "User", login: "contributor" } }), false);
  assert.equal(isBotAuthored({}), false);
  assert.equal(isBotAuthored(null), false);
});

test("does not announce on a bot-authored pull request", async () => {
  const f = fakes({ openPulls: [botPull(5)] });
  await go(f);

  assert.equal(
    f.posted.filter((p) => p.marker === "asf-preview-howto").length,
    0,
    "a dependency bot opens many PRs and reads none of them",
  );
});

test("still publishes a bot's PR when a maintainer arms it", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [botPull(5)],
    comments: { 5: [armCmd()] },
  });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.equal(f.pushed.length, 1, "asking for a preview of a bot PR must still work");
  assert.equal(f.pushed[0].branch, "preview/pr5-staging");
});

test("does not announce twice when its own explainer is already there", async () => {
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [bot("hi\n\n<!-- asf-preview-howto -->")] },
  });
  await go(f);

  assert.equal(f.posted.filter((p) => p.marker === "asf-preview-howto").length, 0);
});

test("still announces when a human plants the explainer marker", async () => {
  // A substring test on any author would let anyone suppress the explainer.
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [human("see <!-- asf-preview-howto -->")] },
  });
  await go(f);

  assert.equal(f.posted.filter((p) => p.marker === "asf-preview-howto").length, 1);
});

test("publishes an armed open PR", async () => {
  const dir = await artifactDir();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.equal(f.pushed.length, 1);
  assert.equal(f.pushed[0].branch, "preview/pr5-staging");
  assert.equal(f.pushed[0].contentDir, dir, "the artifact's content must be published");
  assert.match(f.pushed[0].files[".asf.yaml"], /profile: pr5/);
  assert.match(f.pushed[0].files["robots.txt"], /Disallow: \//);
  assert.ok(f.posted.some((p) => p.marker === "asf-preview-status" && /published/i.test(p.body)));
});

test("announces each publish with a new comment, not only an edit", async () => {
  const dir = await artifactDir();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.equal(f.created.length, 1);
  assert.equal(f.created[0].n, 5);
  assert.match(f.created[0].body, new RegExp(SHA.slice(0, 7)));
  assert.match(f.created[0].body, /magpie-pr5\.staged\.apache\.org/);
});

test("does not announce when nothing was published", async () => {
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, hasBuild: false });
  await go(f);
  assert.equal(f.created.length, 0);
});

test("a maintainer's command adds the label and is acknowledged", async () => {
  const dir = await artifactDir();
  const cmd = armCmd();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [cmd] } });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.deepEqual(f.labelled, [{ n: 5, name: "preview" }]);
  assert.deepEqual(f.reacted, [cmd.id]);
  assert.equal(f.pushed.length, 1);
});

test("an acknowledged command does not re-add a removed label", async () => {
  const cmd = armCmd();
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [cmd] },
    acknowledged: [cmd.id],
    branches: ["preview/pr5-staging"],
  });

  await go(f);

  assert.deepEqual(f.labelled, [], "removing the label must disarm for good");
  assert.ok(f.pushed.some((p) => /tombstone/.test(p.message)), "the preview must be retired");
});

test("a drive-by's command neither labels nor publishes", async () => {
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd("drive-by")] } });
  await go(f);

  assert.deepEqual(f.labelled, []);
  assert.deepEqual(f.reacted, []);
  assert.equal(f.pushed.length, 0);
});

test("publishes a PR labelled by a maintainer", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    labelEvents: { 5: [labeledBy("maintainer")] },
  });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.equal(f.pushed.length, 1);
  assert.deepEqual(f.labelled, [], "the label is already there");
});

test("does not publish a PR labelled by someone without write access", async () => {
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    labelEvents: { 5: [labeledBy("triager")] },
  });
  let fetched = 0;

  await go(f, { fetchArtifact: async () => { fetched += 1; return null; } });

  assert.equal(fetched, 0);
  assert.equal(f.pushed.length, 0);
});

test("publishedShaFrom reads the published commit, and nothing else", () => {
  assert.equal(publishedShaFrom("Publish preview for #180 (14fdc13)"), "14fdc13");
  assert.equal(publishedShaFrom("Retire preview for #9 [tombstone]"), null);
  assert.equal(publishedShaFrom("Publish preview for #180 (14fdc13)\n\nGenerated-by: x"), "14fdc13");
  assert.equal(publishedShaFrom(""), null);
  assert.equal(publishedShaFrom(null), null);
});

test("does not republish a preview that already matches the PR head", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [armCmd()] },
    branches: ["preview/pr5-staging"],
    headMessages: { "preview/pr5-staging": `Publish preview for #5 (${SHA.slice(0, 7)})` },
  });
  let fetched = 0;

  await go(f, {
    fetchArtifact: async () => {
      fetched += 1;
      return { dir, meta: { pr: 5, headSha: SHA } };
    },
  });

  assert.equal(fetched, 0, "an unchanged preview must not download its artifact again");
  assert.equal(f.pushed.length, 0, "an unchanged preview must not be force-pushed again");
  assert.equal(
    f.posted.filter((p) => p.marker === "asf-preview-status").length,
    0,
    "an unchanged preview must not rewrite its status comment",
  );
});

test("republishes when the PR head has moved on", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [armCmd()] },
    branches: ["preview/pr5-staging"],
    headMessages: { "preview/pr5-staging": "Publish preview for #5 (deadbee)" },
  });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.equal(f.pushed.length, 1, "a moved head must publish");
  assert.equal(f.pushed[0].branch, "preview/pr5-staging");
});

test("a manual dispatch republishes even when nothing changed", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [pull(5)],
    branches: ["preview/pr5-staging"],
    headMessages: { "preview/pr5-staging": `Publish preview for #5 (${SHA.slice(0, 7)})` },
  });

  await go(f, {
    only: 5,
    dispatchedBy: "maintainer",
    fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }),
  });

  assert.equal(f.pushed.length, 1, "asking for it explicitly must republish");
});

test("does not publish an unarmed PR", async () => {
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [human("/show-preview")] } });
  let fetched = 0;

  await go(f, {
    fetchArtifact: async () => {
      fetched += 1;
      return null;
    },
  });

  assert.equal(fetched, 0, "an unarmed PR must never have its artifact fetched");
  assert.equal(f.pushed.length, 0);
  assert.equal(
    f.posted.filter((p) => p.marker === "asf-preview-status").length,
    0,
    "an unarmed PR must not get a status comment",
  );
});

test("refuses an artifact whose metadata claims another PR", async () => {
  const dir = await artifactDir();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 6, headSha: SHA } }) });

  assert.equal(f.pushed.length, 0);
  assert.match(f.posted.at(-1).body, /could not be published/i);
});

test("refuses to publish when the unsafe-entry screen cannot run", async () => {
  // Fail closed: an error in the screen must never read as "nothing found".
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });

  await go(f, {
    fetchArtifact: async () => ({ dir: "/nonexistent-preview-dir", meta: { pr: 5, headSha: SHA } }),
  });

  assert.equal(f.pushed.length, 0, "must not publish an artifact it could not screen");
  assert.match(f.posted.at(-1).body, /could not screen/i);
});

test("refuses an artifact containing a symlink", async () => {
  const dir = await artifactDir({ withSymlink: true });
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.equal(f.pushed.length, 0);
  assert.match(f.posted.at(-1).body, /unsafe entries/i);
});

test("says it is waiting when there is no successful build", async () => {
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, hasBuild: false });
  await go(f);

  assert.equal(f.pushed.length, 0);
  assert.match(f.posted.at(-1).body, /waiting on a build/i);
});

test("tombstones a closed PR's preview and corrects its status comment", async () => {
  const f = fakes({ openPulls: [], branches: ["preview/pr9-staging"] });
  await go(f);

  assert.equal(f.pushed.length, 1);
  assert.match(f.pushed[0].files["index.html"], /retired/i);
  assert.match(f.pushed[0].message, /\[tombstone\]/);
  assert.equal(f.deleted.length, 0, "delete waits for a later run");
  assert.ok(
    f.posted.some((p) => p.n === 9 && /retired/i.test(p.body)),
    "the status comment must stop claiming the preview is live",
  );
});

test("deletes a branch that already carries a tombstone", async () => {
  const f = fakes({
    openPulls: [],
    branches: ["preview/pr9-staging"],
    headMessages: { "preview/pr9-staging": "Retire preview for #9 [tombstone]" },
  });
  await go(f);

  assert.deepEqual(f.deleted, ["preview/pr9-staging"]);
});

test("a manual dispatch arms the PR with the label and says who", async () => {
  const dir = await artifactDir();
  const f = fakes({ openPulls: [pull(42)] });

  await go(f, {
    only: 42,
    dispatchedBy: "maintainer",
    fetchArtifact: async () => ({ dir, meta: { pr: 42, headSha: SHA } }),
  });

  assert.deepEqual(f.labelled, [{ n: 42, name: "preview" }],
    "a dispatch must arm durably so the next run does not reap it");
  assert.ok(f.created.some((c) => c.n === 42 && /@maintainer/.test(c.body)));
  assert.equal(f.pushed.length, 1);
});

test("a PR the publisher labelled survives a later run", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [pull(42, SHA, ["preview"])],
    labelEvents: { 42: [labeledBy("github-actions[bot]")] },
    branches: ["preview/pr42-staging"],
  });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 42, headSha: SHA } }) });

  assert.equal(f.pushed.filter((p) => /tombstone/.test(p.message)).length, 0,
    "a dispatched preview must not be reaped on the next run");
  assert.deepEqual(f.deleted, []);
});

test("one PR's failure does not abort the reap", async () => {
  const f = fakes({
    openPulls: [pull(5)],
    throwFor: [5],
    branches: ["preview/pr9-staging"],
  });

  await go(f);

  assert.ok(
    f.pushed.some((p) => /tombstone/.test(p.message)),
    "a transient failure on one PR must not block teardown of unrelated previews",
  );
});

test("a branch whose head cannot be read is tombstoned, never deleted", async () => {
  const f = fakes({ openPulls: [], branches: ["preview/pr9-staging"] });
  f.gh.branchHeadMessage = async () => {
    throw new Error("unreadable");
  };

  await go(f);

  assert.ok(
    f.pushed.some((p) => /tombstone/.test(p.message)),
    "an unreadable head must still be tombstoned",
  );
  assert.deepEqual(f.deleted, [], "must not delete a branch it could not inspect");
});

test("a label added by a different bot does not arm the PR", async () => {
  // Only the publisher's own login is trusted: any GitHub App installed on the
  // repository can add labels.
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    labelEvents: { 5: [labeledBy("other-app[bot]")] },
  });
  let fetched = 0;

  await go(f, { fetchArtifact: async () => { fetched += 1; return null; } });

  assert.equal(fetched, 0);
  assert.equal(f.pushed.length, 0);
});

test("a run with a persistently failing operation reports failure via the exit code", async () => {
  // The publisher runs unattended; catching and logging every
  // error without ever failing the process lets a permanently broken
  // publisher report success forever.
  const f = fakes({ openPulls: [pull(5)], throwFor: [5] });

  process.exitCode = 0;
  await go(f);

  assert.equal(process.exitCode, 1, "a run with a failed operation must set a nonzero exit code");
  process.exitCode = 0;
});

test("injectOverlay adds the scripts once, before </body>", async () => {
  const { injectOverlay } = await import("./publish.mjs");
  const html = "<html><body><h1>x</h1></body></html>";
  const once = injectOverlay(html);

  assert.match(once, /_preview\/review\.js/);
  assert.match(once, /_preview\/html2canvas-pro\.min\.js/);
  assert.ok(once.indexOf("</body>") > once.indexOf("review.js"), "scripts come before </body>");
  assert.equal(injectOverlay(once), once, "injecting twice changes nothing");
});

test("injectOverlay leaves a document with no body alone", async () => {
  const { injectOverlay } = await import("./publish.mjs");
  assert.equal(injectOverlay("no body here"), "no body here");
});

test("injectOverlay injects at the last </body> and only once", async () => {
  const { injectOverlay } = await import("./publish.mjs");

  // An inline script containing </body> must not be the injection point.
  const html = '<html><body><script>var s = "</body>";</script></body></html>';
  const once = injectOverlay(html);
  assert.ok(
    once.lastIndexOf("_preview/review.js") > once.indexOf('var s = "</body>"'),
    "must inject after the string literal, not inside the script",
  );
  assert.equal(injectOverlay(once), once, "injecting twice changes nothing");

  // A page that merely mentions the script path still gets the overlay.
  const mentions = "<html><body>see /_preview/review.js for details</body></html>";
  assert.match(injectOverlay(mentions), /asf-preview-overlay/);
});

test("deletes this repository's head branch once its pull request has closed", async () => {
  const closed = { state: "closed", head: { ref: "fix-counts", sha: SHA, repo: { full_name: "apache/magpie-site" } } };
  const f = fakes({
    repoBranches: [
      { name: "main", protected: true, commit: { sha: SHA } },
      { name: "fix-counts", protected: false, commit: { sha: SHA } },
      { name: "wip", protected: false, commit: { sha: SHA } },
    ],
    pullsForHead: { "fix-counts": [closed] },
  });
  await go(f, { reapHeads: true });

  assert.deepEqual(f.deleted, ["fix-counts"]);
});

test("every status comment leads with the preview URL", async () => {
  const url = /\*\*Preview:\*\* https:\/\/magpie-pr5\.staged\.apache\.org\//;

  const waiting = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, hasBuild: false });
  await go(waiting);
  assert.match(waiting.posted.at(-1).body, url);

  const dir = await artifactDir({ withSymlink: true });
  const refused = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  await go(refused, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });
  assert.match(refused.posted.at(-1).body, url);

  const ok = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  const okDir = await artifactDir();
  await go(ok, { fetchArtifact: async () => ({ dir: okDir, meta: { pr: 5, headSha: SHA } }) });
  assert.match(ok.posted.at(-1).body, url);
  assert.match(ok.created.at(-1).body, url);
});

test("leaves head branches alone unless reaping them is switched on", async () => {
  const closed = { state: "closed", head: { ref: "fix-counts", sha: SHA, repo: { full_name: "apache/magpie-site" } } };
  const f = fakes({
    repoBranches: [{ name: "fix-counts", protected: false, commit: { sha: SHA } }],
    pullsForHead: { "fix-counts": [closed] },
  });
  await go(f);

  assert.deepEqual(f.deleted, []);
});
