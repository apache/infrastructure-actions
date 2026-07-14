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

# ASF Allowlist Check

A composite GitHub Action that verifies all `uses:` refs in a project's workflow files are on the ASF Infrastructure [approved allowlist](../approved_patterns.yml). Catches violations **before merge**, preventing the silent CI failures that occur when an action is not on the org-level allowlist (see [#574](https://github.com/apache/infrastructure-actions/issues/574)).

## Why

When a GitHub Actions workflow references an action that isn't on the ASF org-level allowlist, the CI job silently fails with "Startup failure" — no logs, no notifications, and the PR may appear green because no checks ran. This action catches those problems at PR time with a clear error message.

## Usage

Add a workflow file to your project (e.g., `.github/workflows/asf-allowlist-check.yml`):

```yaml
name: "ASF Allowlist Check"

on:
  workflow_dispatch:
  pull_request:
    paths:
      - ".github/**"
  push:
    branches:
      - main
    paths:
      - ".github/**"

permissions:
  contents: read

jobs:
  asf-allowlist-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: apache/infrastructure-actions/allowlist-check@main
```

That's it — two steps. The `actions/checkout` step checks out your repo so `.github/` is available to scan, then the allowlist check runs against those files.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `scan-glob` | No | `.github/**/*.yml` | Glob pattern for YAML files to scan for action refs |
| `expiry-warning-days` | No | `30` | Emit a non-failing warning when a workflow pins an allowlisted action whose approved version expires within this many days. Set to `0` to warn only once a pin has actually expired. |

### Custom scan glob

To scan only workflow files (excluding other YAML under `.github/`):

```yaml
- uses: apache/infrastructure-actions/allowlist-check@main
  with:
    scan-glob: ".github/workflows/*.yml"
```

## What it checks

The action scans all matching YAML files for `uses:` keys and validates each action ref against the [approved_patterns.yml](../approved_patterns.yml) allowlist.

### Automatically allowed

Actions from these GitHub organizations are implicitly trusted and don't need to be in the allowlist:
- `actions/*` — GitHub's official actions
- `github/*` — GitHub's own actions
- `apache/*` — ASF's own actions

### Skipped

- **Local refs** (`./`) — paths within the same repo are not subject to the org allowlist
- **Docker refs** (`docker://`) — container actions pulled directly from a registry
- **Empty YAML files** — skipped
- **Malformed YAML files** — fails with an error

### Violation output

When violations are found, the action fails with exit code 1 and prints:

```
::error::Found 2 action ref(s) not on the ASF allowlist:
::error file=.github/workflows/ci.yml::some-org/some-action@v1 is not on the ASF allowlist
::error file=.github/workflows/release.yml::other-org/other-action@abc123 is not on the ASF allowlist
```

To resolve a violation, open a PR in this repo to [add the action](../README.md#adding-a-new-action-to-the-allow-list) or [add a new version](../README.md#adding-a-new-version-to-the-allow-list) to the allowlist.

When all refs pass:

```
All 15 unique action refs are on the ASF allowlist
```

### Expiry warnings

Approved *versions* of an action don't live forever: when a newer version is
approved, older pinned SHAs are given an `expires_at` date (a grace period,
typically three months) and are eventually removed from the allowlist. Once a
pin is removed, workflows still using it start failing the allowlist check.

To give projects advance notice, the action also fetches
[`actions.yml`](../actions.yml) (which carries the `expires_at` metadata) and
emits a **non-failing** `::warning::` for any allowlisted pin in your workflows
that expires within `expiry-warning-days` (default 30):

```
1 allowlisted ref(s) expiring within 30 day(s) -- bump these to a newer approved version before they are removed:
::warning file=.github/workflows/ci.yml::some-org/some-action@abc123 is allowlisted but its approved pin expires on 2026-07-25 (in 12 day(s)); bump to a newer approved version to avoid a future CI failure.
```

Warnings never change the exit code — they surface in the job log and the PR
"Files changed" annotations so you can bump the pin before it breaks. This is
best-effort: if `actions.yml` can't be fetched, the check still runs, just
without expiry warnings.

```yaml
- uses: apache/infrastructure-actions/allowlist-check@main
  with:
    expiry-warning-days: "60"   # start warning two months ahead
```

## Dependencies

- Python 3 (pre-installed on GitHub-hosted runners)
- ruyaml (installed automatically by the action)
