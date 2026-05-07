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
# Agents Guidelines

## Running tests

Use `uv run` for all commands run during tests. For example:

```bash
uv run pytest
```

## Commit messages

Do not add `Co-Authored-By` headers in commit messages. Instead, use a `Generated-by` trailer
following the guidelines of the ASF:

```
Generated-by: <Agent information>
```

## Brevity in commits and PRs

Default to short. The diff and the linked issue carry the detail — the message is for the *why*,
not a recap of every step.

**Commit messages.** Subject under 70 chars, imperative voice, names the area touched
(`verify-action-build: …`, `analyze-action-pr: …`). Body is rarely longer than 5–10 lines; only as
long as it needs to be to explain the why.

**PR titles.** Same rules as commit subjects.

**PR bodies.** Lead with what changed in 2–4 bullets, then a brief test plan. Skip restating
the commit message — reviewers read both.

Cut:
- Recaps of what didn't work or how the diagnosis evolved.
- Per-file change inventories (the diff already says it).
- "Note: …" appendices for tangential observations — open a follow-up issue instead.
- Multi-paragraph backstories when one sentence covers it.

Keep:
- The why, once, plainly.
- A reproducer or fixture path for non-obvious bugs.
- Links to related issues / PRs.

## Pull requests

Always use `--web` when creating PRs (e.g. `gh pr create --web ...`). This opens the PR in the
browser and gives the author a chance to review the title, description, and diff before submitting.
Do not create PRs directly from the CLI without `--web`.

### PR templates

This repository uses multiple PR templates located in `.github/PULL_REQUEST_TEMPLATE/`:

- **`action_approval.md`** — Use for requests to add a new GitHub Action to the allow list. Includes
  fields for the action name, URL, pinned version hash, permissions, related actions, and a review
  checklist.
- **`code_change.md`** — Use for all other changes: new utilities, bug fixes, enhancements, workflow
  or CI changes, and documentation updates.

When creating a PR via `gh pr create --web`, GitHub will present a template chooser. Select the
template that matches the type of change. When opening a PR URL directly, you can append
`&template=action_approval.md` or `&template=code_change.md` to pre-fill the appropriate template.

## Documentation

When you add, change, or remove a user-visible feature, workflow, script, or flag, update the
corresponding reference documentation in the same PR. At minimum this means the relevant section of
`README.md`; check other `*.md` files in the area you touched for stale references as well. A PR
that introduces a new workflow in `.github/workflows/`, a new utility under `utils/`, or a new CLI
flag is not complete until the docs describe it — reviewers should not have to ask "is this
documented?".

## License headers

All files must include the Apache License 2.0 header where the file format supports it. Use the
appropriate comment syntax for the file type (e.g., `<!-- -->` for Markdown/HTML, `#` for YAML/Python,
`//` for JavaScript/Go). See existing files in the repository for examples of the correct format.
