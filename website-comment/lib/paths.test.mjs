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
import { parsePatterns, patternRegExp, touchesPaths } from "./paths.mjs";

const files = (...names) => names.map((filename) => ({ filename }));

test("patterns are one per line, without blanks or comments", () => {
  assert.deepEqual(parsePatterns("src/\n\n  # the config\n astro.config.* \n"), ["src/", "astro.config.*"]);
  assert.deepEqual(parsePatterns(""), []);
  assert.deepEqual(parsePatterns(undefined), []);
});

test("a trailing slash matches everything under the directory, and nothing else", () => {
  const re = patternRegExp("src/");
  assert.ok(re.test("src/pages/index.astro"));
  assert.ok(re.test("src/x"));
  assert.ok(!re.test("src"));
  assert.ok(!re.test("srcs/x"));
  assert.ok(!re.test("docs/src/x"));
});

test("* and ? stay within one segment; ** crosses them", () => {
  assert.ok(patternRegExp("astro.config.*").test("astro.config.mjs"));
  assert.ok(!patternRegExp("*.json").test("src/data/tools.json"));
  assert.ok(patternRegExp("**/*.json").test("src/data/tools.json"));
  assert.ok(patternRegExp("**/*.json").test("package.json"), "**/ matches no directory too");
  assert.ok(patternRegExp("src/**/x.astro").test("src/x.astro"));
  assert.ok(patternRegExp("v?.txt").test("v1.txt"));
  assert.ok(!patternRegExp("v?.txt").test("v/.txt"));
});

test("a plain path matches only itself: dots and brackets are literal", () => {
  const re = patternRegExp("package.json");
  assert.ok(re.test("package.json"));
  assert.ok(!re.test("packageXjson"));
  assert.ok(!re.test("sub/package.json"));
  assert.ok(patternRegExp("a[1].md").test("a[1].md"));
});

test("a pull request touches the site when any changed file matches", () => {
  const site = ["src/", "package.json"];
  assert.equal(touchesPaths(files(".github/workflows/build.yml", "src/a.astro"), site), true);
  assert.equal(touchesPaths(files(".github/workflows/build.yml", ".asf.yaml"), site), false);
  assert.equal(touchesPaths([], site), false);
});

test("a rename counts under its old name too", () => {
  const moved = [{ filename: "attic/a.astro", previous_filename: "src/a.astro" }];
  assert.equal(touchesPaths(moved, ["src/"]), true);
});

test("no patterns means every change touches the site", () => {
  assert.equal(touchesPaths(files(".asf.yaml"), []), true);
  assert.equal(touchesPaths([], []), true);
});
