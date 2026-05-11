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
name: analyze-action-pr
description: Triage a PR that adds or bumps an action in this repo's allowlist. Runs verify-action-build, classifies each failing action (clean / pipe-to-shell / unverified-download / nested-action-issue / verify-script-bug), and proposes concrete next actions — recommend approval, open an upstream issue + ping the PR author, or fix verify-action-build itself with a regression test. Use when the user says "analyze PR <N>", "triage PR <N>", "verify PR <N>", or otherwise asks to review an action-allowlist PR in this repo.
---

# Analyze an apache/infrastructure-actions PR

End-to-end triage of a PR that adds or bumps an action in this repo's
allowlist (`actions.yml` or the dependabot trigger composite). The output
is a recommended set of actions, each drafted for the user to confirm
before sending.

## Prerequisites

- `gh` CLI authenticated (`gh auth status`).
- `uv` installed (the verify-action-build CLI is invoked through it).
- For node-action JS rebuilds: Docker running.

## Workflow

### 1. Read the PR

```
gh api repos/apache/infrastructure-actions/pulls/<N>
gh pr diff <N> --repo apache/infrastructure-actions
```

Note every `org/repo:` key with new commit hashes — both wholly-new keys
and hash additions under pre-existing keys.

### 2. Verify

```
cd utils
GITHUB_TOKEN=$(gh auth token) uv run python -m verify_action_build --from-pr <N>
```

Exit 0 = all actions pass. Exit 1 = at least one failed.

If the count of `Extracted action reference` lines is lower than the
number of new hashes in the diff, the extractor is buggy → see case **E**
under "Classify".

### 3. Classify each finding

| Case | Shape | Recognise it by |
|---|---|---|
| **A** | pipe-to-shell | `curl ... \| sh`, `wget ... \| sh`, PowerShell `iex "& { $(iwr ...) } ..."` |
| **B** | plain unverified download | `curl -Lo bin URL && chmod +x bin` (or `ADD https://...` in a Dockerfile) with no checksum/signature step in the same file |
| **C** | nested-action issue | Top-level action passes but a `uses:` dependency (e.g. `install/foo`) hits A or B |
| **D** | metadata-only | `No LICENSE`, input interpolation in `run:` blocks, `GITHUB_PATH` writes — soft warnings, mention but don't block |
| **E** | verify-script gap | The script gets the wrong answer for a reason unrelated to the action's actual security: false positive (regex hole, missing pattern), missing capability (new action type / build flow / verification mechanism it doesn't yet recognize), bad attribution (extractor drops an action that's clearly in the diff), or a check that misreads a legitimate input shape |

### 4. Look up upstream verification material (for A/B/C)

```
gh api repos/<org>/<binary-repo>/releases/latest \
  --jq '{tag, assets: [.assets[].name]}'
```

Pick the simplest verification path the action could use:

| If upstream ships ... | The action can use ... |
|---|---|
| `*.sha256`, `SHA256SUMS` | `sha256sum -c` |
| `*.asc` (GPG detached signature) | `gpg --verify` |
| GitHub attestations | `gh attestation verify` |
| SLSA `provenance.json`, `attestations.jsonl` | `slsa-verifier` or `cosign verify-blob` |
| Sigstore bundle | `cosign verify` |

If upstream ships **none** of these, the upstream issue should ask them
to publish at least a `SHA256SUMS` file at release time.

### 5. Propose next actions

**Always draft, then wait for explicit confirmation before sending. This
applies to issues, PR comments, PR titles, and PR bodies.**

#### A / B / C — upstream action needs a fix

Two messages, both held until the user OKs:

1. **Issue** on the upstream action repo (the one whose `action.yml` has
   the offending line) that:
   - Quotes the offending lines from `action.yml`.
   - Names this PR (`apache/infrastructure-actions#<N>`) as a downstream
     consumer blocked on the fix.
   - Lists the verification material upstream already publishes (from
     step 4) so the proposal is concrete.
   - Proposes a fix using the simplest mechanism that matches.
   - Mentions any missing LICENSE if the repo has none.
   - Offers to send a PR.

2. **Comment** on `apache/infrastructure-actions#<N>` pinging the PR
   author, summarising the finding (1–3 lines), linking the upstream
   issue, and asking them to support it as a downstream consumer.

Do **not** approve the action.

#### D only (passing verification but with metadata warnings)

Note the warnings and recommend approval — the user typically approves
manually after a final read-through.

#### E — verify-action-build itself is wrong (or missing a capability)

This case is broader than "the script has a bug" — it also covers
"the script doesn't yet know about this kind of action / build flow
/ verification mechanism." If the verdict is wrong for a reason that
**isn't** about the action's actual security posture, it's case E.
Resist the temptation to wave it off as a one-off; almost every gap
becomes a recurring blocker once a second action hits the same shape.

Common shapes (with the PR / commit that closed each one — use these
as templates when proposing your own extension):

| Kind of gap | Example | Fix landed in |
|---|---|---|
| New action type entirely | Deno-based action (`deno task bundle`) | [#749](https://github.com/apache/infrastructure-actions/pull/749) |
| New action type entirely | Dart-based action (`setup-dart`) | [#741](https://github.com/apache/infrastructure-actions/pull/741) |
| New compiled-bundle extension | `.cjs` / `.mjs` not scanned | [#734](https://github.com/apache/infrastructure-actions/pull/734) |
| New build flow | `npm run start` invocation | [#664](https://github.com/apache/infrastructure-actions/pull/664) |
| New build flow | Multi-step Docker build (`tsc` + `ncc`) | [#685](https://github.com/apache/infrastructure-actions/pull/685) |
| Vendored / non-standard source layout | `node_modules` checked in (vendored deps) | [#652](https://github.com/apache/infrastructure-actions/pull/652) |
| Source-layout edge case | Orphan / source-detached release tags | [#768](https://github.com/apache/infrastructure-actions/pull/768) |
| New verification heuristic | Sibling `sha256sum -c` step counts as verify | [#800](https://github.com/apache/infrastructure-actions/pull/800) |
| New verification heuristic | JS/TS file fetches HTTP as data, not binary | [#775](https://github.com/apache/infrastructure-actions/pull/775) |
| New verification heuristic | `@actions/http-client` `*Json` helpers | commit [`920d616`](https://github.com/apache/infrastructure-actions/commit/920d616) |
| Regex hole on a real call shape | TS generics on `postJson<T>(...)` | [#798](https://github.com/apache/infrastructure-actions/pull/798) |
| New scan target | JS/TS sources not previously scanned for downloads | [#743](https://github.com/apache/infrastructure-actions/pull/743) |
| False positive on legitimate input | Multi-stage Dockerfile `FROM <stage>` flagged as unpinned | [#733](https://github.com/apache/infrastructure-actions/pull/733) |
| Extraction shape | Hash added under an existing `actions.yml` key | [#804](https://github.com/apache/infrastructure-actions/pull/804) |
| Per-ecosystem exemption | Lock-file rule too strict for library-first projects | [#770](https://github.com/apache/infrastructure-actions/pull/770) |

When you hit a case that's clearly one of these — or a new kind not
in the table — propose the fix. The cost of leaving a false positive
or unsupported action type is paid by the next reviewer who runs
into the same shape.

Steps:

1. **Identify the right module** under `utils/verify_action_build/`:
   - `pr_extraction.py` — `--from-pr` ref extraction.
   - `security.py` — most signal/heuristic patterns (binary downloads,
     verification, action.yml metadata, scripts).
   - `verification.py` — top-level verification orchestration.
   - `docker_build.py` — Docker rebuild flow.
   - `action_ref.py` / `release_lookup.py` / `github_client.py` —
     fetching action source and metadata.

2. **Capture the breaking shape as a real-world fixture.** PR #798's
   lesson: a stripped-down test fixture hid a TS-generic regex hole
   that the real source triggered. Use the actual file content (or
   a faithfully-trimmed version) and add a regression test that
   would have caught the issue.

3. **Run the full suite** from the repo root: `uv run pytest utils/tests/`.
   All tests must pass.

4. **Run prek** before pushing — see AGENTS.md for the install +
   workflow.

5. **Open a fix PR** off latest `origin/main`. Consult `CLAUDE.md`
   and `CODEOWNERS` for repo-specific conventions on commit
   attribution and reviewers; the verify-action-build area has
   established reviewers worth requesting.

#### All-clean PR

Render each verification summary, name any prior approvals on file,
and recommend approval.

## Improve this skill

When a run uncovers a pattern this skill doesn't already describe — a
new failure shape, a new false positive, a new verification mechanism,
an asset-naming convention worth recording — leave the skill better
than you found it:

1. Identify the gap concretely. Quote the line, the action, the
   verification material — whatever didn't fit a row in the table or
   a step in the workflow.
2. Draft a focused edit to this `SKILL.md`. The cheapest places to grow
   are: a new row in the case table (step 3), a new entry in the
   verification-material lookup (step 4), a new bullet under the
   relevant case in step 5, or a new line in "Recent precedents" below.
3. Show the diff to the user and ask whether to extend the skill.
4. On approval, open it as a separate small PR (one new pattern per PR
   keeps review easy and the diff anchored to a concrete cite).

The "Recent precedents" table is the easiest growth surface: each
triaged PR adds one line, anchored to a real PR/issue/commit, so
future runs can cite a precedent instead of re-deriving the analysis.

## Quick references

| Need | Command |
|---|---|
| Verify a single action | `cd utils && uv run python -m verify_action_build org/repo@<sha>` |
| Read a nested `action.yml` | `gh api 'repos/<org>/<repo>/contents/<path>/action.yml?ref=<sha>' --jq '.content' \| base64 -d` |
| List release assets | `gh api repos/<org>/<repo>/releases/latest --jq '{tag, assets: [.assets[].name]}'` |
| Run all tests | `uv run pytest utils/tests/` (from repo root) |
| Re-trigger PR CI | `gh pr comment <N> --body "@dependabot recheck"` (dependabot PRs) or push an empty commit |

## Recent precedents

| PR | Finding | Case | Outcome |
|---|---|---|---|
| #795 | `http.postJson<IdToken>(...)` — TS generic broke the `*Json` regex | E | Fix landed in PR #798 |
| #802 | carabiner-dev nested `install/{ampel,bnd}` do `curl + chmod 0755`; upstream ships SLSA `provenance.json` / `attestations.jsonl` | C | Upstream issue carabiner-dev/actions#51 + PR comment |
| #803 | 3 actions in one diff; extractor only got the wholly-new key | E | Fix landed in PR #804 |
| #806 | `jbangdev/setup-jbang` does `curl ... \| bash`; upstream ships SHA256/GPG | A | Upstream issue jbangdev/setup-jbang#16 + PR comment |
