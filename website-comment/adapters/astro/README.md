<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->
# Astro adapter

A Vite plugin that stamps every JSX host element (lowercase tag) with
`data-preview-src="<repo-relative path>:<line>"` in a preview build. It parses
each `.tsx`/`.jsx` file with the site's own Vite parser (`parseSync`, oxc)
before the JSX transform and inserts the attribute into the opening tag, so
line mappings still hold.

Enable it with [`preview-annotate`](../../preview-annotate/action.yml) and
`generator: astro`, whose `vite-plugin` output is this plugin's path. Pass
it to the preview build as `PREVIEW_VITE_PLUGIN`, with `PREVIEW_ANNOTATE=1`,
for `astro.config.mjs` to load — see the [Astro path](../../README.md#astro).
The config passes the site's `vite` module in: this file is loaded from the
action's checkout, which has no `node_modules` of its own. It needs Vite 8 or
later, and neither Babel nor `@astrojs/react` options — since `@astrojs/react`
7 the React integration no longer runs Babel.

Paths are relative to the repository root: `PREVIEW_SRC_ROOT` when set,
else the nearest directory above the build holding `.git`, as the Jekyll
adapter does. A site in a subdirectory gets the same paths as the pull
request's diff.

Left alone: component elements (`<Thing />`), whose host elements are stamped
in their own file; member-expression tags (`<motion.div>`); an element that
already carries `data-preview-src`; files under `node_modules`; and a file
that fails to parse, which passes through unchanged. `.astro` templates and
Markdown content are compiled by Astro, so a region there falls back to the
pull request's Conversation tab.

Tests: `npm test` in `website-comment/`.
