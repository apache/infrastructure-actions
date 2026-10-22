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

import { existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

/**
 * The directory stamped paths are relative to, as the Jekyll adapter finds it:
 * PREVIEW_SRC_ROOT when set, else the nearest ancestor of `start` holding .git
 * (a directory, or a file in a worktree), else `start`. Paths must match the
 * repository-relative ones the diff anchors use, also for a site in a
 * subdirectory.
 */
export function repoRoot(start = process.cwd(), env = process.env) {
  if (env.PREVIEW_SRC_ROOT) return resolve(env.PREVIEW_SRC_ROOT);
  for (let dir = resolve(start); ; dir = dirname(dir)) {
    if (existsSync(join(dir, ".git"))) return dir;
    if (dirname(dir) === dir) return resolve(start);
  }
}

/**
 * Stamps every JSX host element with the source file and line it came from, so
 * a preview's overlay can resolve a marked region back to a diff line.
 *
 * Only host elements (lowercase names) are stamped: a component element renders
 * host elements of its own, and those carry the location the reviewer can act
 * on. Active only when the build sets PREVIEW_ANNOTATE=1 — the
 * production build must emit none of this.
 *
 * This is a Vite plugin running before the JSX transform, so it only sees JSX:
 * `.tsx` and `.jsx` files. `.astro` templates and Markdown content are
 * never annotated — that would need the Astro compiler.
 *
 * `vite` is the site's own Vite module (`import * as vite from "vite"`). This
 * file is loaded from the action's checkout, which has no node_modules, so it
 * cannot import Vite itself; it uses the parser the site already builds with.
 */
export default function previewSrc({ vite, root = repoRoot() } = {}) {
  if (typeof vite?.parseSync !== "function" || typeof vite?.Visitor !== "function") {
    throw new TypeError("previewSrc needs { vite }: the site's `import * as vite from \"vite\"` (Vite 8 or later)");
  }
  return {
    name: "preview-src",
    enforce: "pre",
    transform: {
      filter: { id: { include: /\.[jt]sx(?:$|\?)/, exclude: /[/\\]node_modules[/\\]/ } },
      handler(code, id) {
        const stamped = stampSource(code, id.split("?")[0], root, vite);
        // Attributes are inserted within a line, so line mappings still hold.
        return stamped === code ? null : { code: stamped, map: null };
      },
    },
  };
}

export function stampSource(code, filename, root, { parseSync, Visitor }) {
  const lang = filename.endsWith(".jsx") ? "jsx" : "tsx";
  const { program, errors } = parseSync(filename, code, { lang });
  if (errors.length) return code;

  const lineStarts = [0];
  for (let i = 0; i < code.length; i++) if (code[i] === "\n") lineStarts.push(i + 1);
  const lineOf = (offset) => {
    let lo = 0;
    let hi = lineStarts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (lineStarts[mid] <= offset) lo = mid;
      else hi = mid - 1;
    }
    return lo + 1;
  };

  const file = relative(root, filename);
  const inserts = [];
  new Visitor({
    JSXOpeningElement(node) {
      if (node.name.type !== "JSXIdentifier") return;
      if (!/^[a-z]/.test(node.name.name)) return;
      const already = node.attributes.some(
        (a) => a.type === "JSXAttribute" && a.name.name === "data-preview-src",
      );
      if (already) return;
      inserts.push([node.name.end, ` data-preview-src="${file}:${lineOf(node.start)}"`]);
    },
  }).visit(program);

  let out = code;
  for (const [at, text] of inserts.sort((a, b) => b[0] - a[0])) {
    out = out.slice(0, at) + text + out.slice(at);
  }
  return out;
}
