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

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, rm, cp, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

const run = promisify(execFile);

/**
 * Remove a credential from text before it can reach a log.
 *
 * execFile's rejection message contains the whole argv, and the remote URL
 * carries the token. publish.mjs prints err.message on failure, so without this
 * a push failure puts a push-capable token into a public Actions log. Actions'
 * own secret masking only covers registered secrets, so it cannot be relied on.
 */
export function redactToken(text, token) {
  if (!token) return String(text ?? "");
  return String(text ?? "").replaceAll(token, "***");
}

/**
 * Strip a token from every field of a child-process rejection that can reach a
 * log. `spawnargs` is an ARRAY of raw arguments, and a spawn-level failure
 * (ENOENT and friends) populates it with the tokenised remote URL — so a string
 * sweep over the scalar fields is not enough on its own.
 */
export function redactError(err, token) {
  for (const field of ["message", "stderr", "stdout", "cmd", "path", "stack"]) {
    if (err?.[field]) err[field] = redactToken(err[field], token);
  }
  if (Array.isArray(err?.spawnargs)) {
    err.spawnargs = err.spawnargs.map((arg) => redactToken(arg, token));
  }
  return err;
}

/**
 * Remove every .git and .gitignore ANYWHERE in the tree, not just at the root.
 *
 * A nested sub/.git is not an execution vector — git will not run a nested
 * repo's hooks — but `git add` records `sub` as a gitlink, so the subtree
 * silently vanishes from the published preview and its target SHA is chosen by
 * whoever built the artifact.
 */
async function stripGitMetadata(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.name === ".git" || entry.name === ".gitignore" || entry.name === ".gitmodules") {
      await rm(full, { recursive: true, force: true });
      continue;
    }
    // Symlinked directories are already refused at the archive level; do not
    // follow one here either.
    if (entry.isDirectory() && !entry.isSymbolicLink()) await stripGitMetadata(full);
  }
}

/**
 * Build the tree to be committed, and strip everything the pull request could
 * have smuggled into its own artifact.
 *
 * Exported so the stripping is testable without git or a network.
 */
export async function prepareTree({ dir, files, contentDir = null }) {
  if (contentDir) await cp(contentDir, dir, { recursive: true });

  // The artifact is pull-request-controlled. `git init` REINITIALISES an
  // existing .git directory, keeping its hooks and its config, and the commit
  // below would then execute them in the job holding the push token. A
  // .gitignore would silently drop our generated .asf.yaml and robots.txt from
  // the commit, defeating both the noindex control and the generated-config
  // defence. Neither is caught by the entry-name screen or the symlink walk.
  await stripGitMetadata(dir);

  // Generated files are written AFTER the copy, so ours always win over any
  // file of the same name shipped inside the artifact.
  for (const [name, body] of Object.entries(files)) {
    const target = join(dir, name);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, body);
  }

  // Never publish the metadata the publisher validated against.
  await rm(join(dir, "preview-meta.json"), { force: true });
}

export function createGit({ repo, token }) {
  const remote = `https://x-access-token:${token}@github.com/${repo}.git`;

  // Hooks and ignore files come from the artifact's tree, so every git
  // invocation neutralises both.
  const SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.excludesFile=/dev/null"];

  async function git(args) {
    try {
      return await run("git", args);
    } catch (err) {
      throw redactError(err, token);
    }
  }

  return {
    /** Force-push an orphan commit containing contentDir plus generated files. */
    async pushTree(branch, files, message, contentDir = null) {
      const dir = await mkdtemp(join(tmpdir(), "preview-push-"));
      try {
        await prepareTree({ dir, files, contentDir });

        await git([...SAFE, "init", "-q", dir]);
        await git(["-C", dir, ...SAFE, "checkout", "-q", "-b", branch]);
        await git(["-C", dir, "config", "user.name", "github-actions[bot]"]);
        await git(["-C", dir, "config", "user.email", "github-actions[bot]@users.noreply.github.com"]);
        await git(["-C", dir, ...SAFE, "add", "-A", "-f"]);
        await git([
          "-C", dir, ...SAFE, "commit", "-q", "--no-verify",
          "-m", `${message}\n\nGenerated-by: preview-publish\n`,
        ]);
        await git(["-C", dir, ...SAFE, "push", "-f", remote, branch]);
      } finally {
        // Always, including on failure: this tree holds the whole built site.
        await rm(dir, { recursive: true, force: true });
      }
    },
  };
}
