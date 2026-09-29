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

  renderAsfYaml,
  renderRobots,
  renderTombstone,
  previewBranch,
  previewUrl,
  siteName,
} from "./files.mjs";

// Every URL below is for a site staged as magpie-pr<N>.staged.apache.org.
process.env.PREVIEW_SITE_NAME = "magpie";

test("branch and url agree on the profile", () => {
  assert.equal(previewBranch(176), "preview/pr176-staging");
  assert.equal(previewUrl(176), "https://magpie-pr176.staged.apache.org/");
});

test("asf.yaml declares the profile and a whoami matching the branch", () => {
  const yaml = renderAsfYaml(176);
  assert.match(yaml, /profile: pr176/);
  assert.match(yaml, /whoami: preview\/pr176-staging/);
});

test("asf.yaml never contains a publish block", () => {
  // A publish block on a preview branch would target magpie.apache.org.
  assert.doesNotMatch(renderAsfYaml(176), /publish:/);
});

test("robots.txt disallows everything", () => {
  assert.match(renderRobots(), /User-agent: \*/);
  assert.match(renderRobots(), /Disallow: \//);
});

test("the tombstone names the PR and links to it", () => {
  const html = renderTombstone({ pr: 176, repo: "apache/magpie-site" });
  assert.match(html, /176/);
  assert.match(html, /https:\/\/github\.com\/apache\/magpie-site\/pull\/176/);
  assert.match(html, /<meta name="robots" content="noindex">/);
});

test("refuses a pr value that would inject YAML", () => {
  assert.throws(
    () => renderAsfYaml("1\npublish:\n  whoami: evil"),
    /digits only/,
  );
});

test("refuses a missing or non-numeric pr", () => {
  assert.throws(() => previewBranch(undefined), /digits only/);
  assert.throws(() => previewUrl("../../evil"), /digits only/);
  assert.throws(() => renderTombstone({ pr: "x", repo: "apache/magpie-site" }), /digits only/);
});

test("still accepts a numeric string", () => {
  assert.equal(previewBranch("176"), "preview/pr176-staging");
});

test("siteName is required and accepts a site's label", () => {
  assert.throws(() => siteName({}), /hostname label/);
  assert.throws(() => siteName({ PREVIEW_SITE_NAME: "" }), /hostname label/);
  assert.equal(siteName({ PREVIEW_SITE_NAME: "tooling-site" }), "tooling-site");
});

test("siteName refuses anything that is not a hostname label", () => {
  for (const bad of ["Magpie", "a.b", "-x", "x-", "a\nstaging:", "a b"]) {
    assert.throws(() => siteName({ PREVIEW_SITE_NAME: bad }), /hostname label/);
  }
});
