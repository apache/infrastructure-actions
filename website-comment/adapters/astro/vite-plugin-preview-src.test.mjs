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
import * as vite from "vite";
import previewSrc, { stampSource } from "./vite-plugin-preview-src.mjs";

const run = (code, filename = "/repo/src/components/Thing.tsx") =>
  stampSource(code, filename, "/repo", vite);

test("stamps a host element with its repo-relative path and line", () => {
  const out = run("const a = <div>hi</div>;");
  assert.match(out, /data-preview-src="src\/components\/Thing\.tsx:1"/);
});

test("leaves component elements alone", () => {
  // A component renders host elements of its own, which get stamped there.
  const out = run("const a = <Thing prop={1} />;");
  assert.doesNotMatch(out, /data-preview-src/);
});

test("does not overwrite an existing attribute", () => {
  const out = run('const a = <div data-preview-src="kept" />;');
  assert.match(out, /data-preview-src="kept"/);
  assert.equal(out.match(/data-preview-src/g).length, 1);
});

test("records the line each element starts on", () => {
  const out = run("const a = (\n  <div>\n    <span>x</span>\n  </div>\n);");
  assert.match(out, /data-preview-src="src\/components\/Thing\.tsx:2"/);
  assert.match(out, /data-preview-src="src\/components\/Thing\.tsx:3"/);
});

test("parses TypeScript and inserts at the right place after non-ASCII text", () => {
  const out = run("const s: string = 'é—✓';\nconst a = <p title={s}>naïve</p>;");
  assert.ok(out.includes('<p data-preview-src="src/components/Thing.tsx:2" title={s}>'), out);
});

test("leaves unparsable source untouched", () => {
  const code = "const a = <div>;";
  assert.equal(run(code), code);
});

test("the plugin transforms through the site's Vite, and refuses to run without it", () => {
  const plugin = previewSrc({ vite, root: "/repo" });
  const out = plugin.transform.handler("const a = <div />;", "/repo/src/A.jsx?v=1");
  assert.match(out.code, /data-preview-src="src\/A\.jsx:1"/);
  assert.throws(() => previewSrc({ root: "/repo" }), /needs \{ vite \}/);
});
