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
import { mkdtemp, writeFile, readFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { injectMain, parseGenerated } from "./inject-main.mjs";

test("injects the overlay in main mode into every page", async () => {
  const dir = await mkdtemp(join(tmpdir(), "inject-main-"));
  await mkdir(join(dir, "docs"));
  await writeFile(join(dir, "index.html"), "<html><body><h1>x</h1></body></html>");
  await writeFile(join(dir, "docs", "index.html"), "<html><body>docs</body></html>");

  await injectMain({
    dir,
    repo: "apache/magpie-site",
    sha: "abcdef1234567",
    generated: ["src/content/docs/"],
  });

  for (const page of ["index.html", "docs/index.html"]) {
    assert.match(await readFile(join(dir, page), "utf8"), /_preview\/review\.js/);
  }
  const review = await readFile(join(dir, "_preview", "review.js"), "utf8");
  const config = JSON.parse(/window\.__ASF_PREVIEW__ = (.*);\n/.exec(review)[1]);
  assert.deepEqual(config, {
    repo: "apache/magpie-site",
    mode: "main",
    branch: "main",
    sha: "abcdef1",
    generated: ["src/content/docs/"],
  });
  assert.ok(!("pr" in config), "main mode has no pull request, and so no preview banner");
  assert.match(review, /function issueUrl/, "the logic is inlined");
  await readFile(join(dir, "_preview", "html2canvas-pro.min.js"), "utf8");
});

test("parseGenerated accepts lines or commas and drops blanks", () => {
  assert.deepEqual(parseGenerated("a/\n b/ ,c/\n\n"), ["a/", "b/", "c/"]);
  assert.deepEqual(parseGenerated(""), []);
  assert.deepEqual(parseGenerated(undefined), []);
});
