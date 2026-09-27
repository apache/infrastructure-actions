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

A Babel plugin, wired through `@astrojs/react`, that stamps every JSX host
element (lowercase tag) with `data-preview-src="<repo-relative path>:<line>"`
in a preview build.

Enable it with [`preview-annotate`](../../preview-annotate/action.yml) and
`generator: astro`, whose `babel-plugin` output is this plugin's path. Pass
it to the preview build as `PREVIEW_BABEL_PLUGIN`, with `PREVIEW_ANNOTATE=1`,
for `astro.config.mjs` to read — see the [Astro path](../../README.md#astro).

Coverage: `.tsx` and `.jsx` only. `.astro` templates and Markdown content are
compiled by Astro, not Babel, so a region there falls back to the pull
request's Conversation tab.

Tests: `npm test` in `pr-preview/`.
