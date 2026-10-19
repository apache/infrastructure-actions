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
import {
  resolveArmed,
  pendingArmingCommands,
  previewLabel,
  hasLabel,
  publisherLogin,
  publisherVouched,
} from "./armed.mjs";

test("publisherLogin defaults to the workflow token's bot and holds to a login", () => {
  assert.equal(publisherLogin({}), "github-actions[bot]");
  assert.equal(publisherLogin({ PREVIEW_PUBLISHER_LOGIN: "site-app[bot]" }), "site-app[bot]");
  assert.equal(publisherLogin({ PREVIEW_PUBLISHER_LOGIN: "site-bot" }), "site-bot");
  for (const bad of ["a/b", "x y", "-lead", "a[bot", "../x"]) {
    assert.throws(() => publisherLogin({ PREVIEW_PUBLISHER_LOGIN: bad }), /GitHub login/);
  }
});

const comment = (body, login, id = 1) => ({ id, body, user: { login } });
const writers = (...logins) => async (login) => logins.includes(login);
const labelled = { labels: [{ name: "preview" }] };
const labeledBy = (login, name = "preview") => ({ event: "labeled", label: { name }, actor: { login } });

test("previewLabel defaults to preview and rejects anything path-unsafe", () => {
  assert.equal(previewLabel({}), "preview");
  assert.equal(previewLabel({ PREVIEW_LABEL: "show preview" }), "show preview");
  assert.throws(() => previewLabel({ PREVIEW_LABEL: "../x" }), TypeError);
  assert.throws(() => previewLabel({ PREVIEW_LABEL: "a/b" }), TypeError);
});

test("hasLabel reads the pull request's labels", () => {
  assert.equal(hasLabel(labelled, "preview"), true);
  assert.equal(hasLabel({ labels: [] }, "preview"), false);
  assert.equal(hasLabel({}, "preview"), false);
});

test("is unarmed without the label, whatever the events say", async () => {
  const r = await resolveArmed({
    pull: { labels: [] }, label: "preview", labelEvents: [labeledBy("maintainer")], hasWriteAccess: writers("maintainer"),
  });
  assert.equal(r.armed, false);
});

test("arms when a writer added the label", async () => {
  const r = await resolveArmed({
    pull: labelled, label: "preview", labelEvents: [labeledBy("maintainer")], hasWriteAccess: writers("maintainer"),
  });
  assert.deepEqual(r, { armed: true, by: "maintainer" });
});

test("does not arm when a triager without write access added the label", async () => {
  const r = await resolveArmed({
    pull: labelled, label: "preview", labelEvents: [labeledBy("triager")], hasWriteAccess: writers("maintainer"),
  });
  assert.equal(r.armed, false);
});

test("judges the LAST time the label was added", async () => {
  const r = await resolveArmed({
    pull: labelled,
    label: "preview",
    labelEvents: [labeledBy("maintainer"), { event: "unlabeled", label: { name: "preview" } }, labeledBy("triager")],
    hasWriteAccess: writers("maintainer"),
  });
  assert.equal(r.armed, false);
});

test("trusts the publisher's own label only with its arming record, and no other bot", async () => {
  const noone = writers();
  const base = { pull: labelled, label: "preview", hasWriteAccess: noone };
  const own = await resolveArmed({
    ...base, labelEvents: [labeledBy("github-actions[bot]")], vouched: async (event) => event?.event === "labeled",
  });
  // Another workflow on the shared token (an auto-labeller) added it.
  const unvouched = await resolveArmed({ ...base, labelEvents: [labeledBy("github-actions[bot]")] });
  const other = await resolveArmed({
    ...base, labelEvents: [labeledBy("other-app[bot]")], vouched: async () => true,
  });
  assert.equal(own.armed, true);
  assert.equal(unvouched.armed, false);
  assert.equal(other.armed, false);
});

test("publisherVouched needs the publisher's record for the current label event", async () => {
  const publisher = "github-actions[bot]";
  const hasWriteAccess = async (login) => login === "maintainer";
  const T0 = "2026-01-01T00:00:00Z";
  const T1 = "2026-01-01T00:00:05Z";
  const EARLIER = "2025-12-31T00:00:00Z";
  const labelEvent = { event: "labeled", created_at: T0 };
  const cmd = (login, id) => ({ id, user: { login, type: "User" }, body: "/show-preview" });
  const vouched = (comments, acks = {}, event = labelEvent) =>
    publisherVouched({ comments, hasWriteAccess, reactedAt: async (c) => acks[c.id] ?? null, publisher, labelEvent: event });
  const armed = (at) => ({ user: { login: publisher, type: "Bot" }, body: "armed\n\n<!-- asf-preview-armed -->", created_at: at });

  assert.equal(await vouched([]), false);
  assert.equal(await vouched([armed(T1)]), true);
  assert.equal(await vouched([armed(EARLIER)]), false, "a dispatch record from an earlier arming");
  assert.equal(await vouched([{ ...armed(T1), user: { login: "other[bot]", type: "Bot" } }]), false);
  assert.equal(await vouched([cmd("maintainer", 1)], { 1: T1 }), true);
  assert.equal(await vouched([cmd("maintainer", 1)], { 1: EARLIER }), false, "acknowledged for an earlier arming");
  assert.equal(await vouched([cmd("maintainer", 1)], {}), false, "not acknowledged");
  assert.equal(await vouched([cmd("drive-by", 2)], { 2: T1 }), false, "not a writer");
  assert.equal(await vouched([armed(T1)], {}, { event: "labeled" }), false, "an event with no time never vouches");
});

test("a label with no labeled event is unarmed", async () => {
  const r = await resolveArmed({
    pull: labelled, label: "preview", labelEvents: [labeledBy("maintainer", "other")], hasWriteAccess: writers("maintainer"),
  });
  assert.equal(r.armed, false);
});

test("a writer's unacknowledged command is pending", async () => {
  const c = comment("/show-preview", "maintainer");
  const out = await pendingArmingCommands({
    comments: [c], hasWriteAccess: writers("maintainer"), isAcknowledged: async () => false,
  });
  assert.deepEqual(out, [c]);
});

test("an acknowledged command is not pending again", async () => {
  const out = await pendingArmingCommands({
    comments: [comment("/show-preview", "maintainer")],
    hasWriteAccess: writers("maintainer"),
    isAcknowledged: async () => true,
  });
  assert.deepEqual(out, []);
});

test("a non-writer's command is never pending, and costs no acknowledgement lookup", async () => {
  let looked = 0;
  const out = await pendingArmingCommands({
    comments: [comment("/show-preview", "drive-by")],
    hasWriteAccess: writers("maintainer"),
    isAcknowledged: async () => {
      looked += 1;
      return false;
    },
  });
  assert.deepEqual(out, []);
  assert.equal(looked, 0);
});

test("ignores comments that merely mention the command", async () => {
  const out = await pendingArmingCommands({
    comments: [comment("try /show-preview here", "maintainer")],
    hasWriteAccess: writers("maintainer"),
    isAcknowledged: async () => false,
  });
  assert.deepEqual(out, []);
});

test("checks each author at most once", async () => {
  let calls = 0;
  await pendingArmingCommands({
    comments: [comment("/show-preview", "drive-by", 1), comment("/show-preview", "drive-by", 2)],
    hasWriteAccess: async () => {
      calls += 1;
      return false;
    },
    isAcknowledged: async () => false,
  });
  assert.equal(calls, 1);
});
