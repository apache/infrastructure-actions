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
description: Triage a PR that adds or bumps an action in this repo's allowlist. Runs verify-action-build, classifies each failing action (clean / pipe-to-shell / unverified-download / nested-action-issue / metadata-only / verify-script gap / in-tree native binaries) and proposes concrete next actions — recommend approval, open an upstream issue + ping the PR author, or fix verify-action-build itself with a regression test. Use when the user says "analyze PR <N>", "triage PR <N>", "verify PR <N>", or otherwise asks to review an action-allowlist PR in this repo.
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

### Environment gotchas

`verify_action_build` shells out to `gh` and `docker`, so it needs both to
work *from a child process* — not just from your own shell. Two setups break
that, and both surface as errors that point away from the real cause:

| Symptom | Cause | Fix |
|---|---|---|
| `Error: could not fetch diff for PR #<N>`, or `HTTP 401: Requires authentication` from a nested `gh` call, while `gh auth status` reports a healthy login | `gh` stores its token in the OS keyring (`gh auth status` says `Logged in to github.com account <user> (keyring)`). Keyring access can be lost in a child process or under a sandbox, so the nested `gh pr diff` authenticates as nobody | Pass the token explicitly: `GITHUB_TOKEN=$(gh auth token) uv run python -m verify_action_build ...`, or run outside the sandbox |
| `Error: docker is required but not found in PATH` while Docker Desktop is running | Docker Desktop installs its CLI under `~/.docker/bin`, which may be outside `PATH` or a sandbox-denied read path | Prepend the CLI shipped in the app bundle: `PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"` (macOS) |

Confirm the keyring case before chasing it — `bash -c 'gh auth status'`
failing while a direct `gh auth status` succeeds is the tell.

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

For a PR whose `verify` check has already run in CI, read that job's log
instead — it is the same tool with the same output, costs no Docker
rebuild, and is the only option when the local environment can't run one:

```
gh pr checks <N> --json name,link --jq '.[] | select(.name=="verify") | .link'
gh run view <run-id> --log --job <job-id> | sed -e 's/\x1b\[[0-9;]*[a-zA-Z]//g'
```

Strip the ANSI escapes (as above) or the summary table is unreadable. Re-run
locally only when you need to test a change to `verify-action-build` itself.

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
| **F** | unverified in-tree binaries | The action ships pre-compiled native binaries directly in the repo (Go cross-compile, `.exe` / `.dll` / `.so` / `.dylib`, `.jar`, `.wasm`, etc.) and exec's them from a small launcher. `verify-action-build`'s `In-tree binary check` tries to reconcile each binary with verifiable upstream provenance — first via `gh attestation verify --owner <org>` (SLSA attestation transparency log), then by comparing SHA256 against the release's `SHA256SUMS` asset. Binaries that pass either check are ✓; those that pass neither are case **F** and should be rejected until upstream adds provenance (`actions/attest-build-provenance` or a signed `SHA256SUMS`). Successful verification is a hard pass: the binary's bytes are tied back to the workflow run that produced them or to a release-time checksum. **Not every unverified binary is case F**: one a bundler copied into the output directory from a lockfile-pinned dependency (a `.wasm`, `.node`, native lib) has npm's provenance chain, not the action's, so demanding a GitHub release attestation for it is case **E** — check whether the clean rebuild reproduces it before treating it as F |

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

#### F — unverified in-tree native binaries

`verify-action-build`'s `In-tree binary check` reports a per-binary
result.  Three outcomes:

- ✓ **Verified via `gh attestation verify`** — the binary's bytes are
  in GitHub's attestation transparency log under the action's owner.
  Pass; recommend approval.
- ✓ **Verified against `SHA256SUMS` release asset** — the binary's
  SHA256 matches the release's checksum file.  Slightly weaker than
  attestation (no signature on the checksum file itself yet) but a
  strong cryptographic chain from release to artifact.  Pass.
- ✗ **Unverified** — neither mechanism worked: no attestation, no
  matching SHA256SUMS entry, or the hash didn't match.  Hard reject.

For the unverified case, two messages, both held until the user OKs:

1. **Upstream issue** asking the action's maintainers to add either
   `actions/attest-build-provenance` to their release workflow (so the
   binaries get a SLSA attestation verifiable via `gh attestation
   verify`) or a `SHA256SUMS` file shipped as a release asset.  Quote
   the offending paths and explain the downstream-review impact.  See
   the runs-on/action#36 / runs-on/action#37 thread as a worked
   example: the maintainer accepted a one-line PR that added
   `actions/attest-build-provenance` and started shipping `SHA256SUMS`
   on each release; once that landed, future bumps verified
   automatically.
2. **Comment** on the ASF PR pinging the PR author, summarising why
   approval is held: the JS-rebuild check only verifies the launcher,
   not the binary, and we have no way to tie what runs on the runner
   back to the published source.  Link the upstream issue.

Before recommending retroactive rejection of an already-approved version
in this shape, **check downstream impact** with code search:

```
gh api 'search/code?q=<org>%2F<action>+org%3Aapache+language%3AYAML' \
  --jq '.items[] | "\(.repository.full_name)  \(.path)"'
```

If multiple ASF projects depend on the action, loop in their maintainers
(e.g. `@apache/<project>-committers`) before any retroactive decision —
they pay the cost of pinning back to an older version or migrating off.

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
| Rebuild emits intermediate output the action doesn't ship | Two-stage `tsc` → `esbuild` build where OUT_DIR resolves to the sub-project, so the gitignored stage-one `lib/` appears only in the rebuild | see the #1123 row under "Recent precedents" |
| Per-ecosystem exemption | Lock-file rule too strict for library-first projects | [#770](https://github.com/apache/infrastructure-actions/pull/770) |
| New lock-file format | `aube-lock.yaml` (pnpm lockfileVersion 9) unrecognised | [#1175](https://github.com/apache/infrastructure-actions/pull/1175) |
| Registry tarball root assumed | DefinitelyTyped roots `@types/*` at the bare package name, not `package/` | [#1174](https://github.com/apache/infrastructure-actions/pull/1174) |
| Executable source not diffed or scanned | `.sh` / `Dockerfile*` outside the source-diff extension set, and script analysis limited to composite/docker actions | see the #1196 row under "Recent precedents" |
| Binary verifiable by rebuild, not by release | Bundler-copied dependency asset in `dist/` has npm provenance, not GitHub-release provenance | see the #1195 row under "Recent precedents" |

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
| #813 | `browser-actions/setup-firefox@v1.7.2` ships a minimal `{"type":"module"}` package.json with no deps; lock-file check too strict | E | Fix landed in PR #816 |
| #809 | `runs-on/action@v2.1.1` ships ~10 MB of UPX-packed Go binaries (`main-linux-amd64`, `main-linux-arm64`, `main-windows-amd64.exe`); launcher exec's them as root; no SLSA, no SHA256SUMS | F | Upstream issue runs-on/action#36; deferred until upstream adds provenance |
| #825 | `runs-on/action@v2.1.2` — same in-tree binaries as v2.1.1, but upstream now ships SLSA attestations (`actions/attest-build-provenance` was wired in via runs-on/action#37) plus a `SHA256SUMS` release asset | F (verified) | Pass — `In-tree binary check` reports all 3 binaries verified via `gh attestation verify` |
| #944 / #960 | `JetBrains/qodana-action@v2026.1.3` (node24 action) ships `gradle/wrapper/gradle-wrapper.jar`; the in-tree binary check scans the whole repo and false-flagged it, but that jar is Gradle build tooling — never executed on a consumer's runner — and is checksum-verifiable in its own right (`gradle/wrapper-validation-action`) | E | Fix in PR #951 (path-suffix exemption for the canonical `gradle/wrapper/gradle-wrapper.jar`); also nudged upstream to drop the committed jar via JetBrains/qodana-action#605 |
| #1123 | `JetBrains/qodana-action@v2026.2.0` — `JS build verification ✗` whose entire content was 4 files **only in the rebuild** (`lib/{annotations,main,output,utils}.js`). Two-stage build: `tsc --build` (`outDir: ./lib`) → `esbuild lib/main.js --bundle --outfile=dist/index.js`. `main: scan/dist/index.js` makes the Dockerfile's `cut -d'/' -f1` resolve OUT_DIR to the whole `scan/` sub-project, so stage-one output — gitignored upstream via `**/lib`, never committed — lands in the compared tree. **#960 hit this too and was merged over it; only the gradle-jar half got fixed in #951.** Also burns a pointless approved-lock-file retry rebuild | E | Fixed by treating only-in-rebuilt files as informational (not published → never runs on a consumer's runner → not a supply-chain vector), keeping only-in-original a hard failure and guarding the no-published-JS case. Action itself clean: dist bundle maps 1:1 to the TS source diff, `common/cli.json` checksums bumped in lockstep |
| #1013 | `jdx/mise-action@5228313` (v3.6.3, node20) — clean all-pass: dist matches rebuild, LICENSE + lockfile present, and its `curl \| tar` fetch of the mise binary is **not** flagged (verifier reports "no binary downloads detected" — it's the tool pulling its own tarball from its official pinned release). Checksum verification is opt-in only (`sha256` input, from mise-action#185), off by default | clean (soft D note) | Approved. Gentle hardening issue mise-action#547: verify by default via a **signature against a pinned key** (mise's `minisign.pub`, key `64113EDF160FDEC2`, stable since Dec 2024), not a same-source `SHASUMS256.txt` — a checksum fetched from the same release as the binary is integrity, not authenticity, and can't cover the `mise.jdx.dev` CDN path. **Outcome: upstream accepted and fixed it same-day in mise-action#548 (signed-checksum verification); #547 closed completed** |
| #1133 / #1134 | `pypa/gh-action-pypi-publish` v1.13.0→v1.14.2 (composite) and `hadolint/hadolint-action` v3.3.0→v3.4.0 (docker) — both clean all-pass, each with the single warning `Dockerfile FROM <image> is tag-pinned, not digest-pinned`. In both cases the **already-approved prior version carries the identical shape**, so the warning is a standing property of the action, not something the bump introduced. pypi-publish's `oidc-exchange.py` looks alarming at +83/-15 but is ~90% type annotations; the substantive edits are hardening (`os.getenv()`→`os.environ[]`, explicit `IdentityError` when `detect_credential()` returns `None`, `format_map(locals())`→explicit kwargs), with no new endpoints and no change to the token-exchange flow | clean (soft D note) | Approved and merged. **Rule of thumb: for a tag-pinned-base-image warning, diff against the approved version before treating it as a finding — carried-over warnings are not regressions.** Worth tracking as a general hardening theme (digest-pinning base images) rather than blocking individual bumps |
| #1167 | `jdx/mise-action@7e36c90` (v4.2.4) — single red check `Lock file presence ✗`, everything else green including `JS build verification ✓`. The repo *does* have a lock file: v4.2.4 moved the build to [aube](https://github.com/jdx/aube), which writes `aube-lock.yaml` (pnpm lockfileVersion 9, 460 deps pinned with sha512 integrity). The presence check only knew the five established filenames | E | Fix in PR #1175. **Confirm a "missing lock file" verdict by listing the repo root before believing it** — `gh api 'repos/<org>/<repo>/contents/?ref=<sha>'` |
| #1171 | `JamesIves/github-pages-deploy-action@fa24774` (v4.9.0) — `Vendored npm registry check ✗ 13 extra`, but 0 modified, 0 errors, 116 packages integrity-verified, `JS build verification ✓`, and the vendored `node_modules` comparison matching all 3008 files across 75 packages. The 13 "extra" files were *every* file of exactly three packages (`@types/esrecurse`, `@types/estree`, `@types/json-schema`) — DefinitelyTyped roots its tarballs at the bare package name, so nothing lined up with `node_modules/@types/<pkg>/<rel>`. Switched on for the first time by this version's yarn → npm migration adding `node_modules/.package-lock.json` | E | Fix in PR #1174. **Heuristic: when *all* files of a package are reported extra, that is tarball resolution failing, not tampering. Injected code shows up as a few extra files among many accounted-for ones** |
| #1196 | `uraimo/run-on-arch-action@460cb8e` (v3.2.0) — two findings. (a) `JS build verification ✗` is entirely vendored-`node_modules` drift (87 files / 3 packages committed vs 192 / 6 rebuilt); the upstream compare touches no `node_modules` file, so the already-approved v3.1.0 carries the identical shape — carried over, not a regression, and the stricter registry check never engages because there is no `node_modules/.package-lock.json`. (b) The tool's "source changes" section listed only `action.yml`, hiding `src/run-on-arch.sh` **+12/-1** and three new `Dockerfiles/Dockerfile.*` — and `src/run-on-arch.js:127` `exec()`s that very script, so the shell payload *is* the action. `.sh` was outside `diff_source.py`'s extension set, and `analyze_scripts` ran only for composite/docker actions and only for scripts named in `action.yml` or a Dockerfile | E (+ carried-over) | Fix in branch `verify-diff-shell-sources`. The hidden `.sh` change was benign (bind-mounts `GITHUB_OUTPUT`/`ENV`/`PATH`/`STEP_SUMMARY`/`STATE` into the container to replace deprecated `set-output`). **For a node action, always check the upstream `compare` API for changed non-JS files — the tool's source diff is not the whole diff** |
| #1195 | `1Password/load-secrets-action@70062d7` (v5.0.1) — two findings, both carried over from the approved v5.0.0. (a) `Binary download verification ✗ 3 unverified` — `tc.downloadTool()` pulls the `op` CLI from the `cache.agilebits.com` CDN (not GitHub releases) with no checksum, then `core.addPath()`s it; the three installer `.ts` files are **untouched** by the bump. (b) `In-tree binary check ✗ dist/core_bg.wasm` — but the build is `ncc build ./src/index.ts`, and ncc copies dependency assets verbatim, so that 14 MB wasm comes from `@1password/sdk-core@0.5.0`, pinned by sha512 integrity in `package-lock.json`. Its provenance chain is npm's; asking for a GitHub-release attestation was the wrong question — case E, not F | E (+ carried-over B) | Fix in branch `verify-intree-rebuild-credit`: delete bundler-copied binaries before the rebuild as the minified JS already is, and credit any that come back byte-identical. **Both findings were already filed upstream by @potiuk — 1Password/load-secrets-action#168 (CLI download) and #186 (wasm provenance). Search the upstream repo before drafting anything** |
