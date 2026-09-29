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
import { clampRegion, captionFor, targetUrl } from "./logic.mjs";

const viewport = { w: 1000, h: 800 };

test("clampRegion normalises a drag in any direction", () => {
  assert.deepEqual(clampRegion({ x1: 300, y1: 200, x2: 100, y2: 50 }, viewport), {
    x: 100, y: 50, w: 200, h: 150,
  });
});

test("clampRegion clips to the viewport", () => {
  assert.deepEqual(clampRegion({ x1: -50, y1: -20, x2: 1200, y2: 900 }, viewport), {
    x: 0, y: 0, w: 1000, h: 800,
  });
});

test("clampRegion rejects a region too small to be deliberate", () => {
  assert.equal(clampRegion({ x1: 10, y1: 10, x2: 14, y2: 14 }, viewport), null);
});

test("captionFor names the page, the source and the build", () => {
  const caption = captionFor({
    url: "https://magpie-pr180.staged.apache.org/tools",
    source: "src/components/landing/SiteFooter.tsx:72",
    region: { x: 240, y: 1150, w: 420, h: 180 },
    sha: "14fdc13",
  });

  assert.match(caption, /magpie-pr180\.staged\.apache\.org\/tools/);
  assert.match(caption, /SiteFooter\.tsx:72/);
  assert.match(caption, /420×180/);
  assert.match(caption, /14fdc13/);
});

test("captionFor says so when no source was resolved", () => {
  const caption = captionFor({
    url: "https://x/",
    source: null,
    region: { x: 0, y: 0, w: 10, h: 10 },
    sha: "abc1234",
  });
  assert.match(caption, /source not resolved/i);
});

test("targetUrl anchors on the diff line when the line is in the diff", () => {
  const anchors = { "a.tsx": { anchor: "diff-deadbeef", ranges: [[10, 20]] } };
  assert.equal(
    targetUrl({ repo: "apache/magpie-site", pr: 180, source: "a.tsx:12", anchors }),
    "https://github.com/apache/magpie-site/pull/180/files#diff-deadbeefR12",
  );
});

test("targetUrl falls back to the conversation for a line outside the diff", () => {
  const anchors = { "a.tsx": { anchor: "diff-deadbeef", ranges: [[10, 20]] } };
  assert.equal(
    targetUrl({ repo: "apache/magpie-site", pr: 180, source: "a.tsx:99", anchors }),
    "https://github.com/apache/magpie-site/pull/180",
  );
});

test("targetUrl falls back when the file, the manifest or the source is missing", () => {
  const base = { repo: "apache/magpie-site", pr: 180 };
  const conversation = "https://github.com/apache/magpie-site/pull/180";
  assert.equal(targetUrl({ ...base, source: "other.tsx:1", anchors: {} }), conversation);
  assert.equal(targetUrl({ ...base, source: "a.tsx:1", anchors: null }), conversation);
  assert.equal(targetUrl({ ...base, source: null, anchors: {} }), conversation);
});

test("targetUrl treats the diff range as inclusive at both ends", () => {
  const anchors = { "a.tsx": { anchor: "diff-deadbeef", ranges: [[10, 20]] } };
  const base = { repo: "apache/magpie-site", pr: 180, anchors };
  const files = (line) => targetUrl({ ...base, source: `a.tsx:${line}` });

  assert.match(files(10), /files#diff-deadbeefR10$/, "the first line of a range is in the diff");
  assert.match(files(20), /files#diff-deadbeefR20$/, "the last line of a range is in the diff");
  assert.equal(files(9), "https://github.com/apache/magpie-site/pull/180", "one before is not");
  assert.equal(files(21), "https://github.com/apache/magpie-site/pull/180", "one after is not");
});

test("clampRegion yields no region for a drag entirely off-screen", () => {
  assert.equal(clampRegion({ x1: 1200, y1: 100, x2: 1300, y2: 200 }, viewport), null);
  assert.equal(clampRegion({ x1: 100, y1: -300, x2: 200, y2: -200 }, viewport), null);
});

test("sourceUrl links the marked line on the branch", async () => {
  const { sourceUrl } = await import("./logic.mjs");
  assert.equal(
    sourceUrl({ repo: "apache/magpie-site", branch: "main", source: "src/pages/tools.astro:12" }),
    "https://github.com/apache/magpie-site/blob/main/src/pages/tools.astro#L12",
  );
  assert.equal(sourceUrl({ repo: "a/b", branch: "main", source: null }), null);
  assert.equal(sourceUrl({ repo: "a/b", branch: "main", source: "no-line" }), null);
  assert.equal(
    sourceUrl({ repo: "a/b", branch: "main", source: "src/content/docs/x.md:3", generated: ["src/content/docs/"] }),
    null,
    "generated files are not in this repository",
  );
});

test("issueUrl prefills a new issue with the page, source and build", async () => {
  const { issueUrl } = await import("./logic.mjs");
  const url = new URL(issueUrl({
    repo: "apache/magpie-site",
    branch: "main",
    pageUrl: "https://magpie.apache.org/tools/",
    source: "src/pages/tools.astro:12",
    sha: "abc1234",
  }));
  assert.equal(url.origin + url.pathname, "https://github.com/apache/magpie-site/issues/new");
  assert.equal(url.searchParams.get("title"), "Feedback on /tools/");
  const body = url.searchParams.get("body");
  assert.match(body, /\*\*Page:\*\* https:\/\/magpie\.apache\.org\/tools\//);
  assert.match(body, /blob\/main\/src\/pages\/tools\.astro#L12/);
  assert.match(body, /abc1234/);
  assert.match(body, /paste it here/);
});

test("issueUrl leaves the source out when it cannot be linked", async () => {
  const { issueUrl } = await import("./logic.mjs");
  const body = new URL(issueUrl({ repo: "a/b", branch: "main", pageUrl: "https://x.org/", source: null }))
    .searchParams.get("body");
  assert.doesNotMatch(body, /Source/);
});
