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
 * maintainer's behalf (a /show-preview comment or a manual dispatch), so its
 * own login is trusted — that exact login, never any bot.
 *
 * A label with no matching `labeled` event is treated as unarmed: who added it
 * is unknown, and unknown must not be more permissive than a refusal.
 */
export async function resolveArmed({ pull, label, labelEvents, hasWriteAccess }) {
  if (!hasLabel(pull, label)) return { armed: false, by: null };

  const last = labelEvents
    .filter((e) => e?.event === "labeled" && e?.label?.name === label)
    .at(-1);
  const by = last?.actor?.login;
  if (!by) return { armed: false, by: null };

  if (by === PUBLISHER_LOGIN) return { armed: true, by };
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
