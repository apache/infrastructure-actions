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

import { isShowPreviewComment } from "./comments.mjs";

/** The login the workflow's GITHUB_TOKEN acts as. */
export const PUBLISHER_LOGIN = "github-actions[bot]";

/**
 * The login the publisher's token acts as. It defaults to the workflow token's
 * bot; a site that passes another token (a GitHub App's, or a personal one)
 * sets PREVIEW_PUBLISHER_LOGIN to match, or its own acknowledgements, labels
 * and status comments would not be recognised as its own.
 */
export function publisherLogin(env = process.env) {
  const login = env.PREVIEW_PUBLISHER_LOGIN || PUBLISHER_LOGIN;
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$/.test(login)) {
    throw new TypeError(`PREVIEW_PUBLISHER_LOGIN must be a GitHub login, got ${JSON.stringify(login)}`);
  }
  return login;
}

/**
 * The label that arms a pull request's preview. Another site sets
 * PREVIEW_LABEL; it is interpolated into an API path, so it is held to a plain
 * label-name alphabet.
 */
export function previewLabel(env = process.env) {
  const name = env.PREVIEW_LABEL || "preview";
  if (!/^[A-Za-z0-9][A-Za-z0-9 _.:-]{0,49}$/.test(name)) {
    throw new TypeError(`PREVIEW_LABEL must be a plain label name, got ${JSON.stringify(name)}`);
  }
  return name;
}

export const hasLabel = (pull, label) =>
  Array.isArray(pull?.labels) && pull.labels.some((l) => l?.name === label);

/**
 * Armed means the preview label is on the pull request AND whoever last added
 * it has write access.
 *
 * The label alone is not enough: on an ASF repository, collaborators with the
 * triage role can add labels without being committers, and arming has always
 * required write access. The publisher itself adds the label only on a
 * maintainer's behalf (a /show-preview comment or a manual dispatch), but its
 * login is not proof of that: the default github-actions[bot] is shared by
 * every workflow in the repository, an auto-labeller included. So a label
 * from `publisher` counts only when `vouched` finds the record the publisher
 * leaves when it arms — see publisherVouched. Any other bot never counts.
 *
 * A label with no matching `labeled` event is treated as unarmed: who added it
 * is unknown, and unknown must not be more permissive than a refusal.
 */
export async function resolveArmed({
  pull,
  label,
  labelEvents,
  hasWriteAccess,
  publisher = PUBLISHER_LOGIN,
  vouched = async () => false,
}) {
  if (!hasLabel(pull, label)) return { armed: false, by: null };

  const last = labelEvents
    .filter((e) => e?.event === "labeled" && e?.label?.name === label)
    .at(-1);
  const by = last?.actor?.login;
  if (!by) return { armed: false, by: null };

  if (by === publisher) return (await vouched()) ? { armed: true, by } : { armed: false, by: null };
  return (await hasWriteAccess(by)) ? { armed: true, by } : { armed: false, by: null };
}

/**
 * The /show-preview comments that should arm the pull request now: posted by
 * someone with write access, and not yet acknowledged by the publisher.
 *
 * The acknowledgement (a reaction, see publish.mjs) is what makes the label the
 * single source of truth. Without it, a maintainer removing the label to disarm
 * would see it re-added on the next run from the same old comment.
 *
 * Write access is resolved per author and memoised, so a PR spammed with the
 * command costs one permission lookup per distinct author.
 */
export async function pendingArmingCommands({ comments, hasWriteAccess, isAcknowledged }) {
  const writer = new Map();
  const out = [];

  for (const c of comments) {
    if (!isShowPreviewComment(c?.body)) continue;
    const login = c?.user?.login;
    if (!login) continue;

    if (!writer.has(login)) writer.set(login, await hasWriteAccess(login));
    if (!writer.get(login)) continue;
    if (await isAcknowledged(c)) continue;
    out.push(c);
  }

  return out;
}

/** Ends the publisher's comment announcing a dispatch-armed preview. */
export const ARMED_MARKER = "asf-preview-armed";

/**
 * Whether the pull request carries the record the publisher leaves when it
 * arms on a maintainer's behalf: its own dispatch announcement, or a
 * /show-preview from someone with write access that it acknowledged. A label
 * from the publisher's login without either was put there by some other
 * workflow sharing the token, and arms nothing.
 */
export async function publisherVouched({ comments, hasWriteAccess, isAcknowledged, publisher }) {
  const tag = `<!-- ${ARMED_MARKER} -->`;
  const announced = comments.some(
    (c) => c?.user?.login === publisher && typeof c.body === "string" && c.body.trimEnd().endsWith(tag),
  );
  if (announced) return true;

  for (const c of comments) {
    if (!isShowPreviewComment(c?.body)) continue;
    const login = c?.user?.login;
    if (!login || !(await hasWriteAccess(login))) continue;
    if (await isAcknowledged(c)) return true;
  }
  return false;
}
