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
import { mkdtemp, mkdir, writeFile, readFile, readdir, access } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { prepareTree, redactToken, redactError } from "./git.mjs";

const missing = async (p) => {
  try {
    await access(p);
    return false;
  } catch {
    return true;
  }
};

test("redactToken removes every occurrence", () => {
  const msg = "Command failed: git push https://x-access-token:SEKRET@github.com/x SEKRET";
  assert.equal(redactToken(msg, "SEKRET").includes("SEKRET"), false);
  assert.match(redactToken(msg, "SEKRET"), /\*\*\*/);
});

test("redactToken tolerates an empty token and null text", () => {
  assert.equal(redactToken("plain", ""), "plain");
  assert.equal(redactToken(null, "t"), "");
});

test("prepareTree deletes a .git directory shipped inside the artifact", async () => {
  // git init would REINITIALISE this, keeping its hooks, and the commit would
  // then run them in the job holding the push token.
  const content = await mkdtemp(join(tmpdir(), "preview-content-"));
  await mkdir(join(content, ".git", "hooks"), { recursive: true });
  await writeFile(join(content, ".git", "hooks", "pre-commit"), "#!/bin/sh\necho pwned\n");
  await writeFile(join(content, "index.html"), "<h1>site</h1>");

  const dir = await mkdtemp(join(tmpdir(), "preview-tree-"));
  await prepareTree({ dir, files: { "robots.txt": "User-agent: *\nDisallow: /\n" }, contentDir: content });

  assert.ok(await missing(join(dir, ".git")), ".git must not survive into the commit");
  assert.deepEqual((await readdir(dir)).sort(), ["index.html", "robots.txt"]);
});

test("prepareTree deletes a .gitignore shipped inside the artifact", async () => {
  // Otherwise `git add` would silently drop our generated files.
  const content = await mkdtemp(join(tmpdir(), "preview-content-"));
  await writeFile(join(content, ".gitignore"), "robots.txt\n.asf.yaml\n");
  await writeFile(join(content, "index.html"), "<h1>site</h1>");

  const dir = await mkdtemp(join(tmpdir(), "preview-tree-"));
  await prepareTree({ dir, files: { "robots.txt": "Disallow: /\n" }, contentDir: content });

  assert.ok(await missing(join(dir, ".gitignore")));
  assert.equal(await readFile(join(dir, "robots.txt"), "utf8"), "Disallow: /\n");
});

test("generated files win over files of the same name in the artifact", async () => {
  const content = await mkdtemp(join(tmpdir(), "preview-content-"));
  await writeFile(join(content, ".asf.yaml"), "staging:\n  profile: attacker\n");

  const dir = await mkdtemp(join(tmpdir(), "preview-tree-"));
  await prepareTree({ dir, files: { ".asf.yaml": "staging:\n  profile: pr5\n" }, contentDir: content });

  assert.match(await readFile(join(dir, ".asf.yaml"), "utf8"), /profile: pr5/);
});

test("prepareTree strips preview-meta.json", async () => {
  const content = await mkdtemp(join(tmpdir(), "preview-content-"));
  await writeFile(join(content, "preview-meta.json"), '{"pr":5}');
  await writeFile(join(content, "index.html"), "<h1>site</h1>");

  const dir = await mkdtemp(join(tmpdir(), "preview-tree-"));
  await prepareTree({ dir, files: {}, contentDir: content });

  assert.ok(await missing(join(dir, "preview-meta.json")));
});

test("strips a .git directory nested below the root", async () => {
  const content = await mkdtemp(join(tmpdir(), "preview-content-"));
  await mkdir(join(content, "sub", ".git", "hooks"), { recursive: true });
  await writeFile(join(content, "sub", ".git", "hooks", "pre-commit"), "#!/bin/sh\n");
  await writeFile(join(content, "sub", "page.html"), "<h1>kept</h1>");

  const dir = await mkdtemp(join(tmpdir(), "preview-tree-"));
  await prepareTree({ dir, files: {}, contentDir: content });

  assert.ok(await missing(join(dir, "sub", ".git")), "a nested .git must not survive");
  assert.equal(await readFile(join(dir, "sub", "page.html"), "utf8"), "<h1>kept</h1>");
});

test("redactError scrubs the token from every field that can carry argv", () => {
  const remote = "https://x-access-token:SEKRET@h/r";
  const err = Object.assign(new Error(`Command failed: git push ${remote}`), {
    cmd: `git push ${remote}`,
    stderr: `fatal: unable to access '${remote}'`,
    path: "git",
    spawnargs: ["push", "-f", remote, "preview/pr5-staging"],
  });

  redactError(err, "SEKRET");

  for (const f of ["message", "cmd", "stderr"]) {
    assert.equal(String(err[f]).includes("SEKRET"), false, `${f} must not carry the token`);
  }
  assert.equal(
    err.spawnargs.some((a) => String(a).includes("SEKRET")),
    false,
    "spawnargs must not carry the token",
  );
  assert.deepEqual(err.spawnargs.length, 4, "redaction must not drop arguments");
});

test("prepareTree writes generated files into nested directories", async () => {
  const dir = await mkdtemp(join(tmpdir(), "preview-tree-"));
  await prepareTree({ dir, files: { "_preview/review.js": "// hi\n" } });

  assert.equal(await readFile(join(dir, "_preview", "review.js"), "utf8"), "// hi\n");
});
