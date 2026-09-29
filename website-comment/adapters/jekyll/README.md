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

# Jekyll adapter

Makes a Jekyll site's preview build stamp `data-preview-src="<path>:<line>"` on
rendered elements, so the review overlay can take a reviewer from a marked
region to the source line in the pull request's diff. See the
[overview](../../README.md) for how this fits the rest of the preview
system.

## What gets stamped

| Source | How | Granularity |
|---|---|---|
| Markdown pages and collection documents | From the source line kramdown records for each block, shifted past the front matter | Headings, paragraphs, list items, blockquotes, tables and rows, code blocks, definition lists, embedded HTML blocks |
| Layouts (`_layouts/`) | Each opening HTML tag of the raw template, before Liquid runs | Every tag outside `html`, `head`, `body`, `meta`, `link`, `script`, `style`, `title`, `base`, `noscript`, `template` |
| Includes (`include`, `include_relative`) | Same as layouts | Same as layouts |

Left alone: Liquid output (`{{ "<b>" }}`), `{% raw %}`, `{% comment %}` and
`{% highlight %}` blocks, `<script>`, `<style>` and HTML comments, Markdown
rendered by `markdownify` inside a layout, and files from a theme gem, which a
pull request cannot change.

Paths are relative to the repository root: the nearest ancestor of the site
source holding `.git`, or `PREVIEW_SRC_ROOT` when set.

**Known limit.** A Liquid tag inside a Markdown page that expands to several
lines shifts the line of every block after it on that page. The file is still
right, and the screenshot's caption names it.

## Using it

Through the [`preview-annotate`](../../preview-annotate/action.yml) action
with `generator: jekyll` — see the [Jekyll path](../../README.md#jekyll) and
the [Quick start](../../README.md#quick-start). The action runs
`prepare.sh <site source>`, which copies `preview_src.rb` into the site's
plugins directory (`plugins_dir`, default `_plugins`) for the preview build
only. It refuses a site with `safe: true` — which the `github-pages` gem
forces — because safe mode ignores `_plugins` and the build would silently
stamp nothing.

Running `prepare.sh` by hand works too; delete the copied file afterwards,
since anything built while it is there is annotated.

## Tests

```sh
export BUNDLE_GEMFILE=website-comment/adapters/jekyll/test/Gemfile
bundle install
bundle exec ruby website-comment/adapters/jekyll/test/preview_src_test.rb
```

The test builds `test/fixture/` with the plugin loaded and checks the stamped
lines. CI runs it in `.github/workflows/website-comment-test.yml`. Verified against
Jekyll 4.4.1 and 4.3.4.
