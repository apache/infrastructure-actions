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

---
name: sweep-open-prs
description: Use when the maintainer asks what can be landed across the open PR queue - "check if I can merge any PRs", "which PRs can be approved and merged", "sweep the PRs", "what's ready to land", "anything new on the PRs". Also for a routine pass over the queue after an overnight dependabot batch. Not for a deep review of one named PR.
---

# Sweep the open PR queue

Answer "what can I land right now?" for every open PR in one pass, then act
only on what the maintainer confirms.

The queue is mostly dependabot: allowlist bumps under
`.github/actions/for-dependabot-triggered-reviews/`, plus workflow and
`uv.lock` bumps. A sweep is cheap because two `gh` calls cover the whole
queue; the expensive part is reviewing whatever the sweep surfaces as red or
as needing a human read.

## Environment

`gh` reads its config and auth keyring from `~/.config/gh`, which the sandbox
denies. Every `gh` call in this skill needs `dangerouslyDisableSandbox: true`.
The first failure looks like this and is not an auth problem:

```
failed to load config: open /Users/<user>/.config/gh/config.yml: operation not permitted
```

Two more traps, both of which produce a bogus `HTTP 401`:

| Trap | Fix |
|---|---|
| Batching `gh` calls in a shell `for` loop | One `gh` call per Bash invocation |
| `gh` invoked from a python/sh child process | Call `gh` directly from Bash |

## Step 1: gather

Two calls cover the queue. Run them as written - the per-PR alternative burns
a call per PR and hits the loop trap above.

State:

```
gh pr list --repo apache/infrastructure-actions --limit 50 \
  --json number,title,author,isDraft,mergeable,mergeStateStatus,reviewDecision \
  --jq '.[] | "\(.number)\t@\(.author.login)\tmergeable=\(.mergeable)\tstate=\(.mergeStateStatus)\treview=\(.reviewDecision)\t\(.title)"'
```

Checks, collapsed to only what is NOT green:

```
gh pr list --repo apache/infrastructure-actions --limit 50 \
  --json number,statusCheckRollup \
  --jq '.[] | "\(.number)\t" + ([.statusCheckRollup[]
       | select((.conclusion//.state) != "SUCCESS"
             and (.conclusion//.state) != "NEUTRAL"
             and (.conclusion//.state) != "SKIPPED")
       | "\(.name//.context)=\(.conclusion//.state)"]
       | if length==0 then "ALL GREEN" else join(" ") end)'
```

`mergeStateStatus: UNKNOWN` means GitHub is still recomputing mergeability,
usually right after another PR merged. It is not a verdict - re-query that PR
before classifying it.

## Step 2: classify

Bucket each PR on the observable fields alone:

| Bucket | Signature | Action |
|---|---|---|
| **Merge now** | `state=CLEAN`, `review=APPROVED`, all green | Merge on confirmation |
| **Approve + merge** | all green, `review=REVIEW_REQUIRED`, author is dependabot or another person | Review, then approve + merge |
| **Needs triage** | any check not green | Read the failing job before judging |
| **Blocked on reviewer** | all green, `review=REVIEW_REQUIRED`, **maintainer is the author** | Cannot self-approve - nudge, do not attempt |
| **Blocked on author** | `review=CHANGES_REQUESTED` | Waiting on the bump or an upstream fix |
| **Conflicting** | `mergeable=CONFLICTING` | Needs a rebase first |

Two rules that decide most of the queue:

- **You cannot approve your own PR.** A maintainer-authored PR with
  `review=REVIEW_REQUIRED` is never in an actionable bucket no matter how
  green it is. Say so plainly rather than offering to approve it.
- **`state=BLOCKED` with `review=REVIEW_REQUIRED` just means "needs an
  approval"** - it is not a failure. `BLOCKED` alongside a red check is the
  failure.

## Step 3: act, per bucket

### Allowlist bumps (`action-allowlist-review:` titles)

The `verify` check is the gate. Green `verify` means the tool reconciled the
published action against a rebuild from its own lock file.

**REQUIRED SUB-SKILL:** for anything red, or for any bump you are about to
approve on substance rather than on a green tick, use `analyze-action-pr`. It
owns the failure taxonomy (pipe-to-shell, unverified download, in-tree
binaries, verify-script gaps) and the upstream-issue workflow.

Before treating a warning as a finding, **diff it against the approved
version**. A warning the already-approved version also carries is a standing
property of the action, not a regression the bump introduced. Carried-over
warnings do not block.

### Lock-file and workflow bumps

`uv.lock` / `package-lock.json` only, or a workflow action SHA bump: read the
diff, confirm it touches no action surface, approve.

### Red checks

Read the failing job before forming a view:

```
gh pr checks <N> --repo apache/infrastructure-actions --json name,state,link \
  --jq '.[] | select(.state=="FAILURE") | .link'
gh run view --repo apache/infrastructure-actions --job <job-id> --log-failed \
  | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g'
```

Always strip the ANSI escapes or the summary table is unreadable.

**A red check on a stale branch may be a fixed bug.** Before reporting a
finding, check whether the failure predates something already merged to main -
rebase or re-run rather than filing against it.

### Superseded PRs

A bump held for an upstream defect is often replaced rather than fixed:
dependabot opens a new PR at the newer version and closes the old one. Before
digging back into a held PR, check whether a higher-numbered PR for the same
action already exists.

## Reporting

Lead with what the maintainer can act on, not with the full queue. A table
keyed by bucket beats a PR-by-PR narrative once the queue is more than a
handful.

**Draft every outbound message and wait for explicit confirmation** - reviews,
PR comments, upstream issues. Approving is a message in the maintainer's name.
Merging a PR they just approved in the same turn is the mid-flow exception.

## Common mistakes

| Mistake | Consequence |
|---|---|
| Reading `state=BLOCKED` as "broken" | Most of a healthy queue is `BLOCKED` pending one approval |
| Classifying on a `mergeStateStatus: UNKNOWN` | Misses a PR that is actually ready; re-query first |
| Offering to approve the maintainer's own PR | Impossible on GitHub; wastes a round-trip |
| Treating a carried-over warning as a regression | Blocks a bump that is no worse than the approved version |
| Filing upstream before searching the target repo | Duplicate issues; search existing issues, including the maintainer's own |
| Looping `gh` over PR numbers | Spurious `HTTP 401` on every call after the first |
