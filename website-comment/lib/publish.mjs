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
  publisherLogin,
  publisherVouched,
  armedRecord,
  EXPLAINER_MARKER,
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
const HOWTO_MARKER = EXPLAINER_MARKER;
// The publisher's acknowledgement on a /show-preview comment it has turned
// into the label. See pendingArmingCommands.
const ACK_REACTION = "rocket";
const TOMBSTONE_TAG = "[tombstone]";
// How long a tombstone stays up before its branch may be deleted: ASF staging
// picks up a push within minutes, and deleting the branch earlier would leave
// the retired preview's content served with nothing left to replace it.
const TOMBSTONE_GRACE_MS = 30 * 60 * 1000;

/**
 * A comment by the publisher's own login ending in exactly this tag.
 *
 * The same predicate github.mjs uses before editing a comment, and for the same
 * reason: a bare substring test lets anyone who can comment plant the marker.
 */
function hasOwnMarker(comments, marker, publisher) {
  const tag = `<!-- ${marker} -->`;
  return comments.some(
    (c) =>
      c?.user?.login === publisher &&
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

/**
 * When the explainer is posted: on every pull request ("always"), or only on
 * one whose head has a successful build carrying a preview-site artifact
 * ("with-preview-artifact"), for sites whose build uploads a preview only
 * when a pull request changes the site (preview-artifact's `paths`).
 */
export const HOWTO_MODES = ["always", "with-preview-artifact"];

/**
 * Whether a successful build of `headSha` uploaded a preview-site artifact
 * that has not expired. Reads run and artifact ids only, as publishOne does.
 */
async function hasPreviewArtifact(gh, headSha) {
  for (const build of await gh.successfulBuilds(headSha)) {
    const artifacts = await gh.listRunArtifacts(build.id, "preview-site");
    if (artifacts.some((a) => a.name === "preview-site" && !a.expired)) return true;
  }
  return false;
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
  publisher = PUBLISHER_LOGIN,
  howto = "always",
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  if (!HOWTO_MODES.includes(howto)) {
    throw new Error(`howto must be one of ${HOWTO_MODES.join(", ")}, got ${JSON.stringify(howto)}`);
  }
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
  const ownLabelEvents = (events) =>
    events.filter((e) => e?.event === "labeled" && e?.label?.name === label && e?.actor?.login === publisher);

  /**
   * Add the label and return the id of the label event that made, for the
   * arming record. The events API can trail the write by a moment, so it is
   * read until a new event appears. Never guessed: the label must have changed
   * exactly once since the read before the add — one new `labeled` event, by
   * the publisher, last — apart from the publisher's own removal just before
   * it when `removed` (replacing a non-writer's label), which a lagging events
   * API can still be catching up on. Anything else (a maintainer removed it
   * and a workflow on the shared token put it back) or an event that never
   * shows up fails this PR's run before the command is acknowledged, and the
   * next run tries again from a clean state.
   */
  const arm = async (n, { removed = false } = {}) => {
    if (!labelReady) {
      await gh.ensureLabel(label);
      labelReady = true;
    }
    const before = new Set((await gh.listLabelEvents(n)).map((e) => e?.id));
    await gh.addLabel(n, label);
    for (let attempt = 0; attempt < 5; attempt += 1) {
      if (attempt) await sleep(1000);
      const since = (await gh.listLabelEvents(n)).filter(
        (e) => e?.label?.name === label && !before.has(e?.id),
      );
      const ours = ownLabelEvents(since);
      if (ours.length === 0) continue;
      const added = since.at(-1);
      const earlier = since.slice(0, -1);
      const expected =
        ours.length === 1 &&
        added === ours[0] &&
        Number.isInteger(added.id) &&
        earlier.length <= (removed ? 1 : 0) &&
        earlier.every((e) => e?.event === "unlabeled" && e?.actor?.login === publisher);
      if (!expected) {
        throw new Error("the label changed again while it was being added; not recording an arming");
      }
      return added.id;
    }
    throw new Error("the label event just added did not appear in the events API");
  };

  // Armed state is resolved for EVERY open PR, even when publishing just one:
  // scoping this to the dispatched PR would leave every other preview looking
  // disarmed, and the reap step would tombstone all of them.
  //
  // The label is the arming state. A maintainer's /show-preview comment and a
  // manual dispatch are two ways of putting it there; removing it disarms.
  const armedByPr = new Map();
  const commentsByPr = new Map();
  const isAcknowledged = (c) => gh.hasReaction(c.id, ACK_REACTION, publisher);
  const armedBy = async (pull, comments) =>
    (await resolveArmed({
      pull,
      label,
      labelEvents: await gh.listLabelEvents(pull.number),
      hasWriteAccess,
      publisher,
      vouched: async (labelEvent) => publisherVouched({ comments, publisher, labelEvent }),
    })).armed;

  for (const pull of openPulls) {
    try {
      const comments = await gh.listComments(pull.number);
      commentsByPr.set(pull.number, comments);
      let labelled = hasLabel(pull, label);
      let armedHere = false;
      let howtoWritten = false;

      const pending = await pendingArmingCommands({
        comments,
        hasWriteAccess,
        isAcknowledged,
      });
      // A dispatch is itself the authorisation.
      if (pending.length || only === pull.number) {
        // A label someone without write access put on first does not arm, and
        // the command is acknowledged below, so it would arm this run only.
        // Re-adding the label as the publisher makes the arming stick.
        let removed = false;
        if (labelled && !(await armedBy(pull, comments))) {
          await gh.removeLabel(pull.number, label);
          labelled = false;
          removed = true;
        }
        if (!labelled) {
          const record = armedRecord(await arm(pull.number, { removed }));
          labelled = true;
          if (only === pull.number) {
            await gh.createComment(pull.number, `${armedBody(pull.number, dispatchedBy, label)}\n\n${record}`);
          } else if (!isBotAuthored(pull)) {
            // Into the explainer, edited in place: an edit notifies nobody.
            await gh.upsertComment(pull.number, HOWTO_MARKER, `${howtoBody(label)}\n\n${record}`);
            howtoWritten = true;
          } else {
            await gh.createComment(pull.number, `${armedByCommandBody(pull.number, label)}\n\n${record}`);
          }
        }
        armedHere = true;
        // Acknowledged only after the label is on, so a failure in between
        // retries on the next run instead of losing the request.
        for (const c of pending) await gh.addReaction(c.id, ACK_REACTION);
      }

      const armed = armedHere || (labelled && (await armedBy(pull, comments)));
      armedByPr.set(pull.number, armed);

      // Announce to humans only. A dependency-bump bot opens many pull
      // requests and reads none of them, so the explainer is noise on its
      // timeline and on commits@. Arming is deliberately not gated the same
      // way: a maintainer who wants a preview of a bot's PR can still ask for
      // one, and it will publish.
      //
      // Where the build uploads a preview only for pull requests that change
      // the site, the explainer waits for one: a pull request with nothing to
      // preview is not told how to preview it. Checked last, as it costs API
      // calls, and only until the explainer is up.
      if (
        !howtoWritten &&
        !isBotAuthored(pull) &&
        !hasOwnMarker(comments, HOWTO_MARKER, publisher) &&
        (howto === "always" || (await hasPreviewArtifact(gh, pull.head.sha)))
      ) {
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
  const settled = new Set();
  const publishedByBranch = new Map();
  for (const branch of previewBranches) {
    try {
      const { message, date } = await gh.branchHead(branch);
      if (message.includes(TOMBSTONE_TAG)) {
        tombstoned.add(branch);
        // An unreadable date never settles: keeping a branch is recoverable.
        const age = now() - Date.parse(date);
        if (Number.isFinite(age) && age >= TOMBSTONE_GRACE_MS) settled.add(branch);
      }

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
    settled,
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
        comments: commentsByPr.get(pr) ?? [],
        publisher,
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
      // The status must say retired before the branch goes: a run that pushed
      // the tombstone but failed to post that would otherwise leave the PR
      // claiming a live preview for good. A closed PR's comments are read here.
      const pr = prOfBranch(branch);
      if (pr !== null) {
        const comments = commentsByPr.get(pr) ?? (await gh.listComments(pr));
        const status = ownMarked(comments, MARKER, publisher);
        if (status?.trimEnd() !== `${retiredBody(pr)}\n\n<!-- ${MARKER} -->`) {
          await gh.upsertComment(pr, MARKER, retiredBody(pr));
        }
      }
      await gh.deleteBranch(branch);
    } catch (err) {
      console.error(`preview: delete failed for ${branch}: ${err.message}`);
      failures += 1;
    }
  }

  // Opt-in: deleting branches other than this system's own preview/* ones is
  // a repository policy decision, not something a preview tool should assume.
  if (reapHeads) failures += await reapHeadBranches({ gh, git, repo });

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
async function reapHeadBranches({ gh, git, repo }) {
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

  // The tips were read before the PR lookups above, and someone may push in
  // between. The delete is conditional on the tip still being the one judged
  // stale, so a branch with new work on it survives.
  const tips = new Map(branches.map((b) => [b.name, b.commit?.sha]));
  for (const name of staleHeadBranches({ branches, pullsByBranch, repo })) {
    try {
      await git.deleteBranchAt(name, tips.get(name));
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
  comments = [],
  publisher = PUBLISHER_LOGIN,
}) {
  const pull = openPulls.find((p) => p.number === pr);
  if (!pull) return false;
  const headSha = pull.head.sha;

  // Nothing has changed since the last publish. Republishing anyway force-pushes
  // an identical tree on every run — a commits@ mail and a rewritten
  // status comment for a preview nobody touched. A manual dispatch is an
  // explicit request, so it republishes regardless. The comments are checked
  // all the same: a run that pushed and then failed to post them would
  // otherwise leave them missing until the next commit.
  if (!force && publishedSha && headSha.startsWith(publishedSha)) {
    await repairComments({ gh, pr, headSha, comments, publisher });
    return false;
  }

  // Status writes that would change nothing are skipped: the publisher runs
  // on every event in the repository, and rewriting an identical comment on
  // each is churn.
  const status = ownMarked(comments, MARKER, publisher);
  const setStatus = async (body) => {
    if (status?.trimEnd() === `${body}\n\n<!-- ${MARKER} -->`) return;
    await gh.upsertComment(pr, MARKER, body);
  };

  const builds = await gh.successfulBuilds(headSha);
  if (!builds.length) {
    await setStatus(waitingBody(pr, headSha));
    return false;
  }

  // Artifacts already passed over for this head (refused, or another PR's
  // build of the same commit) are listed hidden in the status comment and not
  // downloaded again: an artifact does not change, and the publisher runs on
  // every event in the repository. Each is skipped on its own, so the other
  // builds are still looked at. A re-run uploads a new artifact, which is
  // screened; a dispatch screens everything.
  const known = force ? new Set() : passedOverIn(status, headSha);
  const passedOver = [];
  let artifact = null;
  let refusal = null;
  let skippedKnown = false;
  // Newest first, until one build's artifact is this PR's own.
  for (const build of builds) {
    let candidate;
    try {
      candidate = await fetchArtifact(build.id, { skip: known });
    } catch (err) {
      // A refused archive is reported and an older build tried; anything else
      // (a failed download) fails the run, to be retried.
      if (err?.refused !== true) throw err;
      refusal = err.message;
      passedOver.push(err.artifactId);
      continue;
    }
    if (!candidate) continue;
    if (candidate.skipped) {
      passedOver.push(candidate.artifactId);
      skippedKnown = true;
      continue;
    }
    let check;
    try {
      check = validateMeta(candidate.meta, { number: pr, headSha });
    } catch {
      // The metadata is the pull request's: anything validateMeta cannot even
      // inspect is a refusal like any other, never a failed run.
      check = { ok: false, reason: "metadata could not be read" };
    }
    if (check.ok) {
      artifact = candidate;
      break;
    }
    await candidate.cleanup?.();
    passedOver.push(candidate.artifactId);
    // Another PR's build of the same commit says nothing about this PR's own,
    // which may still be on its way: keep waiting rather than refuse.
    if (check.otherPr) continue;
    refusal = check.reason;
  }
  if (!artifact) {
    // A refusal found this run, else the one the status already reports for
    // an artifact skipped as known.
    const reason = refusal ?? (skippedKnown ? refusalIn(status, headSha) : null);
    await setStatus(
      reason ? refusedBody(pr, reason, headSha, passedOver) : waitingBody(pr, headSha, passedOver),
    );
    return false;
  }

  try {
    return await publishArtifact({ gh, git, repo, pr, headSha, artifact, setStatus });
  } finally {
    await artifact.cleanup?.();
  }
}

/** The PR number a preview branch belongs to, or null. */
function prOfBranch(branch) {
  const match = /^preview\/pr(\d+)-staging$/.exec(branch);
  return match ? Number(match[1]) : null;
}

/** The body of the publisher's own comment ending in this marker, or null. */
function ownMarked(comments, marker, publisher) {
  const tag = `<!-- ${marker} -->`;
  const found = comments.filter(
    (c) => c?.user?.login === publisher && typeof c.body === "string" && c.body.trimEnd().endsWith(tag),
  );
  return found.at(-1)?.body ?? null;
}

/** Post whichever of a publish's two comments did not make it for `headSha`. */
async function repairComments({ gh, pr, headSha, comments, publisher }) {
  const short = headSha.slice(0, 7);
  const status = ownMarked(comments, MARKER, publisher);
  if (!status?.includes(`Built from \`${short}\``)) {
    await gh.upsertComment(pr, MARKER, publishedBody(pr, headSha));
  }
  const announced = comments.some(
    (c) =>
      c?.user?.login === publisher &&
      typeof c.body === "string" &&
      c.body.trimEnd().endsWith(`<!-- ${PUBLISHED_MARKER} -->`) &&
      c.body.includes(`updated to \`${short}\``),
  );
  if (!announced) {
    await gh.createComment(pr, `${announceBody(pr, headSha)}\n\n<!-- ${PUBLISHED_MARKER} -->`);
  }
}

/** Screen and push an artifact already matched to this PR's head. */
async function publishArtifact({ gh, git, repo, pr, headSha, artifact, setStatus }) {
  const refuse = (reason) => setStatus(refusedBody(pr, reason, headSha, [artifact.artifactId]));
  if (!artifact.dir) {
    await refuse("artifact had no extracted directory");
    return false;
  }

  // Fail CLOSED. A security screen on the one privileged, credential-holding
  // step must never read "could not look" as "nothing found".
  let unsafe;
  try {
    unsafe = await findUnsafeEntries(artifact.dir);
  } catch (err) {
    // The message can carry paths from the artifact; it goes to the log only.
    console.error(`preview: could not screen the artifact for #${pr}: ${err.message}`);
    await refuse("could not screen the artifact");
    return false;
  }
  if (unsafe.length) {
    // Entry names are pull-request content: the log gets them, the PR a count.
    console.error(`preview: unsafe entries for #${pr}: ${JSON.stringify(unsafe.slice(0, 20))}`);
    await refuse(`${unsafe.length} symlink entr${unsafe.length === 1 ? "y" : "ies"}`);
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

  await setStatus(publishedBody(pr, headSha));
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

const waitingBody = (pr, sha, artifactIds = []) =>
  `### Preview waiting on a build\n\n${urlLine(pr)}\n\nNo successful build for \`${sha.slice(0, 7)}\` yet. ` +
  `The preview publishes as soon as the build goes green.` +
  (artifactIds.length ? `\n\n${passedOverTag(sha, artifactIds)}` : "");

const REFUSED_HEADING = "### Preview could not be published";
const refusedFor = (sha) => `The build artifact for \`${sha.slice(0, 7)}\` was refused`;
// The artifacts passed over for a head ride along hidden in the status, so a
// later run can skip them and still tell a new artifact from those.
const passedOverTag = (sha, ids) =>
  `<!-- asf-preview-passed-over ${sha.slice(0, 7)}: ${ids.filter(Number.isInteger).join(",")} -->`;
const passedOverIn = (body, sha) => {
  const match = new RegExp(`<!-- asf-preview-passed-over ${sha.slice(0, 7)}: ([0-9,]*) -->`).exec(body ?? "");
  return new Set((match?.[1] ?? "").split(",").filter(Boolean).map(Number));
};
/** The reason the status already gives for refusing `sha`, or null. */
const refusalIn = (body, sha) => {
  if (!body?.startsWith(REFUSED_HEADING)) return null;
  const line = (body.split("\n").find((l) => l.startsWith(`${refusedFor(sha)}: `)) ?? "");
  return line ? line.slice(refusedFor(sha).length + 2) : null;
};
const refusedBody = (pr, reason, sha, artifactIds = []) =>
  `${REFUSED_HEADING}\n\n${urlLine(pr)} (unchanged)\n\n` +
  `${refusedFor(sha)}: ${String(reason).slice(0, 200)}\n\n${passedOverTag(sha, artifactIds)}`;

const retiredBody = (pr) =>
  `### Preview retired\n\nThe preview for this pull request is no longer published. ` +
  `${previewUrl(pr)} now serves a notice instead.`;

const armedBody = (pr, by, label) =>
  `### Preview armed by manual dispatch\n\n${urlLine(pr)}\n\n` +
  (by ? `@${by} armed this preview by dispatching the workflow.` : `This preview was armed by manual dispatch.`) +
  ` The \`${label}\` label now tracks this PR's head commit; remove the label to retire the preview.`;

// A bot-authored PR has no explainer to carry the arming record.
const armedByCommandBody = (pr, label) =>
  `### Preview armed\n\n${urlLine(pr)}\n\nA committer armed this preview with \`/show-preview\`. ` +
  `The \`${label}\` label now tracks this PR's head commit; remove the label to retire the preview.`;

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
import { isMain } from "./main.mjs";

if (isMain(import.meta.url)) {
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


  const publisher = publisherLogin();
  const gh = createClient({ repo, token, publisher });
  await run({
    gh,
    git: createGit({ repo, token }),
    publisher,
    repo,
    fetchArtifact: createArtifactFetcher({ gh, token }),
    only,
    dispatchedBy: process.env.GITHUB_ACTOR ?? null,
    reapHeads: process.env.PREVIEW_REAP_HEAD_BRANCHES === "true",
    howto: process.env.PREVIEW_HOWTO || "always",
  });
}
