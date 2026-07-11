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

# Releasing the actions

This repository is a *monorepo of actions*: several independently-consumed
actions are served from one repo. To let downstream projects pin a specific
version — and let Dependabot propose bumps — each action is released under its
own **path-prefixed tag**.

## Tag scheme

The tag prefix is the action's **leaf directory name**, which is also the
prefix a consumer repeats in the `@ref`:

| Action (consumed path)                          | Tag prefix       | Example tag              |
| ----------------------------------------------- | ---------------- | ------------------------ |
| `allowlist-check`                               | `allowlist-check`| `allowlist-check/v1.2.3` |
| `pelican`                                       | `pelican`        | `pelican/v1.2.3`         |
| `stash/save`                                    | `save`           | `save/v1.2.3`            |
| `stash/restore`                                 | `restore`        | `restore/v1.2.3`         |

For every `X.Y.Z` release we also move a **major** tag (`<prefix>/vN`) to the
same commit, so consumers can track a major line if they prefer.

This leaf-name prefix scheme is the format Dependabot's `github_actions`
ecosystem understands for monorepos
([dependabot/dependabot-core#11286][11286], added specifically for this repo).
Dependabot filters candidate tags by the prefix, so `save/*` and `restore/*`
bump independently.

[11286]: https://github.com/dependabot/dependabot-core/pull/11286

## How a release happens (automatic)

Releases are cut automatically by
[`.github/workflows/release-actions.yml`](.github/workflows/release-actions.yml)
on every push to `main` that touches an action's files. The workflow runs
[`scripts/release_actions.py`](scripts/release_actions.py), which:

1. Diffs the pushed range and maps changed files to the affected action(s). A
   change under `stash/shared/` releases **both** stash actions, since they
   import that shared code at runtime.
2. Picks the bump type (see below).
3. For each affected action, computes the next version from the newest
   existing `<prefix>/vX.Y.Z` tag (the first release seeds `v1.0.0`), creates
   the annotated tag, moves the `<prefix>/vN` major tag and publishes a GitHub
   Release with auto-generated notes.

### Choosing the bump

The bump defaults to **patch**. Raise or suppress it per PR:

| Signal (PR label or commit-message token) | Effect             |
| ----------------------------------------- | ------------------ |
| _none_                                    | `patch` (default)  |
| `release:minor` / `[minor]`               | `minor`            |
| `release:major` / `[major]`               | `major`            |
| `release:skip` / `[skip release]`         | no release cut     |

Labels take precedence over commit-message tokens; `release:major` wins over
`release:minor`.

## Cutting a release manually

Use the **Run workflow** button on the *Release actions* workflow
(`workflow_dispatch`) to release out-of-band — for example to seed the very
first tags or to force a specific bump:

- **action** — a single tag prefix (`allowlist-check`, `pelican`, `save`,
  `restore`). Leave empty to auto-detect from the latest commit.
- **bump** — `patch`, `minor` or `major`.

## Testing the release logic

The version math and changed-action detection are pure functions with unit
tests:

```bash
uv run pytest scripts/test_release_actions.py
```

A dry run (no `--apply`, so nothing is tagged or pushed) prints what *would*
be released:

```bash
python3 scripts/release_actions.py \
  --repo apache/infrastructure-actions --action restore --bump minor
```
