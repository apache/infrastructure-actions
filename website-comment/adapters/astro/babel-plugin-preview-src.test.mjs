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
import { transformSync } from "@babel/core";
import plugin from "./babel-plugin-preview-src.mjs";

const run = (code, filename = "/repo/src/components/Thing.tsx") =>
  transformSync(code, {
    filename,
    root: "/repo",
    plugins: [[plugin, { root: "/repo" }]],
    parserOpts: { plugins: ["jsx", "typescript"] },
    configFile: false,
    babelrc: false,
  }).code;

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
