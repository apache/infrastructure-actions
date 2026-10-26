// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { bridge, configuration } = require("./bridge.cjs");
const now = Date.UTC(2026, 9, 26);
const sha = "a".repeat(40);
const config = configuration({ FORK_CI_WORKFLOW: "ci.yml" });
const upstream = { owner: { login: "apache" }, name: "example", full_name: "apache/example" };
const fork = { owner: { login: "contributor" }, name: "example", full_name: "contributor/example" };
const pull = (number = 1, overrides = {}) => ({
  number, state: "open", updated_at: new Date(now).toISOString(),
  base: { ref: "main", repo: upstream }, head: { sha, ref: "feature", repo: fork }, ...overrides,
});
const run = (id = 10, overrides = {}) => ({
  id, status: "completed", conclusion: "success", run_attempt: 1, event: "push",
  head_sha: sha, head_branch: "feature", head_repository: fork, ...overrides,
});
const failure = (status) => Object.assign(new Error("untrusted response text"), { status });

function harness(options = {}) {
  const prs = options.prs || [pull()];
  const writes = [], calls = [], errors = [], failures = [];
  const checks = structuredClone(options.checks || []);
  const runs = options.runs || [run()];
  const record = (name, params) => calls.push({ name, params });
  const rest = {
    pulls: {
      list: async (p) => {
        record("pulls.list", p);
        return { data: prs.slice((p.page - 1) * p.per_page, p.page * p.per_page) };
      },
      get: async (p) => {
        record("pulls.get", p);
        return { data: options.current ? options.current(p.pull_number) : prs.find((pr) => pr.number === p.pull_number) };
      },
    },
    checks: {
      listForRef: async (p) => {
        record("checks.listForRef", p);
        const matching = checks.filter((c) => c.head_sha === p.ref);
        return { data: { check_runs: matching.slice((p.page - 1) * p.per_page, p.page * p.per_page) } };
      },
      create: async (p) => {
        record("checks.create", p);
        if (options.writeError?.(p)) throw failure(403);
        writes.push({ method: "create", ...p });
        const check = { ...p, id: 1000 + checks.length, app: { slug: "github-actions" } };
        checks.push(check);
        return { data: check };
      },
      update: async (p) => {
        record("checks.update", p);
        if (options.writeError?.(p)) throw failure(403);
        writes.push({ method: "update", ...p });
        Object.assign(checks.find((c) => c.id === p.check_run_id), p);
      },
    },
    actions: {
      listWorkflowRuns: async (p) => {
        record("actions.listWorkflowRuns", p);
        const status = options.readError?.(p);
        if (status) throw failure(status);
        // Follow GitHub's newest-first ordering and event filter. Keep other
        // mismatches to test defensive response validation.
        return { data: { workflow_runs: runs.filter((r) => r.event === p.event).sort((a, b) => b.id - a.id).slice(0, p.per_page) } };
      },
      getWorkflow: async (p) => {
        record("actions.getWorkflow", p);
        if (options.stateError) throw failure(options.stateError);
        return { data: { state: options.state || "active" } };
      },
    },
  };
  const github = {
    rest,
    paginate: async (method, params) => {
      const result = [];
      for (let page = 1; ; page++) {
        const { data } = await method({ ...params, page });
        const items = Array.isArray(data) ? data : data.check_runs;
        result.push(...items);
        if (items.length < params.per_page) return result;
      }
    },
  };
  const context = {
    repo: { owner: "apache", repo: "example" }, runId: 123,
    eventName: options.event || "pull_request_target",
    payload: options.payload || { pull_request: { number: prs[0]?.number } },
  };
  const core = { info() {}, error: (s) => errors.push(s), setFailed: (s) => failures.push(s) };
  return {
    writes, calls, checks, errors, failures,
    execute: () => bridge({ github, context, core, config: { ...config, ...options.config }, now: options.now ?? now }),
  };
}

async function existing(runOverrides = {}) {
  const h = harness({ runs: [run(10, runOverrides)] });
  await h.execute();
  return h.checks;
}

for (const [status, conclusion, expectedStatus, expectedConclusion] of [
  ["completed", "success", "completed", "success"],
  ["completed", "failure", "completed", "failure"],
  ["completed", "cancelled", "completed", "cancelled"],
  ["completed", "timed_out", "completed", "timed_out"],
  ["completed", "neutral", "completed", "neutral"],
  ["completed", "skipped", "completed", "skipped"],
  ["completed", "action_required", "completed", "action_required"],
  ["completed", "stale", "completed", "failure"],
  ["completed", "startup_failure", "completed", "failure"],
  ["completed", null, "completed", "failure"],
  ["in_progress", null, "in_progress", undefined],
  ["waiting", null, "queued", undefined],
  ["queued", null, "queued", undefined],
]) {
  test(`maps ${status}/${conclusion} to a writable check state`, async () => {
    const h = harness({ runs: [run(10, { status, conclusion })] });
    await h.execute();
    assert.equal(h.writes[0].status, expectedStatus);
    assert.equal(h.writes[0].conclusion, expectedConclusion);
    assert.equal(h.writes[0].head_sha, sha);
  });
}

test("manual dispatch replaces an older failed push, but a PR run cannot replace it", async () => {
  const h = harness({ runs: [run(10, { conclusion: "failure" }), run(11, { event: "workflow_dispatch" }), run(12, { event: "pull_request" })] });
  await h.execute();
  assert.equal(JSON.parse(h.writes[0].output.text).run_id, 11);
  for (const c of h.calls.filter((c) => c.name === "actions.listWorkflowRuns")) {
    assert.equal(c.params.workflow_id, "ci.yml");
    assert.equal(c.params.head_sha, sha);
    assert.equal(c.params.branch, "feature");
  }
});

for (const override of [
  { head_sha: "b".repeat(40) }, { head_branch: "other" },
  { head_repository: { full_name: "stranger/example" } },
]) {
  test(`rejects unrelated run ${JSON.stringify(override)}`, async () => {
    const h = harness({ runs: [run(10, override)] });
    await h.execute();
    assert.equal(h.writes[0].conclusion, "neutral");
    assert.equal(JSON.parse(h.writes[0].output.text).run_id, null);
  });
}

test("constructs run links instead of rendering a fork-supplied URL", async () => {
  const h = harness({ runs: [run(10, { html_url: "https://evil.example/steal" })] });
  await h.execute();
  assert.equal(h.writes[0].details_url, "https://github.com/contributor/example/actions/runs/10");
  assert.ok(!JSON.stringify(h.writes).includes("evil.example"));
});

for (const [state, conclusion] of [["active", "neutral"], ["disabled_fork", "action_required"], ["disabled_manually", "action_required"]]) {
  test(`missing run on ${state} workflow gives ${conclusion}`, async () => {
    const h = harness({ runs: [], state });
    await h.execute();
    assert.equal(h.writes[0].conclusion, conclusion);
  });
}

test("404 is missing workflow, not a failed sweep", async () => {
  const h = harness({ readError: () => 404, stateError: 404 });
  await h.execute();
  assert.equal(h.writes[0].conclusion, "neutral");
  assert.deepEqual(h.failures, []);
});

for (const status of [403, 429, 500]) {
  test(`HTTP ${status} replaces stale success with an API error and continues other PRs`, async () => {
    const other = pull(2, { head: { sha, ref: "feature", repo: { ...fork, owner: { login: "other" }, full_name: "other/example" } } });
    const h = harness({ event: "schedule", prs: [pull(), other], checks: await existing(),
      config: { sweepSize: 100 }, readError: (p) => p.owner === "contributor" ? status : null });
    const result = await h.execute();
    assert.equal(result.failed, 1);
    assert.equal(result.processed, 2);
    assert.equal(h.writes[0].conclusion, "action_required");
    assert.equal(h.writes.length, 2);
    assert.equal(h.failures.length, 1);
    assert.ok(!h.errors.join().includes("untrusted response"));
  });
}

test("workflow-state API errors also fail visibly", async () => {
  const h = harness({ runs: [], stateError: 503 });
  assert.equal((await h.execute()).failed, 1);
  assert.equal(h.writes[0].conclusion, "action_required");
});

test("a missing event result does not mask an error from the other event", async () => {
  const h = harness({ readError: (p) => p.event === "push" ? 404 : 503 });
  assert.equal((await h.execute()).failed, 1);
  assert.equal(h.writes[0].conclusion, "action_required");
});

test("a successful push does not mask an unreadable manual-run list", async () => {
  const h = harness({ readError: (p) => p.event === "workflow_dispatch" ? 403 : null });
  assert.equal((await h.execute()).failed, 1);
  assert.equal(h.writes[0].conclusion, "action_required");
});

test("an empty repository sweep does not call fork or check APIs", async () => {
  const h = harness({ event: "schedule", prs: [] });
  assert.deepEqual(await h.execute(), { processed: 0, written: 0, failed: 0 });
  assert.deepEqual(h.calls.map((c) => c.name), ["pulls.list"]);
});

test("a check write failure does not stop the next PR", async () => {
  const h = harness({ event: "schedule", prs: [pull(), pull(2)], config: { sweepSize: 100 },
    writeError: (p) => p.external_id.endsWith(":1") });
  assert.equal((await h.execute()).failed, 1);
  assert.equal(h.writes.length, 1);
  assert.ok(h.writes[0].external_id.endsWith(":2"));
});

test("deleted fork run clears a previous success and its link", async () => {
  const h = harness({ checks: await existing(), runs: [] });
  await h.execute();
  assert.equal(h.writes[0].method, "update");
  assert.equal(h.writes[0].conclusion, "neutral");
  assert.ok(h.writes[0].details_url.endsWith("/actions/workflows/ci.yml"));
});

test("reruns create a fresh active check and later update it", async () => {
  const h = harness({ checks: await existing(), runs: [run(10, { run_attempt: 2, status: "in_progress", conclusion: null })] });
  await h.execute();
  assert.equal(h.writes[0].method, "create");
  assert.equal(h.writes[0].conclusion, undefined);
  const completed = harness({ checks: h.checks, runs: [run(10, { run_attempt: 2 })] });
  await completed.execute();
  assert.equal(completed.writes[0].method, "update");
  assert.equal(completed.writes[0].check_run_id, h.checks.at(-1).id);
});

test("unchanged observations and repeated notifications do not write", async () => {
  const h = harness({ checks: await existing() });
  await h.execute();
  assert.deepEqual(h.writes, []);
});

test("late runs replace missing checks", async () => {
  const missing = harness({ runs: [] });
  await missing.execute();
  const h = harness({ checks: missing.checks });
  await h.execute();
  assert.equal(h.writes[0].conclusion, "success");
});

test("repairs malformed metadata on an owned check", async () => {
  const checks = await existing();
  checks[0].output.text = "invalid JSON";
  const h = harness({ checks });
  await h.execute();
  assert.equal(JSON.parse(h.writes[0].output.text).run_id, 10);
});

test("does not adopt another app's or PR's checks even beyond the first page", async () => {
  const [owned] = await existing();
  const checks = Array.from({ length: 101 }, (_, i) => ({ ...owned, id: i,
    app: { slug: "other-app" } }));
  checks.push({ ...owned, id: 500, external_id: "fork-ci:ci.yml:pr:2" });
  checks.push({ ...owned, id: 501, external_id: "fork-ci:other.yml:pr:1" });
  checks.push(owned);
  const h = harness({ checks, runs: [run(10, { conclusion: "failure" })] });
  await h.execute();
  assert.equal(h.writes[0].method, "update");
  assert.equal(h.writes[0].check_run_id, owned.id);
  assert.ok(h.calls.some((c) => c.name === "checks.listForRef" && c.params.page === 2));
});

for (const override of [
  { head: { sha, ref: "feature", repo: null } },
  { head: { sha, ref: "feature", repo: upstream } },
  { state: "closed" }, { base: { ref: "release", repo: upstream } },
  { base: { ref: "main", repo: fork } },
]) {
  test(`ignores ineligible PR ${JSON.stringify(override)}`, async () => {
    const h = harness({ prs: [pull(1, override)] });
    await h.execute();
    assert.deepEqual(h.writes, []);
    assert.equal(h.calls.length, 1); // Only the authoritative PR read.
  });
}

for (const override of [{ state: "closed" }, { head: { sha: "b".repeat(40), ref: "feature", repo: fork } }, { base: { ref: "release", repo: upstream } }]) {
  test(`does not publish after concurrent PR change ${JSON.stringify(override)}`, async () => {
    let reads = 0;
    const h = harness({ current: () => ++reads === 1 ? pull() : pull(1, override) });
    await h.execute();
    assert.deepEqual(h.writes, []);
  });
}

const signal = { workflow_run: { event: "pull_request", head_repository: fork, head_branch: "feature", head_sha: sha, pull_requests: [] } };

test("workflow_run matches every eligible PR at the exact repository, branch and SHA", async () => {
  const h = harness({ event: "workflow_run", payload: signal, prs: [pull(), pull(2),
    pull(3, { base: { ref: "release", repo: upstream } }),
    pull(4, { head: { ref: "feature", sha, repo: { ...fork, full_name: "stranger/example" } } }),
    pull(5, { head: { ref: "other", sha, repo: fork } }),
    pull(6, { head: { ref: "feature", sha: "b".repeat(40), repo: fork } }),
  ] });
  await h.execute();
  assert.deepEqual(h.writes.map((w) => w.external_id), ["fork-ci:ci.yml:pr:1", "fork-ci:ci.yml:pr:2"]);
});

test("ignores notifications for push workflows", async () => {
  const h = harness({ event: "workflow_run", payload: { workflow_run: { ...signal.workflow_run, event: "push" } } });
  assert.deepEqual(await h.execute(), { processed: 0, written: 0, failed: 0 });
  assert.deepEqual(h.calls, []);
});

test("rejects unprivileged or unrelated event types", async () => {
  const h = harness({ event: "pull_request" });
  await assert.rejects(h.execute, /requires schedule/);
  assert.deepEqual(h.calls, []);
});

test("sweep visits more than 500 old PRs across all pages and rotating slices", async () => {
  const prs = Array.from({ length: 526 }, (_, i) => pull(i + 1, { updated_at: "2020-01-01T00:00:00Z" }));
  const visited = new Set();
  for (let slot = 0; slot < 22; slot++) {
    const h = harness({ prs, event: "schedule", now: now + slot * 15 * 60_000 });
    await h.execute();
    for (const w of h.writes) visited.add(JSON.parse(w.output.text).pr);
    assert.ok(h.calls.some((c) => c.name === "pulls.list" && c.params.page === 6));
    assert.ok(h.writes.length <= 25);
  }
  assert.equal(visited.size, 526);
});

test("recent completed results wait for the sweep, active and missing results do not", async () => {
  const checks = await existing();
  const prs = [pull(), pull(2)];
  // Select the second PR as the sweep slice.
  const opts = { checks, prs, event: "schedule", config: { sweepSize: 1 }, now: 15 * 60_000 };
  const done = harness(opts);
  await done.execute();
  assert.equal(done.calls.filter((c) => c.name === "actions.listWorkflowRuns").length, 2);
  for (const status of ["queued", "in_progress"]) {
    const active = harness({ ...opts, checks: [{ ...checks[0], status, conclusion: null }] });
    await active.execute();
    assert.equal(active.calls.filter((c) => c.name === "actions.listWorkflowRuns").length, 4);
  }
  const missing = harness({ runs: [] });
  await missing.execute();
  const late = harness({ ...opts, checks: missing.checks });
  await late.execute();
  assert.equal(late.calls.filter((c) => c.name === "actions.listWorkflowRuns").length, 4);
});

test("configuration supports another workflow, check name and exact base branch", async () => {
  const custom = configuration({ FORK_CI_WORKFLOW: "build.yaml", FORK_CI_CHECK_NAME: "Build (fork)", FORK_CI_BASE_BRANCHES: "trunk\nrelease/2", FORK_CI_GUIDE_URL: "https://example.org/guide" });
  const h = harness({ config: custom, prs: [pull(1, { base: { ref: "trunk", repo: upstream } })] });
  await h.execute();
  assert.equal(h.writes[0].name, "Build (fork)");
  assert.ok(h.writes[0].external_id.includes("build.yaml"));
});

for (const env of [
  { FORK_CI_WORKFLOW: "../ci.yml" }, { FORK_CI_WORKFLOW: "ci.yml?ref=evil" },
  { FORK_CI_BASE_BRANCHES: "\n " }, { FORK_CI_SWEEP_SIZE: "0" },
  { FORK_CI_SWEEP_SIZE: "101" }, { FORK_CI_SWEEP_MINUTES: "NaN" },
  { FORK_CI_RECENT_HOURS: "1.5" }, { FORK_CI_GUIDE_URL: "javascript:alert(1)" },
  { FORK_CI_CHECK_NAME: "a\nb" },
]) {
  test(`rejects invalid configuration ${JSON.stringify(env)}`, () => {
    assert.throws(() => configuration({ FORK_CI_WORKFLOW: "ci.yml", ...env }));
  });
}

test("unchanged scheduled observations do not re-read the PR", async () => {
  const h = harness({ event: "schedule", checks: await existing() });
  await h.execute();
  assert.deepEqual(h.writes, []);
  assert.ok(!h.calls.some((c) => c.name === "pulls.get"));
});

test("an active check becomes neutral if its run disappears", async () => {
  const h = harness({ checks: await existing({ status: "in_progress", conclusion: null }), runs: [] });
  await h.execute();
  assert.equal(h.writes[0].method, "update");
  assert.equal(h.writes[0].status, "completed");
  assert.equal(h.writes[0].conclusion, "neutral");
});

test("a stale PR event uses the current head and leaves old-head checks alone", async () => {
  const currentSha = "b".repeat(40);
  const prs = [pull(1, { head: { sha: currentSha, ref: "feature", repo: fork } })];
  const h = harness({ payload: { pull_request: pull() }, prs, checks: await existing(),
    runs: [run(11, { head_sha: currentSha })] });
  await h.execute();
  assert.equal(h.writes[0].method, "create");
  assert.equal(h.writes[0].head_sha, currentSha);
  assert.equal(h.checks[0].head_sha, sha);
  assert.equal(JSON.parse(h.checks[0].output.text).run_id, 10);
});

test("failed signal runs still trigger a fork lookup", async () => {
  const h = harness({ event: "workflow_run", payload: {
    workflow_run: { ...signal.workflow_run, conclusion: "failure" },
  } });
  await h.execute();
  assert.equal(h.writes[0].conclusion, "success");
});

test("a stale signal cannot publish a result for a newer PR head", async () => {
  const h = harness({ event: "workflow_run", payload: signal,
    prs: [pull(1, { head: { sha: "b".repeat(40), ref: "feature", repo: fork } })] });
  assert.equal((await h.execute()).processed, 0);
  assert.deepEqual(h.writes, []);
});
