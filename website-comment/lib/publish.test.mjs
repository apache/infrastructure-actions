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
let nextEventId = 1000;
const labeledBy = (login, id = nextEventId++) => ({ event: "labeled", label: { name: "preview" }, actor: { login }, id });
const unlabeledBy = (login, id = nextEventId++) => ({ event: "unlabeled", label: { name: "preview" }, actor: { login }, id });
const record = (id) => `<!-- asf-preview-armed event=${id} -->`;
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
  headDates = {},
  eventsLag = 0,
  publisher = "github-actions[bot]",
} = {}) {
  const lagging = {};
  const write = (n, event) => {
    const events = (labelEvents[n] ??= []);
    lagging[n] = { from: Math.min(lagging[n]?.from ?? events.length, events.length), reads: eventsLag };
    events.push(event);
  };
  const pushed = [];
  const deleted = [];
  const deletedAt = [];
  const posted = [];
  const created = [];
  const labelled = [];
  const unlabelled = [];
  const reacted = [];
  const reactionLogins = [];

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
    // GitHub records each write as a label event by the token's login, and the
    // events API shows writes from the first unseen one on only after
    // `eventsLag` further reads.
    addLabel: async (n, name) => {
      labelled.push({ n, name });
      write(n, labeledBy(publisher));
    },
    removeLabel: async (n, name) => {
      unlabelled.push({ n, name });
      write(n, unlabeledBy(publisher));
    },
    listLabelEvents: async (n) => {
      const events = labelEvents[n] ?? [];
      if (lagging[n]?.reads > 0) {
        lagging[n].reads -= 1;
        return events.slice(0, lagging[n].from);
      }
      delete lagging[n];
      return events;
    },
    hasReaction: async (id, content, login) => {
      reactionLogins.push(login);
      return acknowledged.includes(id) || reacted.includes(id);
    },
    addReaction: async (id) => {
      reacted.push(id);
    },
    listPreviewBranches: async () => branches,
    deleteBranch: async (name) => {
      deleted.push(name);
    },
    successfulBuilds: async () => (hasBuild === true ? [{ id: 1 }] : hasBuild || []),
    // Tombstones default to long ago, so they are past the grace period.
    branchHead: async (branch) => ({ message: headMessages[branch] ?? "", date: headDates[branch] ?? "2020-01-01T00:00:00Z" }),
    listBranches: async () => repoBranches,
    listPullsForHead: async (name) => pullsForHead[name] ?? [],
  };

  const git = {
    pushTree: async (branch, files, message, contentDir) => {
      pushed.push({ branch, files, message, contentDir });
    },
    deleteBranchAt: async (name, sha) => {
      deleted.push(name);
      deletedAt.push(sha);
    },
  };

  return { gh, git, pushed, deleted, deletedAt, posted, created, labelled, unlabelled, reacted, reactionLogins };
}

const go = (f, extra = {}) =>
  run({ gh: f.gh, git: f.git, repo: "apache/magpie-site", fetchArtifact: async () => null, sleep: async () => {}, ...extra });

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

test("a maintainer's command replaces a label a triager added, so arming sticks", async () => {
  const dir = await artifactDir();
  const cmd = armCmd();
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    comments: { 5: [cmd] },
    labelEvents: { 5: [labeledBy("triager")] },
  });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });

  assert.deepEqual(f.unlabelled, [{ n: 5, name: "preview" }]);
  assert.deepEqual(f.labelled, [{ n: 5, name: "preview" }], "re-added as the publisher");
  assert.deepEqual(f.reacted, [cmd.id]);
  assert.equal(f.pushed.length, 1);
});

test("a command leaves a maintainer's label in place", async () => {
  const cmd = armCmd();
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    comments: { 5: [cmd] },
    labelEvents: { 5: [labeledBy("maintainer")] },
  });
  await go(f);
  assert.deepEqual(f.unlabelled, []);
  assert.deepEqual(f.labelled, []);
});

test("a publisher on another token recognises its own label and acknowledgements", async () => {
  const dir = await artifactDir();
  const cmd = armCmd();
  const added = labeledBy("site-app[bot]");
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    comments: { 5: [cmd, bot(`how to\n\n${record(added.id)}\n\n<!-- asf-preview-howto -->`, "site-app[bot]")] },
    labelEvents: { 5: [added] },
    acknowledged: [cmd.id],
  });

  await go(f, {
    publisher: "site-app[bot]",
    fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }),
  });

  assert.ok(f.reactionLogins.length > 0 && f.reactionLogins.every((l) => l === "site-app[bot]"), f.reactionLogins);
  assert.deepEqual(f.labelled, [], "its acknowledged command is not replayed");
  assert.equal(f.pushed.length, 1, "its own label arms the PR");
  assert.ok(!f.posted.some((p) => p.marker === "asf-preview-howto"), "its own explainer is recognised");
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

// What a completed publish of SHA leaves on the PR.
const publishedComments = () => [
  bot(`### Preview published\n\nBuilt from \`${SHA.slice(0, 7)}\`.\n\n<!-- asf-preview-status -->`),
  bot(`### Preview updated to \`${SHA.slice(0, 7)}\`\n\n<!-- asf-preview-published -->`),
];

test("does not republish a preview that already matches the PR head", async () => {
  const dir = await artifactDir();
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [armCmd(), ...publishedComments()] },
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

test("posts the comments a publish pushed but failed to post, without pushing again", async () => {
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [armCmd(), bot("### Preview waiting on a build\n\n<!-- asf-preview-status -->")] },
    branches: ["preview/pr5-staging"],
    headMessages: { "preview/pr5-staging": `Publish preview for #5 (${SHA.slice(0, 7)})` },
  });

  await go(f);

  assert.equal(f.pushed.length, 0);
  assert.match(f.posted.find((p) => p.marker === "asf-preview-status").body, /Built from `ccccccc`/);
  assert.equal(f.created.filter((c) => /asf-preview-published/.test(c.body)).length, 1);
});

test("finds this PR's own build when another PR shares its head commit", async () => {
  const dir = await artifactDir();
  const cleaned = [];
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, hasBuild: [{ id: 2 }, { id: 1 }] });

  await go(f, {
    fetchArtifact: async (id) => ({
      dir,
      meta: { pr: id === 2 ? 6 : 5, headSha: SHA },
      cleanup: async () => cleaned.push(id),
    }),
  });

  assert.equal(f.pushed.length, 1, "the older build is this PR's and publishes");
  assert.deepEqual(cleaned.sort(), [1, 2], "both extracted artifacts are removed");
});

test("malformed metadata is a refusal: the older build is tried, the bad one removed", async () => {
  const dir = await artifactDir();
  const cleaned = [];
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, hasBuild: [{ id: 2 }, { id: 1 }] });
  await go(f, {
    fetchArtifact: async (id) => ({
      dir,
      meta: id === 2 ? { pr: { toString: null }, headSha: SHA } : { pr: 5, headSha: SHA },
      cleanup: async () => cleaned.push(id),
    }),
  });
  assert.equal(process.exitCode ?? 0, 0);
  assert.equal(f.pushed.length, 1);
  assert.deepEqual(cleaned.sort(), [1, 2]);
});

// A fetcher whose run N carries artifact N*10, honouring the publisher's skip
// set as the real one does, and counting the downloads it makes.
function countingFetcher(byRun) {
  const downloads = [];
  const fetchArtifact = async (runId, { skip = new Set() } = {}) => {
    const artifactId = runId * 10;
    if (skip.has(artifactId)) return { skipped: true, artifactId };
    downloads.push(artifactId);
    const result = byRun(runId, artifactId);
    if (result instanceof Error) throw Object.assign(result, { refused: true, artifactId });
    return result && { ...result, artifactId };
  };
  return { fetchArtifact, downloads };
}

const refusedStatus = (sha, ids) =>
  bot(
    `### Preview could not be published\n\n**Preview:** https://magpie-pr5.staged.apache.org/ (unchanged)\n\n` +
      `The build artifact for \`${sha.slice(0, 7)}\` was refused: 1 symlink entry\n\n` +
      `<!-- asf-preview-passed-over ${sha.slice(0, 7)}: ${ids} -->\n\n<!-- asf-preview-status -->`,
  );

test("does not download again an artifact already refused for this head", async () => {
  const refuse = () => new Error("archive contains symlink members");

  const same = countingFetcher(refuse);
  const f1 = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd(), refusedStatus(SHA, "10")] } });
  await go(f1, { fetchArtifact: same.fetchArtifact });
  assert.deepEqual(same.downloads, [], "no download for an artifact already refused");
  assert.equal(f1.posted.filter((p) => p.marker === "asf-preview-status").length, 0, "and no status churn");

  const moved = countingFetcher(refuse);
  const f2 = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd(), refusedStatus("d".repeat(40), "10")] } });
  await go(f2, { fetchArtifact: moved.fetchArtifact });
  assert.deepEqual(moved.downloads, [10], "a new head is screened");

  const dispatched = countingFetcher(refuse);
  const f3 = fakes({ openPulls: [pull(5)], comments: { 5: [refusedStatus(SHA, "10")] } });
  await go(f3, { only: 5, dispatchedBy: "maintainer", fetchArtifact: dispatched.fetchArtifact });
  assert.deepEqual(dispatched.downloads, [10], "a dispatch screens again");
});

test("a re-run's new artifact is screened even though its run id was refused before", async () => {
  const dir = await artifactDir();
  // Run 1 now carries artifact 10; the status remembers an older artifact 7.
  const rerun = countingFetcher(() => ({ dir, meta: { pr: 5, headSha: SHA } }));
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd(), refusedStatus(SHA, "7")] } });
  await go(f, { fetchArtifact: rerun.fetchArtifact });
  assert.deepEqual(rerun.downloads, [10]);
  assert.equal(f.pushed.length, 1);
});

test("a known refusal is skipped on its own while other builds are still looked at", async () => {
  // Build 3: another PR's artifact; build 2: already refused; build 1: expired.
  const fetcher = countingFetcher((run) =>
    run === 3 ? { dir: "/nonexistent", meta: { pr: 6, headSha: SHA }, cleanup: async () => {} } : null,
  );
  const f = fakes({
    openPulls: [pull(5)],
    comments: { 5: [armCmd(), refusedStatus(SHA, "20")] },
    hasBuild: [{ id: 3 }, { id: 2 }, { id: 1 }],
  });
  await go(f, { fetchArtifact: fetcher.fetchArtifact });
  assert.deepEqual(fetcher.downloads, [30, 10], "the refused artifact 20 is not downloaded");
  const status = f.posted.filter((p) => p.marker === "asf-preview-status").at(-1);
  assert.match(status.body, /was refused: 1 symlink entry/, "the earlier refusal is still reported");
  assert.match(status.body, /passed-over ccccccc: 30,20 -->/, "and both passed-over artifacts remembered");

  // With both remembered, the next run downloads nothing at all.
  const next = countingFetcher(() => null);
  const f2 = fakes({
    openPulls: [pull(5)],
    comments: { 5: [armCmd(), bot(`${status.body}\n\n<!-- asf-preview-status -->`)] },
    hasBuild: [{ id: 3 }, { id: 2 }, { id: 1 }],
  });
  await go(f2, { fetchArtifact: next.fetchArtifact });
  assert.deepEqual(next.downloads, [10], "only the expired build is asked about, which downloads nothing");
});

test("another PR's build of a shared head commit is not a refusal of this one", async () => {
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  let cleaned = 0;
  await go(f, {
    fetchArtifact: async () => ({ dir: "/nonexistent", meta: { pr: 6, headSha: SHA }, cleanup: async () => (cleaned += 1) }),
  });
  const status = f.posted.filter((p) => p.marker === "asf-preview-status").at(-1);
  assert.match(status.body, /waiting on a build/, "this PR's own build may still come");
  assert.equal(cleaned, 1);
});

test("an unchanged status comment is not rewritten", async () => {
  const waiting =
    "### Preview waiting on a build\n\n**Preview:** https://magpie-pr5.staged.apache.org/\n\n" +
    `No successful build for \`${SHA.slice(0, 7)}\` yet. The preview publishes as soon as the build goes green.`;
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd(), bot(`${waiting}\n\n<!-- asf-preview-status -->`)] }, hasBuild: false });
  await go(f);
  assert.equal(f.posted.filter((p) => p.marker === "asf-preview-status").length, 0);
});

test("removes a refused artifact", async () => {
  const dir = await artifactDir({ withSymlink: true });
  let cleaned = 0;
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  await go(f, {
    fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA }, cleanup: async () => (cleaned += 1) }),
  });
  assert.equal(f.pushed.length, 0);
  assert.equal(cleaned, 1);
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

test("never publishes an artifact whose metadata claims another PR", async () => {
  const dir = await artifactDir();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 6, headSha: SHA } }) });

  assert.equal(f.pushed.length, 0);
  assert.match(f.posted.at(-1).body, /waiting on a build/i, "it is another PR's build, not this one's");
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
  assert.match(f.posted.at(-1).body, /1 symlink entry/i);
});

test("a refusal comment never repeats the artifact's own names or values", async () => {
  const dir = await artifactDir();
  await symlink("/etc/passwd", join(dir, "@someone [click](evil.example)"));
  const named = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  await go(named, { fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });
  assert.doesNotMatch(named.posted.at(-1).body, /@someone|evil\.example/);

  const bogus = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  await go(bogus, { fetchArtifact: async () => ({ dir, meta: { pr: "@someone", headSha: SHA } }) });
  assert.doesNotMatch(bogus.posted.at(-1).body, /@someone/);
  assert.match(bogus.posted.at(-1).body, /could not be published/);
});

test("an archive the fetcher refuses is reported, and an older build is tried", async () => {
  const dir = await artifactDir();
  const refused = Object.assign(new Error("archive contains symlink members"), { refused: true });

  const one = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  await go(one, { fetchArtifact: async () => { throw refused; } });
  assert.equal(process.exitCode ?? 0, 0, "a refusal is not a failed run");
  assert.match(one.posted.at(-1).body, /could not be published[\s\S]*symlink members/);

  const two = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, hasBuild: [{ id: 2 }, { id: 1 }] });
  await go(two, {
    fetchArtifact: async (id) => {
      if (id === 2) throw refused;
      return { dir, meta: { pr: 5, headSha: SHA } };
    },
  });
  assert.equal(two.pushed.length, 1, "the older, acceptable build publishes");
});

test("a failed download fails the run instead of being reported as a refusal", async () => {
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] } });
  const error = console.error;
  console.error = () => {};
  try {
    await go(f, { fetchArtifact: async () => { throw new Error("artifact download failed with 502"); } });
  } finally {
    console.error = error;
  }
  assert.equal(process.exitCode, 1);
  assert.ok(!f.posted.some((p) => /could not be published/.test(p.body)));
  process.exitCode = 0;
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

test("a tombstone whose retired status never got posted is repaired before its branch goes", async () => {
  const published = bot("### Preview published\n\nBuilt from `ccccccc`.\n\n<!-- asf-preview-status -->");
  const f = fakes({
    openPulls: [],
    comments: { 9: [published] },
    branches: ["preview/pr9-staging"],
    headMessages: { "preview/pr9-staging": "Retire preview for #9 [tombstone]" },
  });
  await go(f);
  const status = f.posted.filter((p) => p.n === 9 && p.marker === "asf-preview-status");
  assert.match(status.at(-1)?.body ?? "", /Preview retired/);
  assert.deepEqual(f.deleted, ["preview/pr9-staging"]);

  // If the status cannot be written, the branch stays for a later run.
  const failing = fakes({
    openPulls: [],
    comments: { 9: [published] },
    branches: ["preview/pr9-staging"],
    headMessages: { "preview/pr9-staging": "Retire preview for #9 [tombstone]" },
  });
  failing.gh.upsertComment = async () => {
    throw new Error("502");
  };
  const error = console.error;
  console.error = () => {};
  try {
    await go(failing);
  } finally {
    console.error = error;
  }
  assert.deepEqual(failing.deleted, []);
  process.exitCode = 0;
});

test("keeps a fresh tombstone up until staging has had time to serve it", async () => {
  const at = Date.parse("2026-01-01T12:00:00Z");
  const fresh = fakes({
    openPulls: [],
    branches: ["preview/pr9-staging"],
    headMessages: { "preview/pr9-staging": "Retire preview for #9 [tombstone]" },
    headDates: { "preview/pr9-staging": "2026-01-01T11:59:00Z" },
  });
  await go(fresh, { now: () => at });
  assert.deepEqual(fresh.deleted, [], "a minute-old tombstone stays");
  assert.equal(fresh.pushed.length, 0, "and is not pushed again");

  const old = fakes({
    openPulls: [],
    branches: ["preview/pr9-staging"],
    headMessages: { "preview/pr9-staging": "Retire preview for #9 [tombstone]" },
    headDates: { "preview/pr9-staging": "2026-01-01T11:00:00Z" },
  });
  await go(old, { now: () => at });
  assert.deepEqual(old.deleted, ["preview/pr9-staging"]);
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
    comments: { 42: [bot(`### Preview armed by manual dispatch\n\n${record(4242)}`)] },
    labelEvents: { 42: [labeledBy("github-actions[bot]", 4242)] },
    branches: ["preview/pr42-staging"],
  });

  await go(f, { fetchArtifact: async () => ({ dir, meta: { pr: 42, headSha: SHA } }) });

  assert.equal(f.pushed.filter((p) => /tombstone/.test(p.message)).length, 0,
    "a dispatched preview must not be reaped on the next run");
  assert.deepEqual(f.deleted, []);
});

test("a label another workflow added with the shared token arms nothing", async () => {
  const f = fakes({
    openPulls: [pull(42, SHA, ["preview"])],
    labelEvents: { 42: [labeledBy("github-actions[bot]")] },
    branches: ["preview/pr42-staging"],
  });
  let fetched = 0;
  await go(f, { fetchArtifact: async () => (fetched += 1, null) });
  assert.equal(fetched, 0, "nothing is published");
  assert.ok(f.pushed.some((p) => /tombstone/.test(p.message)), "and an existing preview is retired");
});

test("a label re-added by the shared token after a disarm does not revive an old arming", async () => {
  // A maintainer armed with /show-preview (the publisher's label event 1,
  // recorded), removed the label to disarm, and an auto-labeller then put it
  // back as the same login: event 3, which no record names.
  const cmd = armCmd();
  const f = fakes({
    openPulls: [pull(42, SHA, ["preview"])],
    comments: { 42: [cmd, bot(`how to\n\n${record(1)}\n\n<!-- asf-preview-howto -->`)] },
    acknowledged: [cmd.id],
    labelEvents: {
      42: [labeledBy("github-actions[bot]", 1), unlabeledBy("maintainer", 2), labeledBy("github-actions[bot]", 3)],
    },
    branches: ["preview/pr42-staging"],
  });
  let fetched = 0;
  await go(f, { fetchArtifact: async () => (fetched += 1, null) });
  assert.equal(fetched, 0, "nothing is published");
  assert.deepEqual(f.labelled, [], "the old command is acknowledged and is not replayed");
  assert.ok(f.pushed.some((p) => /tombstone/.test(p.message)), "the preview stays retired");
});

test("a /show-preview records the new label event in the explainer, by editing it", async () => {
  const events = { 5: [] };
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, labelEvents: events });
  await go(f);
  const added = events[5].at(-1);
  const howto = f.posted.filter((p) => p.marker === "asf-preview-howto");
  assert.equal(howto.length, 1, "one explainer write, carrying the record");
  assert.ok(howto[0].body.endsWith(record(added.id)), howto[0].body);
  assert.equal(f.created.filter((c) => /asf-preview-armed/.test(c.body)).length, 0, "no extra comment");
});

test("a /show-preview on a bot's PR records the label event in a short comment", async () => {
  const f = fakes({ openPulls: [botPull(5)], comments: { 5: [armCmd()] } });
  await go(f);
  assert.equal(f.posted.filter((p) => p.marker === "asf-preview-howto").length, 0);
  const armed = f.created.find((c) => /asf-preview-armed/.test(c.body));
  assert.ok(armed, "the record has to live somewhere");
  assert.doesNotMatch(armed.body, /@/);
});

test("the record names the event the publisher just added, waiting out a lagging events API", async () => {
  const cmd = armCmd();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [cmd] }, eventsLag: 2 });
  await go(f);
  const howto = f.posted.find((p) => p.marker === "asf-preview-howto");
  assert.ok(howto, "the record is written once the event shows up");
  assert.deepEqual(f.reacted, [cmd.id]);

  const stuck = fakes({ openPulls: [pull(5)], comments: { 5: [armCmd()] }, eventsLag: 99 });
  const error = console.error;
  console.error = () => {};
  try {
    await go(stuck);
  } finally {
    console.error = error;
  }
  assert.deepEqual(stuck.reacted, [], "never acknowledged without a record, so the next run retries");
  assert.equal(process.exitCode, 1);
  process.exitCode = 0;
});

test("a remove and bot re-add during the read-back records nothing", async () => {
  // A maintainer removes the publisher's new label and a workflow on the
  // shared token puts it back, all before the publisher reads its event back.
  const events = { 5: [] };
  const cmd = armCmd();
  const f = fakes({ openPulls: [pull(5)], comments: { 5: [cmd] }, labelEvents: events });
  const addLabel = f.gh.addLabel;
  f.gh.addLabel = async (n, name) => {
    await addLabel(n, name);
    events[n].push(unlabeledBy("maintainer"), labeledBy("github-actions[bot]"));
  };
  const error = console.error;
  console.error = () => {};
  try {
    await go(f);
  } finally {
    console.error = error;
  }
  assert.ok(!f.posted.some((p) => /asf-preview-armed/.test(p.body)), "no record in the explainer");
  assert.ok(!f.created.some((c) => /asf-preview-armed/.test(c.body)), "no record comment");
  assert.deepEqual(f.reacted, [], "not acknowledged, so the next run starts over");
  assert.equal(process.exitCode, 1);
  process.exitCode = 0;
});

test("replacing a non-writer's label arms even while the events API lags the removal", async () => {
  const events = { 5: [labeledBy("triager", 1)] };
  const cmd = armCmd();
  const f = fakes({
    openPulls: [pull(5, SHA, ["preview"])],
    comments: { 5: [cmd] },
    labelEvents: events,
    eventsLag: 1,
  });
  await go(f);
  assert.deepEqual(f.unlabelled, [{ n: 5, name: "preview" }]);
  const added = events[5].at(-1);
  assert.equal(added.event, "labeled");
  const howto = f.posted.find((p) => p.marker === "asf-preview-howto");
  assert.ok(howto?.body.endsWith(record(added.id)), "the new label event is recorded");
  assert.deepEqual(f.reacted, [cmd.id]);
  assert.equal(process.exitCode ?? 0, 0);
});

test("a dispatch leaves the record that keeps its label armed", async () => {
  const dir = await artifactDir();
  const f = fakes({ openPulls: [pull(5)] });
  await go(f, { only: 5, dispatchedBy: "maintainer", fetchArtifact: async () => ({ dir, meta: { pr: 5, headSha: SHA } }) });
  assert.match(f.created[0].body, /<!-- asf-preview-armed event=\d+ -->$/);
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
  f.gh.branchHead = async () => {
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

test("injectOverlay handles an uppercase or omitted </body>", async () => {
  const { injectOverlay } = await import("./publish.mjs");

  const upper = injectOverlay("<HTML><BODY><h1>x</h1></BODY></HTML>");
  assert.ok(upper.indexOf("review.js") < upper.indexOf("</BODY>"), upper);

  const noBodyEnd = injectOverlay("<!DOCTYPE html><html><body><h1>x</h1></html>");
  assert.ok(noBodyEnd.indexOf("review.js") < noBodyEnd.indexOf("</html>"), noBodyEnd);

  const neither = injectOverlay("<!doctype html>\n<title>x</title>\n<p>text");
  assert.ok(neither.indexOf("review.js") > neither.indexOf("<p>text"), neither);
  assert.equal(injectOverlay(neither), neither, "injecting twice changes nothing");
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
  assert.deepEqual(f.deletedAt, [SHA], "deleted only while its tip is the one judged stale");
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
