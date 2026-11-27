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

`gh` reads its config and auth keyring from `~/.config/gh` and needs
api.github.com, so it does not work inside the sandbox. The usual setup
excludes it with `sandbox.excludedCommands: ["gh *"]`, and then every `gh`
call runs unsandboxed with no bypass needed. Without that exclusion, each call
needs `dangerouslyDisableSandbox: true`, and the first failure looks like this
(it is not an auth problem):

```
failed to load config: open /Users/<user>/.config/gh/config.yml: operation not permitted
```

**The exclusion only matches a bare `gh` command.** A pipe, redirect, `;` or
`&&` puts the whole command back in the sandbox, where it fails with
`deny network-outbound api.github.com:443`, or with
`tls: failed to verify certificate: x509: OSStatus -26276` once the host is
allowed. Filter with `--jq` / `--template` instead of piping, and read output
from the tool result instead of redirecting it to a file. Keep the command on
one line too: a multi-line argument (a `--body` with newlines, a `\`
continuation) does not match the exclusion either, so pass long text with
`--body-file`.

Two more traps, both of which produce a bogus `HTTP 401`:

| Trap | Fix |
|---|---|
| Batching `gh` calls in a shell `for` loop | One `gh` call per Bash invocation |
| `gh` invoked from a python/sh child process | Call `gh` directly from Bash |

## Step 1: gather

Two calls cover the queue. Run them as written, each on a single line (see
Environment) - the per-PR alternative burns a call per PR and hits the loop
trap above.

State:

```
gh pr list --repo apache/infrastructure-actions --limit 50 --json number,title,author,isDraft,mergeable,mergeStateStatus,reviewDecision --jq '.[] | "\(.number)\t@\(.author.login)\tmergeable=\(.mergeable)\tstate=\(.mergeStateStatus)\treview=\(.reviewDecision)\t\(.title)"'
```

Checks, collapsed to only what is NOT green:

```
gh pr list --repo apache/infrastructure-actions --limit 50 --json number,statusCheckRollup --jq '.[] | "\(.number)\t" + ([.statusCheckRollup[] | . as $c | (if ($c.status // "COMPLETED") != "COMPLETED" then $c.status elif ($c.conclusion // "") != "" then $c.conclusion elif ($c.state // "") != "" then $c.state else "PENDING" end) as $s | select($s != "SUCCESS" and $s != "NEUTRAL" and $s != "SKIPPED") | "\($c.name // $c.context)=\($s)"] | if length==0 then "ALL GREEN" else join(" ") end)'
```

**Read `status` before `conclusion`.** A `CheckRun` carries `conclusion: null`
until its `status` reaches `COMPLETED`, and its `state` is always null - `state`
belongs to the `StatusContext` shape instead. A query that reaches straight for
`(.conclusion // .state)` therefore prints a bare `name=` for every check still
running, which reads like a finding rather than "ask again in a minute". The
query above reports `QUEUED` or `IN_PROGRESS` instead.

Two states that are *not* verdicts, and must be re-queried rather than
classified:

- `mergeStateStatus: UNKNOWN` - GitHub is recomputing mergeability, usually
  right after another PR merged. It also appears mid-merge-storm when several
  PRs touch one file; the state settles back to `CLEAN` on its own.
- A check reported `QUEUED` or `IN_PROGRESS` - not yet a result. Checks on a
  fresh PR routinely take minutes, so a sweep run the moment a PR opens will
  see them unfinished.

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
- **`approval` is a commit status, and `PENDING` is its normal state** on an
  unreviewed allow-list PR: it turns `SUCCESS` after a maintainer approves.
  Only `ERROR` is a failure (re-post with
  `gh workflow run allowlist_approval_status.yml`). A `FAILURE` named
  `approval` is a check run left by the old gate on a pre-switch commit;
  `gh pr update-branch <N> --rebase` replaces it.

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
gh pr checks <N> --repo apache/infrastructure-actions --json name,state,link --jq '.[] | select(.state=="FAILURE") | .link'
gh run view --repo apache/infrastructure-actions --job <job-id> --log-failed
```

Run each as its own Bash call, with no pipe (see Environment). The log keeps
its ANSI colour escapes; read past them to the summary table.

**A red check on a stale branch may be a fixed bug.** Before reporting a
finding, check whether the failure predates something already merged to main -
rebase or re-run rather than filing against it.

### Superseded PRs

A bump held for an upstream defect is often replaced rather than fixed:
dependabot opens a new PR at the newer version and closes the old one. Before
digging back into a held PR, check whether a higher-numbered PR for the same
action already exists.

### Batch approve and merge

Once the maintainer confirms a set of PRs and one approval text, act on the
whole set in **two `gh` calls**, not one per PR: one call per PR means one
permission prompt per PR, and a shell loop hits the `HTTP 401` trap. GraphQL
runs the aliased mutations of one request in order and reports each alias's
error on its own, so one PR failing does not stop the rest.

1. Get the node ids (one call):

   ```
   gh pr list --repo apache/infrastructure-actions --limit 50 --json number,id --jq '.[] | "\(.number) \(.id)"'
   ```

2. Write the approve batch to a file with the file-writing tool, one aliased
   `addPullRequestReview` per confirmed PR. The body is the confirmed text,
   ASCII only (an em-dash in a `gh` body has produced a spurious `401`), with
   newlines written as `\n`:

   ```graphql
   mutation {
     pr1565: addPullRequestReview(input: {pullRequestId: "PR_kwDO...", event: APPROVE, body: "Verified: ...\n\n---\nDrafted-by: ..."}) { pullRequestReview { state } }
     pr1566: addPullRequestReview(input: {pullRequestId: "PR_kwDO...", event: APPROVE, body: "..."}) { pullRequestReview { state } }
   }
   ```

   Send it with one bare call: `gh api graphql -F query=@<file>`.

3. Merge batch, same shape, one aliased
   `mergePullRequest(input: {pullRequestId: "...", mergeMethod: MERGE}) { pullRequest { number state } }`
   per approved PR. Wait a minute or two after approving first: the
   `approval` status has to turn `SUCCESS` before a PR is `CLEAN`.
4. Every alias that errored (not yet `CLEAN`, or a conflict after an earlier
   merge in the batch touched the same file) goes into a third batch of
   `enablePullRequestAutoMerge(input: {pullRequestId: "...", mergeMethod: MERGE}) { pullRequest { number } }`.
   Auto-merge refuses a PR that is already `CLEAN` (`Pull request is in clean
   status`), so it belongs only here, after the direct merge was tried.
   Dependabot rebases a conflicting PR on its own; auto-merge then lands it.

Report per PR from the response: approved, merged, auto-merge set, or the
alias's error.

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
| Reading an unfinished check as a failure | A running check has no conclusion yet; re-query rather than triaging it |
| Reading a `PENDING` `approval` status as stuck CI | It is waiting for a maintainer's approval, by design; nothing to re-run |
| Offering to approve the maintainer's own PR | Impossible on GitHub; wastes a round-trip |
| Treating a carried-over warning as a regression | Blocks a bump that is no worse than the approved version |
| Filing upstream before searching the target repo | Duplicate issues; search existing issues, including the maintainer's own |
| Looping `gh` over PR numbers | Spurious `HTTP 401` on every call after the first |
| Approving or merging one PR per call | One permission prompt per PR; use the batch above |
| Piping or redirecting `gh` output | The command runs sandboxed and fails with a network deny or `x509` TLS error |
