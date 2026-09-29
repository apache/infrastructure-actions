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

import { relative } from "node:path";

/**
 * Stamps every JSX host element with the source file and line it came from, so
 * a preview's overlay can resolve a marked region back to a diff line.
 *
 * Only host elements (lowercase names) are stamped: a component element renders
 * host elements of its own, and those carry the location the reviewer can act
 * on. Active only when the build sets PREVIEW_ANNOTATE=1 — the
 * production build must emit none of this.
 *
 * This is a Babel plugin wired through @astrojs/react, so it only sees JSX:
 * `.tsx` and `.jsx` files. `.astro` templates and Markdown content are
 * never annotated — that would need the Astro compiler, not Babel.
 */
export default function previewSrc({ types: t }) {
  return {
    name: "preview-src",
    visitor: {
      JSXOpeningElement(path, state) {
        const name = path.node.name;
        if (name.type !== "JSXIdentifier") return;
        if (!/^[a-z]/.test(name.name)) return;

        const already = path.node.attributes.some(
          (a) => a.type === "JSXAttribute" && a.name?.name === "data-preview-src",
        );
        if (already) return;

        const line = path.node.loc?.start?.line;
        if (!line) return;

        const root = state.opts?.root ?? state.file.opts.root ?? process.cwd();
        const file = relative(root, state.filename ?? state.file.opts.filename ?? "");

        path.node.attributes.push(
          t.jsxAttribute(
            t.jsxIdentifier("data-preview-src"),
            t.stringLiteral(`${file}:${line}`),
          ),
        );
      },
    },
  };
}
