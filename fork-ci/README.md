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
# Fork CI

A composite GitHub Action that mirrors a public fork workflow as an informational
check on an upstream pull request. The check links to the contributor's run for
the current PR branch and commit. This helps projects reduce duplicate CI work
on shared runners.

**Keep the fork check informational.** Contributors control their fork workflows.
Require full upstream validation before merge, such as a merge queue that tests
the merged result. This action reports fork results. Each project controls which
upstream jobs to skip.

- [Usage](#usage)
- [Inputs](#inputs)
- [Outputs](#outputs)
- [How it works](#how-it-works)
- [Security and permissions](#security-and-permissions)
- [Contributor setup](#contributor-setup)
- [Development](#development)

## Usage

Enable `push` and `workflow_dispatch` events in the workflow to mirror, such as
`.github/workflows/ci.yml`. Make its fork jobs work without upstream secrets,
repository variables, or private runner groups.

Copy [fork-ci-signal.yml](examples/fork-ci-signal.yml) to `.github/workflows/` in
your repository. It runs a lightweight **Fork CI signal** workflow for PR events.
Then add `.github/workflows/fork-ci-status.yml`:

```yaml
name: Fork CI status
on:
  workflow_run:
    workflows: [Fork CI signal]
    types: [completed]
  schedule:
    - cron: '7,22,37,52 * * * *'
  workflow_dispatch:
permissions:
  actions: read
  checks: write
  pull-requests: read
concurrency:
  group: fork-ci-status
  cancel-in-progress: false
jobs:
  bridge:
    if: github.repository == 'apache/example'
    runs-on: ubuntu-slim
    timeout-minutes: 10
    steps:
      - uses: apache/infrastructure-actions/fork-ci@<sha> # fork-ci/v1.0.0
        with:
          workflow: ci.yml
          check-name: CI (fork)
          base-branches: main
          sweep-minutes: '15'
```

Replace `apache/example`, `workflow`, and `base-branches` with your project's values.
After publication, replace `<sha>` with the reviewed commit of the release tag.
The `fork-ci/vX.Y.Z` and `fork-ci/vN` tags become available with the first release.
See [Versioning and Pinning Actions](../README.md#versioning-and-pinning-actions)
and [Releasing the actions](../RELEASING.md).

The [bridge example](examples/fork-ci-status.yml) contains the same workflow with
adoption comments. The bridge needs no checkout. Use the exact signal workflow
name in its `workflows` list. Merge both workflows into the default branch, then
test a fork PR before changing upstream CI.

### Reduce duplicate upstream jobs

The [CI example](examples/ci.yml) shows the job condition and concurrency setup.
Replace its deliberately failing test step with your complete test command.
The example skips heavy jobs only for external PRs to `main` in the configured
upstream repository. PRs against contributor forks retain their tests. PR numbers
keep concurrency groups separate when contributors use identical branch names.

Before you skip heavy jobs:

- Configure full required checks on `merge_group` in the upstream merge queue.
- Keep full PR CI for branches without equivalent validation before merge.
- Keep full PR CI for branches in the upstream repository.
- Align the skip conditions with the action's `base-branches` input.
- Check `needs` dependencies and artifact consumers for dependencies on skipped jobs.

A skipped producer can also skip dependent jobs or leave them without artifacts.
The action does not enforce repository rules or check your job conditions.
Projects without trusted validation before merge can retain full PR CI and use
this action for additional feedback.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `workflow` | Yes | — | Workflow file name, such as `ci.yml`. No directory or URL. |
| `github-token` | No | `${{ github.token }}` | Upstream `GITHUB_TOKEN`. Custom GitHub App identities are not supported. |
| `check-name` | No | `CI (fork)` | Check name, at most 100 characters. Use a distinct name for each workflow. |
| `base-branches` | No | `main` | Newline-separated exact base branch names. No glob matching. |
| `guide-url` | No | Empty | HTTPS URL for your contributor instructions. |
| `recent-hours` | No | `6` | Recent PR update window, from 1 to 168 hours. |
| `sweep-size` | No | `25` | Number of eligible PRs per rotating sweep, from 1 to 100. |
| `sweep-minutes` | No | `15` | Schedule interval used for sweep rotation, from 5 to 1440 minutes. |

To mirror multiple workflows, call the action once per workflow with distinct
check names. Keep each workflow's calls in one serialized bridge workflow.
A renamed check creates a new check. The action retains the old one.

## Outputs

| Output | Description |
|---|---|
| `processed` | Number of eligible PRs considered, including recent completed checks that wait for their sweep. |
| `written` | Number of checks created or updated. |
| `failed` | Number of PRs whose synchronization failed. |

The outputs contain numeric counts. A per-PR API error marks the action as failed
after it processes the remaining PRs. Configuration and PR-list errors stop the
action immediately and can leave outputs unset.

## How it works

The bridge accepts fork runs from `push` and `workflow_dispatch` events. It matches
the workflow file, fork repository, branch, and head SHA. The newest matching run
ID wins. A rerun uses that run's latest attempt. Rerunning an older run does not
supersede a newer run ID.

The upstream signal triggers the bridge through `workflow_run`. The bridge finds
matching open PRs through the API, even when the event's `pull_requests` array is
empty. It refreshes every eligible PR with that repository, branch, and SHA.

Fork runs do not trigger upstream `workflow_run` events. Scheduled polling finds
late runs and updates their results. It also recovers missed signals, including
signals that need first-time contributor approval.

### Polling

Each scheduled or manual invocation lists every page of open PRs. It polls active
or missing runs on recently updated PRs, plus a rotating group of all eligible PRs.
Completed runs and older PRs wait for their turn in the rotation to discover reruns
or deleted runs. Unchanged observations produce no writes.

With 500 eligible PRs, a group of 25, and a 15-minute schedule, one rotation takes
about five hours. This estimate assumes a stable PR list and on-time execution.
GitHub can delay or omit scheduled runs. Manual dispatch also processes one group
and the recent PR window, not every PR.

Match `sweep-minutes` to a regularly spaced schedule. Irregular schedules can
revisit some groups and miss others. If API usage is too high, reduce
`recent-hours` or `sweep-size`. Smaller values increase the refresh delay.

Each polled PR normally needs a check lookup and two run lookups. A missing run
also needs a workflow-state lookup. Before each write, the bridge reads the
current PR. An unchanged result needs neither that read nor a write.

### Results and troubleshooting

| Observation | Mirrored result |
|---|---|
| Queued, waiting, pending, or requested run | Queued |
| Running workflow | In progress |
| Completed workflow | Its supported conclusion |
| Reserved `stale`, unknown, or absent conclusion on a completed run | Failure |
| Disabled workflow with no matching run | Action required, with setup instructions |
| No matching run, or workflow endpoint returns 404 | Neutral, with setup and filter guidance |
| Permission error, rate limit, or other API error | Action required when a check can be written, and bridge failure |

An absent run does not establish that tests passed or that a path filter skipped
them. If no run appears, check the fork's Actions setup and workflow filters.

When a previous run disappears, the next poll replaces its result and link.
When a new run or attempt replaces a completed check, the bridge creates a new
check. If the Checks API fails, the bridge cannot clear the previous result.
Check the bridge workflow's logs for permission errors, rate limits, or outages.

## Security and permissions

Use the upstream `GITHUB_TOKEN` with `actions: read`, `checks: write`, and
`pull-requests: read`, as shown in [Usage](#usage). Fork reads use public resources.
Private forks are outside the action's supported scope.

The bridge only reads API metadata and writes checks. It does not run PR code,
read artifacts, approve workflows, or edit PRs. It identifies its checks by name,
PR/workflow identifier, and the `github-actions` app identity.

Supported caller events are `workflow_run`, `schedule`, `workflow_dispatch`, and
`pull_request_target`. An existing `pull_request_target` caller can use the action
without the signal workflow. It reads the current PR through the API before
polling. Keep both privileged caller types free of PR checkouts, artifact
execution, and caches from untrusted jobs.

Use one concurrency group with `cancel-in-progress: false` for notifications and
sweeps. Concurrent callers can create duplicate checks or overwrite newer results.
GitHub can replace a pending run even with this configuration. Scheduled polling
also recovers missed PR notifications.

The bridge rechecks PR state before a write. GitHub does not provide an atomic
PR-state/check-write operation. A push immediately after that read can leave a
check on the previous commit. A later invocation reconciles the current head.

The action uses a pinned `actions/github-script` dependency and its Node runtime.
It requires a runner compatible with that dependency, such as the current
GitHub-hosted Linux runners. No npm installation, checkout, or local Node setup
is necessary in the caller.

## Contributor setup

Add these instructions to your contributor guide or PR template:

1. Before your first push, open your fork's **Actions** tab and enable workflows.
2. Update your branch from upstream, then push it to your fork.
3. Open the fork check on your PR to see the workflow run.
4. If a run fails for unrelated reasons, rerun it in your fork's **Actions** tab.
5. If no run exists, check the workflow's branch and path filters.
6. If you enabled Actions after the push, push again or dispatch the workflow on the PR branch.

Manual dispatch requires the workflow to exist on the fork's default branch.
Keep that branch in sync with upstream. The run must use the PR branch and its
current commit.

## Development

Run from the repository root:

```sh
uv run --no-project node --test fork-ci/bridge.test.cjs
uv run --locked pytest fork-ci/test_action.py scripts/test_release_actions.py
prek run --all-files
```

[The CI workflow](../.github/workflows/fork-ci-test.yml) runs the bridge and
composite-entry tests. Coverage includes result transitions, API errors, check
ownership, stale notifications, pagination beyond 500 PRs, and the example job
conditions. The composite tests run `action.yml` with fixture APIs and nondefault
inputs.

Before production adoption, test a fork with Actions enabled and disabled.
Exercise a failed run, a rerun, a second push, a filtered push, and a retargeted PR.
Check that the full required suite still runs in the merge queue. Local tests
use fixture APIs and do not test hosted check writes or repository rules.

The implementation derives from DataFusion's
[fork CI bridge at `350ca765`](https://github.com/apache/datafusion/blob/350ca76505c9513d16e7dd9910bcd9bde2c911ed/.github/workflows/fork_ci_status.yml).
