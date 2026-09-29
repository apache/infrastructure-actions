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

import {
  resolveArmed,
  pendingArmingCommands,
  previewLabel,
  hasLabel,
  PUBLISHER_LOGIN,
} from "./armed.mjs";
import { planActions } from "./plan.mjs";
import { staleHeadBranches } from "./stale.mjs";
import { validateMeta, findUnsafeEntries } from "./validate.mjs";
import { sanitizeAnchors } from "./anchors.mjs";
import { overlayFiles, injectOverlay, injectIntoTree } from "./overlay-files.mjs";
import {
  renderAsfYaml,
  renderRobots,
  renderTombstone,
  previewBranch,
  previewUrl,
} from "./files.mjs";

const MARKER = "asf-preview-status";
const PUBLISHED_MARKER = "asf-preview-published";
const HOWTO_MARKER = "asf-preview-howto";
// The publisher's acknowledgement on a /show-preview comment it has turned
// into the label. See pendingArmingCommands.
const ACK_REACTION = "rocket";
const TOMBSTONE_TAG = "[tombstone]";

/**
 * A bot-authored comment ending in exactly this tag.
 *
 * The same predicate github.mjs uses before editing a comment, and for the same
 * reason: a bare substring test lets anyone who can comment plant the marker.
 */
function hasBotMarker(comments, marker, { login = null } = {}) {
  const tag = `<!-- ${marker} -->`;
  return comments.some(
    (c) =>
      c?.user?.type === "Bot" &&
      (login === null || c?.user?.login === login) &&
      typeof c.body === "string" &&
      c.body.trimEnd().endsWith(tag),
  );
}

const PUBLISHED_SHA_RE = /\(([0-9a-f]{7,40})\)\s*$/;

/**
 * The commit a preview branch was last published from, read from its head
 * commit subject — "Publish preview for #180 (14fdc13)" — or null.
 *
 * A tombstoned branch's subject carries no SHA, so a re-armed pull request
 * publishes again rather than being mistaken for up to date.
 */
export function publishedShaFrom(message) {
  const subject = String(message ?? "").split("\n")[0].trim();
  const match = PUBLISHED_SHA_RE.exec(subject);
  return match ? match[1] : null;
}

/**
 * Whether a pull request was opened by a bot — dependabot and friends.
 *
 * `user.type` is the authoritative signal; the login suffix is a fallback for
 * anything the API reports as a User but which is plainly automation.
 */
export function isBotAuthored(pull) {
  if (pull?.user?.type === "Bot") return true;
  return /\[bot\]$/.test(String(pull?.user?.login ?? ""));
}

export async function run({
  gh,
  git,
  repo,
  fetchArtifact,
  only = null,
  dispatchedBy = null,
  reapHeads = false,
  label = previewLabel(),
}) {
  let failures = 0;

  const openPulls = await gh.listOpenPulls();

  // A dispatch naming a closed, merged or nonexistent PR must fail loudly, not
  // silently succeed with nothing published.
  if (only !== null && !openPulls.some((p) => p.number === only)) {
    throw new Error(`--pr ${only} is not an open pull request`);
  }

  // One permission lookup per login per run, however many PRs it touches.
  const access = new Map();
  const hasWriteAccess = async (login) => {
    if (!access.has(login)) access.set(login, await gh.hasWriteAccess(login));
    return access.get(login);
  };

  let labelReady = false;
  const arm = async (n) => {
    if (!labelReady) {
      await gh.ensureLabel(label);
      labelReady = true;
    }
    await gh.addLabel(n, label);
  };

  // Armed state is resolved for EVERY open PR, even when publishing just one:
  // scoping this to the dispatched PR would leave every other preview looking
  // disarmed, and the reap step would tombstone all of them.
  //
  // The label is the arming state. A maintainer's /show-preview comment and a
  // manual dispatch are two ways of putting it there; removing it disarms.
  const armedByPr = new Map();
  for (const pull of openPulls) {
    try {
      const comments = await gh.listComments(pull.number);
      let labelled = hasLabel(pull, label);
      let armedHere = false;

      const pending = await pendingArmingCommands({
        comments,
        hasWriteAccess,
        isAcknowledged: (c) => gh.hasReaction(c.id, ACK_REACTION, PUBLISHER_LOGIN),
      });
      // A dispatch is itself the authorisation.
      if (pending.length || only === pull.number) {
        if (!labelled) {
          await arm(pull.number);
          labelled = true;
          if (only === pull.number) {
            await gh.createComment(pull.number, armedBody(pull.number, dispatchedBy, label));
          }
        }
        armedHere = true;
        // Acknowledged only after the label is on, so a failure in between
        // retries on the next run instead of losing the request.
        for (const c of pending) await gh.addReaction(c.id, ACK_REACTION);
      }

      const armed = armedHere || (labelled && (await resolveArmed({
        pull,
        label,
        labelEvents: await gh.listLabelEvents(pull.number),
        hasWriteAccess,
      })).armed);
      armedByPr.set(pull.number, armed);

      // Announce to humans only. A dependency-bump bot opens many pull
      // requests and reads none of them, so the explainer is noise on its
      // timeline and on commits@. Arming is deliberately not gated the same
      // way: a maintainer who wants a preview of a bot's PR can still ask for
      // one, and it will publish.
      if (!isBotAuthored(pull) && !hasBotMarker(comments, HOWTO_MARKER)) {
        await gh.upsertComment(pull.number, HOWTO_MARKER, howtoBody(label));
      }
    } catch (err) {
      // One PR's transient failure must not abort every other publish and the
      // whole reap. The PR is left with no armedByPr entry, which planActions
      // treats as unknown and leaves alone.
      console.error(`preview: skipping #${pull.number}: ${err.message}`);
      failures += 1;
    }
  }

  const previewBranches = await gh.listPreviewBranches();

  // Derived from each branch's ACTUAL head commit, never from a belief that an
  // earlier push succeeded: deleting a branch does not unstage the site, so a
  // delete after a failed tombstone strands live content with nothing left to
  // overwrite it.
  const tombstoned = new Set();
  const publishedByBranch = new Map();
  for (const branch of previewBranches) {
    try {
      const message = await gh.branchHeadMessage(branch);
      if (message.includes(TOMBSTONE_TAG)) tombstoned.add(branch);

      const sha = publishedShaFrom(message);
      if (sha) publishedByBranch.set(branch, sha);
    } catch (err) {
      // Treat an unreadable head as un-tombstoned. Re-pushing a tombstone is
      // idempotent; deleting a branch we could not inspect is not recoverable,
      // because deleting a branch does not unstage the site.
      console.error(`preview: could not read ${branch} head: ${err.message}`);
      failures += 1;
    }
  }

  const actions = planActions({
    openPulls: openPulls.map((p) => p.number),
    armedByPr,
    previewBranches,
    tombstoned,
  });

  // A dispatch publishes only its PR, but still reaps everything.
  if (only !== null) actions.publish = actions.publish.filter((n) => n === only);

  for (const pr of actions.publish) {
    try {
      await publishOne({
        gh,
        git,
        repo,
        fetchArtifact,
        openPulls,
        pr,
        publishedSha: publishedByBranch.get(previewBranch(pr)) ?? null,
        force: only === pr,
      });
    } catch (err) {
      console.error(`preview: publish failed for #${pr}: ${err.message}`);
      failures += 1;
    }
  }

  for (const pr of actions.tombstone) {
    try {
      await git.pushTree(
        previewBranch(pr),
        {
          "index.html": renderTombstone({ pr, repo }),
          ".asf.yaml": renderAsfYaml(pr),
          "robots.txt": renderRobots(),
        },
        `Retire preview for #${pr} ${TOMBSTONE_TAG}`,
      );
      await gh.upsertComment(pr, MARKER, retiredBody(pr));
    } catch (err) {
      console.error(`preview: tombstone failed for #${pr}: ${err.message}`);
      failures += 1;
    }
  }

  for (const branch of actions.delete) {
    try {
      await gh.deleteBranch(branch);
    } catch (err) {
      console.error(`preview: delete failed for ${branch}: ${err.message}`);
      failures += 1;
    }
  }

  // Opt-in: deleting branches other than this system's own preview/* ones is
  // a repository policy decision, not something a preview tool should assume.
  if (reapHeads) failures += await reapHeadBranches({ gh, repo });

  if (failures > 0) {
    console.error(`preview: ${failures} operation(s) failed this run`);
    process.exitCode = 1;
  }
}

/**
 * Delete this repository's head branches whose pull requests have all closed.
 * staleHeadBranches holds the rules; this only gathers state and acts on it.
 * Returns the number of operations that failed.
 */
async function reapHeadBranches({ gh, repo }) {
  let failures = 0;

  let branches;
  try {
    branches = await gh.listBranches();
  } catch (err) {
    console.error(`preview: could not list branches: ${err.message}`);
    return 1;
  }

  // A branch whose lookup failed gets no entry, and staleHeadBranches keeps it.
  const pullsByBranch = new Map();
  for (const branch of branches) {
    if (branch.protected) continue;
    try {
      pullsByBranch.set(branch.name, await gh.listPullsForHead(branch.name));
    } catch (err) {
      console.error(`preview: could not list PRs for ${branch.name}: ${err.message}`);
      failures += 1;
    }
  }

  for (const name of staleHeadBranches({ branches, pullsByBranch, repo })) {
    try {
      await gh.deleteBranch(name);
      console.log(`preview: deleted ${name}, whose pull requests are all closed`);
    } catch (err) {
      console.error(`preview: delete failed for ${name}: ${err.message}`);
      failures += 1;
    }
  }

  return failures;
}

/** Returns true only when content was actually published. */
async function publishOne({
  gh,
  git,
  repo,
  fetchArtifact,
  openPulls,
  pr,
  publishedSha = null,
  force = false,
}) {
  const pull = openPulls.find((p) => p.number === pr);
  if (!pull) return false;
  const headSha = pull.head.sha;

  // Nothing has changed since the last publish. Republishing anyway force-pushes
  // an identical tree on every run — a commits@ mail and a rewritten
  // status comment for a preview nobody touched. A manual dispatch is an
  // explicit request, so it republishes regardless.
  if (!force && publishedSha && headSha.startsWith(publishedSha)) return false;

  const build = await gh.latestSuccessfulBuild(headSha);
  if (!build) {
    await gh.upsertComment(pr, MARKER, waitingBody(pr, headSha));
    return false;
  }

  const artifact = await fetchArtifact(build.id);
  if (!artifact) {
    await gh.upsertComment(pr, MARKER, waitingBody(pr, headSha));
    return false;
  }

  const check = validateMeta(artifact.meta, { number: pr, headSha });
  if (!check.ok) {
    await gh.upsertComment(pr, MARKER, refusedBody(pr, check.reason));
    return false;
  }

  if (!artifact.dir) {
    await gh.upsertComment(pr, MARKER, refusedBody(pr, "artifact had no extracted directory"));
    return false;
  }

  // Fail CLOSED. A security screen on the one privileged, credential-holding
  // step must never read "could not look" as "nothing found".
  let unsafe;
  try {
    unsafe = await findUnsafeEntries(artifact.dir);
  } catch (err) {
    await gh.upsertComment(pr, MARKER, refusedBody(pr, `could not screen the artifact: ${err.message}`));
    return false;
  }
  if (unsafe.length) {
    await gh.upsertComment(pr, MARKER, refusedBody(pr, `unsafe entries: ${unsafe.join(", ")}`));
    return false;
  }

  // Computed by the unprivileged build: the diff is pull-request content, and
  // the publisher never reads it.
  const anchors = sanitizeAnchors(artifact.meta?.anchors);
  const generated = {
    ".asf.yaml": renderAsfYaml(pr),
    "robots.txt": renderRobots(),
    ...(await overlayFiles({ repo, pr, sha: headSha.slice(0, 7), anchors })),
  };

  await injectIntoTree(artifact.dir);

  await git.pushTree(
    previewBranch(pr),
    generated,
    `Publish preview for #${pr} (${headSha.slice(0, 7)})`,
    artifact.dir,
  );

  await gh.upsertComment(pr, MARKER, publishedBody(pr, headSha));
  // The status comment above is edited in place, and an edit notifies nobody.
  // Each publish is also announced as a new comment, so everyone following the
  // pull request learns that the preview now shows the latest push.
  await gh.createComment(pr, `${announceBody(pr, headSha)}\n\n<!-- ${PUBLISHED_MARKER} -->`);
  return true;
}

// Re-exported for the tests and any caller that imported it from here.
export { injectOverlay };

// Every preview comment leads with the preview URL on its own line, so it is
// one click away whatever the comment is about.
const urlLine = (pr) => `**Preview:** ${previewUrl(pr)}`;

const publishedBody = (pr, sha) =>
  `### Preview published\n\n${urlLine(pr)}\n\nBuilt from \`${sha.slice(0, 7)}\`. ` +
  `Staging takes a few minutes to pick up a new push.`;

const announceBody = (pr, sha) =>
  `### Preview updated to \`${sha.slice(0, 7)}\`\n\n${urlLine(pr)}\n\n` +
  `Staging usually serves it within a few minutes.`;

const waitingBody = (pr, sha) =>
  `### Preview waiting on a build\n\n${urlLine(pr)}\n\nNo successful build for \`${sha.slice(0, 7)}\` yet. ` +
  `The preview publishes as soon as the build goes green.`;

const refusedBody = (pr, reason) =>
  `### Preview could not be published\n\n${urlLine(pr)} (unchanged)\n\n` +
  `The build artifact was refused: ${String(reason).slice(0, 200)}`;

const retiredBody = (pr) =>
  `### Preview retired\n\nThe preview for this pull request is no longer published. ` +
  `${previewUrl(pr)} now serves a notice instead.`;

const armedBody = (pr, by, label) =>
  `### Preview armed by manual dispatch\n\n${urlLine(pr)}\n\n` +
  (by ? `@${by} armed this preview by dispatching the workflow.` : `This preview was armed by manual dispatch.`) +
  ` The \`${label}\` label now tracks this PR's head commit; remove the label to retire the preview.`;

// No URL here: the PR has no preview yet, and a link that does not resolve is
// worse than none. The URL is posted with the preview, once it is published.
const howtoBody = (label) =>
  `### Preview this pull request\n\nA committer can publish a live preview of this PR by ` +
  `adding the \`${label}\` label, or by commenting \`/show-preview\` on its own line, which ` +
  `adds the label. The preview's link is posted here once it is published, and it then tracks ` +
  `this PR's head commit until the PR closes or the label is removed.`;

import { createClient } from "./github.mjs";
import { createGit } from "./git.mjs";
import { createArtifactFetcher } from "./artifact.mjs";

if (import.meta.url === `file://${process.argv[1]}`) {
  const repo = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN;
  if (!repo || !token) {
    console.error("GITHUB_REPOSITORY and GITHUB_TOKEN are required");
    process.exit(1);
  }

  // Strict parsing on purpose: `Number(...)` + `Number.isInteger` alone lets
  // `--pr -5` through (a negative integer that then matches no PR and quietly
  // publishes nothing) and silently ignores `--pr=42` (`only` stays null and
  // the run publishes EVERY armed PR instead of one). Only a plain run of
  // digits is accepted, both forms are recognised, and repeating the flag is
  // an error rather than picking the first or last occurrence.
  const args = process.argv.slice(2);
  const prValues = [];
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--pr") {
      prValues.push(args[i + 1]);
      i += 1;
    } else if (arg.startsWith("--pr=")) {
      prValues.push(arg.slice("--pr=".length));
    }
  }

  if (prValues.length > 1) {
    console.error("--pr may only be given once");
    process.exit(1);
  }

  let only = null;
  if (prValues.length === 1) {
    const raw = prValues[0];
    if (raw === undefined || !/^\d+$/.test(raw)) {
      console.error(`--pr requires a non-negative integer, got ${JSON.stringify(raw ?? null)}`);
      process.exit(1);
    }
    only = Number(raw);
  }


  const gh = createClient({ repo, token });
  await run({
    gh,
    git: createGit({ repo, token }),
    repo,
    fetchArtifact: createArtifactFetcher({ gh, token }),
    only,
    dispatchedBy: process.env.GITHUB_ACTOR ?? null,
    reapHeads: process.env.PREVIEW_REAP_HEAD_BRANCHES === "true",
  });
}
