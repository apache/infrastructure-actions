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

When all refs pass:

```
All 15 unique action refs are on the ASF allowlist
```

## Dependencies

- Python 3 (pre-installed on GitHub-hosted runners)
- ruyaml (installed automatically by the action)
