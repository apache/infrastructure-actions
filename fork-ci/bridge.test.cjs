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
const signal = { workflow_run: { event: "pull_request", head_repository: fork, head_branch: "feature", head_sha: sha, pull_requests: [] } };

function harness(options = {}) {
  const prs = options.prs || [pull()];
  const writes = [], calls = [], errors = [], warnings = [], failures = [], logs = [];
  const checks = structuredClone(options.checks || []);
  const statuses = structuredClone(options.statuses || []);
  const runs = options.runs || [run()];
  const jobs = options.jobs || [];
  const record = (name, params) => calls.push({ name, params });
  const rest = {
    repos: {
      listCommitStatusesForRef: async (p) => {
        record("repos.listCommitStatusesForRef", p);
        if (options.statusReadError) throw failure(options.statusReadError);
        const matching = statuses.filter((s) => s.sha === p.ref);
        return { data: matching.slice((p.page - 1) * p.per_page, p.page * p.per_page) };
      },
      createCommitStatus: async (p) => {
        record("repos.createCommitStatus", p);
        if (options.writeError?.(p)) throw failure(403);
        const status = { ...p, id: 2000 + statuses.length, creator: { login: "github-actions[bot]" } };
        writes.push({ method: "status", ...p });
        statuses.unshift(status);
        return { data: status };
      },
    },
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
        const id = 1000 + checks.length;
        // Like GitHub for GITHUB_TOKEN writes, keep the check page, not the requested details_url.
        const page = `https://github.com/apache/example/runs/${id}`;
        const check = { ...p, id, app: { slug: "github-actions" }, html_url: page, details_url: page };
        checks.push(check);
        return { data: check };
      },
      listAnnotations: async (p) => {
        record("checks.listAnnotations", p);
        if (options.annotationsError) throw failure(options.annotationsError);
        return { data: options.annotations?.[p.check_run_id] || [] };
      },
      update: async (p) => {
        record("checks.update", p);
        if (options.writeError?.(p)) throw failure(403);
        writes.push({ method: "update", ...p });
        const check = checks.find((c) => c.id === p.check_run_id);
        Object.assign(check, p, { details_url: check.html_url });
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
      listJobsForWorkflowRunAttempt: async (p) => {
        record("actions.listJobsForWorkflowRunAttempt", p);
        if (options.jobsError) throw failure(options.jobsError);
        return { data: { total_count: jobs.length, jobs: jobs.slice((p.page - 1) * p.per_page, p.page * p.per_page) } };
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
    paginate: async (method, params, map) => {
      const result = [];
      let done = false;
      for (let page = 1; ; page++) {
        const { data } = await method({ ...params, page });
        const items = Array.isArray(data) ? data : data.check_runs || data.jobs;
        result.push(...(map ? map({ data }, () => { done = true; }) : items));
        if (done || items.length < params.per_page) return result;
      }
    },
  };
  const context = {
    repo: { owner: "apache", repo: "example" }, runId: options.runId ?? 123,
    eventName: options.event || "workflow_run",
    payload: options.payload || signal,
  };
  const core = { info: (s) => logs.push(s), warning: (s) => warnings.push(s), error: (s) => errors.push(s), setFailed: (s) => failures.push(s) };
  return {
    writes, calls, checks, statuses, logs, errors, warnings, failures,
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
    assert.ok(h.checks[0].output.summary.includes("https://github.com/contributor/example/actions/workflows/ci.yml"));
  });
}

test("404 asks the contributor to enable the fork workflow, not a failed sweep", async () => {
  const h = harness({ readError: () => 404, stateError: 404 });
  await h.execute();
  assert.equal(h.writes[0].conclusion, "action_required");
  assert.equal(h.checks[0].output.title, "Workflow not available on the fork");
  assert.ok(h.checks[0].output.summary.includes("Actions tab"));
  assert.ok(h.checks[0].output.summary.includes("https://github.com/contributor/example/actions/workflows/ci.yml"));
  assert.deepEqual(h.failures, []);
});

test("a 404 workflow lookup after an empty run list also asks the contributor to act", async () => {
  const h = harness({ runs: [], stateError: 404 });
  await h.execute();
  assert.equal(h.writes[0].conclusion, "action_required");
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
    assert.ok(h.checks[0].output.summary.includes("https://github.com/apache/example/actions/runs/123"));
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
  // GitHub drops the requested link, so check what the stored check shows.
  assert.ok(!h.checks[0].output.summary.includes("/actions/runs/10"));
  assert.ok(h.checks[0].output.summary.includes("https://github.com/contributor/example/actions/workflows/ci.yml"));
});

test("repeated API errors link the newest bridge run, and a retry of that run does not write", async () => {
  const first = harness({ readError: () => 503 });
  await first.execute();
  const retry = harness({ checks: first.checks, readError: () => 503 });
  await retry.execute();
  assert.deepEqual(retry.writes, []);
  const next = harness({ checks: first.checks, readError: () => 503, runId: 124 });
  await next.execute();
  assert.equal(next.writes.length, 1);
  assert.ok(next.checks[0].output.summary.includes("https://github.com/apache/example/actions/runs/124"));
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
    assert.deepEqual(h.calls.map((c) => c.name), ["pulls.list"]);
  });
}

for (const override of [{ state: "closed" }, { head: { sha: "b".repeat(40), ref: "feature", repo: fork } }, { base: { ref: "release", repo: upstream } }]) {
  test(`does not publish after concurrent PR change ${JSON.stringify(override)}`, async () => {
    const h = harness({ current: () => pull(1, override) });
    await h.execute();
    assert.deepEqual(h.writes, []);
    assert.equal(h.calls.filter((c) => c.name === "pulls.get").length, 1);
  });
}

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

for (const event of ["pull_request", "pull_request_target", "push"]) {
  test(`rejects unsupported caller event ${event} before any API calls`, async () => {
    const h = harness({ event });
    await assert.rejects(h.execute, /requires schedule, workflow_dispatch, or workflow_run/);
    assert.deepEqual(h.calls, []);
  });
}

test("manual dispatch performs the same sweep as the schedule", async () => {
  const scheduled = harness({ event: "schedule" });
  const manual = harness({ event: "workflow_dispatch" });
  assert.deepEqual(await manual.execute(), await scheduled.execute());
  assert.deepEqual(manual.calls, scheduled.calls);
  assert.deepEqual(manual.writes, scheduled.writes);
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

test("a scheduled sweep finds the current head and leaves old-head checks alone", async () => {
  const currentSha = "b".repeat(40);
  const prs = [pull(1, { head: { sha: currentSha, ref: "feature", repo: fork } })];
  const h = harness({ event: "schedule", prs, checks: await existing(),
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

const statusConfig = { reporting: "commit-status" };
async function existingStatus(overrides = {}) {
  const h = harness({ config: statusConfig, runs: [run(10, overrides)] });
  await h.execute();
  return h.statuses;
}

test("check-run remains the default and unknown reporting modes are rejected", () => {
  assert.equal(config.reporting, "check-run");
  assert.throws(() => configuration({ FORK_CI_WORKFLOW: "ci.yml", FORK_CI_REPORTING: "status" }), /reporting/);
});

for (const [status, conclusion, state] of [
  ["queued", null, "pending"], ["waiting", null, "pending"],
  ["pending", null, "pending"], ["requested", null, "pending"],
  ["in_progress", null, "pending"], ["completed", "success", "success"],
  ["completed", "failure", "failure"], ["completed", "timed_out", "failure"],
  ["completed", "cancelled", "error"], ["completed", "action_required", "error"],
  ["completed", "neutral", "error"], ["completed", "skipped", "error"],
  ["completed", "stale", "failure"], ["completed", "unknown", "failure"],
  ["completed", null, "failure"],
]) {
  test(`commit status maps ${status}/${conclusion} to ${state}`, async () => {
    const h = harness({ config: statusConfig, runs: [run(10, { status, conclusion })] });
    assert.deepEqual(await h.execute(), { processed: 1, written: 1, failed: 0 });
    const [report] = h.statuses;
    assert.equal(report.state, state);
    assert.equal(report.context, "CI (fork) (PR #1)");
    assert.equal(report.sha, sha);
    assert.equal(report.target_url, "https://github.com/contributor/example/actions/runs/10/attempts/1");
    assert.ok(report.description.length <= 140);
    assert.ok(!h.calls.some((c) => c.name.startsWith("checks.")));
  });
}

for (const [state, stateError, expected] of [
  ["active", null, "pending"], ["disabled_fork", null, "error"],
  ["disabled_manually", null, "error"], ["active", 404, "error"],
]) {
  test(`missing fork run with workflow ${state}/${stateError} reports ${expected}`, async () => {
    const h = harness({ config: statusConfig, runs: [], state, stateError });
    await h.execute();
    assert.equal(h.statuses[0].state, expected);
    assert.equal(h.statuses[0].target_url, "https://github.com/contributor/example/actions/workflows/ci.yml");
    assert.deepEqual(h.failures, []);
  });
}

test("#1519 reports failure independently of a hidden check and the ping attempt", async () => {
  const checks = await existing({ conclusion: "failure" });
  Object.assign(checks[0], { id: 128996054771, check_suite: { id: 115296339110 } });
  const h = harness({ checks, config: statusConfig, runs: [run(42567062495, { conclusion: "failure" })],
    payload: { workflow_run: { ...signal.workflow_run, run_attempt: 2, check_suite_id: 115296339110 } } });
  await h.execute();
  assert.equal(h.statuses[0].state, "failure");
  assert.ok(h.statuses[0].target_url.includes("42567062495/attempts/1"));
  assert.ok(!h.calls.some((c) => c.name.startsWith("checks.")));
  const retry = harness({ checks, statuses: h.statuses, config: statusConfig,
    runs: [run(42567062495, { conclusion: "failure" })],
    payload: { workflow_run: { ...signal.workflow_run, run_attempt: 3 } } });
  await retry.execute();
  assert.deepEqual(retry.writes, []);
  assert.ok(!retry.calls.some((c) => c.name === "pulls.get"));
});

test("#1518 a new ping suite on the same commit preserves the commit status", async () => {
  const statuses = await existingStatus();
  const h = harness({ statuses, config: statusConfig,
    payload: { workflow_run: { ...signal.workflow_run, id: 43172771181, check_suite_id: 116909014253 } } });
  await h.execute();
  assert.equal(h.statuses[0].state, "success");
  assert.deepEqual(h.writes, []);
});

test("a status lookup finds the newest context across pages and stops reading history", async () => {
  const [owned] = await existingStatus();
  const statuses = [
    ...Array.from({ length: 100 }, (_, i) => ({ ...owned, context: `unrelated-${i}` })),
    { ...owned, context: owned.context.toUpperCase() },
    ...Array.from({ length: 150 }, () => ({ ...owned, state: "failure" })),
  ];
  const h = harness({ statuses, config: statusConfig });
  await h.execute();
  assert.deepEqual(h.writes, []);
  assert.deepEqual(h.calls.filter((c) => c.name === "repos.listCommitStatusesForRef").map((c) => c.params.page), [1, 2]);
});

test("a status from another writer is not reused even when its visible fields match", async () => {
  const [owned] = await existingStatus();
  const h = harness({ config: statusConfig,
    statuses: [{ ...owned, creator: { login: "another-bot[bot]" } }] });
  await h.execute();
  assert.equal(h.writes.length, 1);
  assert.equal(h.statuses[0].creator.login, "github-actions[bot]");
});

test("a newer foreign status does not let an older owned status suppress a write", async () => {
  const [owned] = await existingStatus({ conclusion: "failure" });
  const h = harness({ config: statusConfig, runs: [run(10, { conclusion: "failure" })], statuses: [
    { ...owned, state: "success", creator: { login: "another-bot[bot]" } }, owned,
  ] });
  await h.execute();
  assert.equal(h.writes.length, 1);
  assert.equal(h.statuses[0].state, "failure");
});

test("different PRs at the same commit have independent status contexts", async () => {
  const h = harness({ config: statusConfig, prs: [pull(), pull(2)], statuses: await existingStatus() });
  await h.execute();
  assert.equal(h.writes.length, 1);
  assert.equal(h.writes[0].context, "CI (fork) (PR #2)");
});

test("different workflows with distinct check names do not reuse a status", async () => {
  const h = harness({ config: { ...statusConfig, checkName: "Other fork workflow" }, statuses: await existingStatus() });
  await h.execute();
  assert.equal(h.writes[0].context, "Other fork workflow (PR #1)");
});

test("fork reruns publish pending then completion, and completed attempts remain distinguishable", async () => {
  const statuses = await existingStatus();
  const active = harness({ config: statusConfig, statuses,
    runs: [run(10, { run_attempt: 2, status: "in_progress", conclusion: null })] });
  await active.execute();
  assert.equal(active.statuses[0].state, "pending");
  assert.ok(active.statuses[0].target_url.endsWith("/attempts/2"));
  const done = harness({ config: statusConfig, statuses: active.statuses, runs: [run(10, { run_attempt: 2 })] });
  await done.execute();
  assert.equal(done.statuses[0].state, "success");
  const missed = harness({ config: statusConfig, statuses, runs: [run(10, { run_attempt: 2 })] });
  await missed.execute();
  assert.equal(missed.writes.length, 1);
  assert.ok(missed.statuses[0].description.includes("attempt 2"));
});

test("a new fork run with the same conclusion updates the status link", async () => {
  const h = harness({ config: statusConfig, statuses: await existingStatus(), runs: [run(11)] });
  await h.execute();
  assert.ok(h.statuses[0].target_url.endsWith("/11/attempts/1"));
});

test("missing and deleted runs clear stale success, and late runs replace pending", async () => {
  const missing = harness({ config: statusConfig, statuses: await existingStatus(), runs: [] });
  await missing.execute();
  assert.equal(missing.statuses[0].state, "pending");
  assert.ok(missing.statuses[0].target_url.endsWith("/workflows/ci.yml"));
  const late = harness({ config: statusConfig, statuses: missing.statuses });
  await late.execute();
  assert.equal(late.statuses[0].state, "success");
  const unchanged = harness({ config: statusConfig, statuses: missing.statuses, runs: [] });
  await unchanged.execute();
  assert.deepEqual(unchanged.writes, []);
});

for (const current of [
  () => pull(1, { state: "closed" }),
  () => pull(1, { base: { ref: "release", repo: upstream } }),
  () => pull(1, { head: { sha: "b".repeat(40), ref: "feature", repo: fork } }),
  () => pull(1, { head: { sha, ref: "renamed", repo: fork } }),
  () => pull(1, { head: { sha, ref: "feature", repo: null } }),
]) {
  test(`commit status rejects a stale PR snapshot ${JSON.stringify(current())}`, async () => {
    const h = harness({ config: statusConfig, current });
    await h.execute();
    assert.deepEqual(h.writes, []);
    assert.ok(h.logs.some((s) => s.includes("skipped write") && s.includes(sha.slice(0, 12))));
  });
}

test("status mode finds a new PR head without writing to the old commit", async () => {
  const nextSha = "b".repeat(40);
  const h = harness({ config: statusConfig, event: "schedule", statuses: await existingStatus(),
    prs: [pull(1, { head: { sha: nextSha, ref: "feature", repo: fork } })],
    runs: [run(11, { head_sha: nextSha })] });
  await h.execute();
  assert.equal(h.writes[0].sha, nextSha);
  assert.equal(h.statuses[1].sha, sha);
});

test("commit status permission failures do not stop other PRs", async () => {
  const h = harness({ config: statusConfig, prs: [pull(), pull(2)],
    writeError: (p) => p.context.endsWith("#1)") });
  assert.equal((await h.execute()).failed, 1);
  assert.equal(h.statuses.length, 1);
  assert.equal(h.statuses[0].context, "CI (fork) (PR #2)");
  assert.ok(!h.errors.join().includes("untrusted response"));
});

test("an unreadable status history fails without publishing a duplicate", async () => {
  const h = harness({ config: statusConfig, statuses: await existingStatus(), statusReadError: 403 });
  assert.equal((await h.execute()).failed, 1);
  assert.deepEqual(h.writes, []);
});

test("fork API errors replace green statuses without appending repeated errors", async () => {
  const first = harness({ config: statusConfig, statuses: await existingStatus(), readError: () => 503 });
  assert.equal((await first.execute()).failed, 1);
  assert.equal(first.statuses[0].state, "error");
  assert.ok(first.statuses[0].target_url.endsWith("/actions/runs/123"));
  const retry = harness({ config: statusConfig, statuses: first.statuses, readError: () => 503 });
  assert.equal((await retry.execute()).failed, 1);
  assert.deepEqual(retry.writes, []);
  const next = harness({ config: statusConfig, statuses: first.statuses, readError: () => 503, runId: 124 });
  assert.equal((await next.execute()).failed, 1);
  assert.deepEqual(next.writes, []);
  assert.ok(!next.calls.some((c) => c.name === "pulls.get"));
  assert.ok(next.statuses[0].target_url.endsWith("/actions/runs/123"));
  const recovered = harness({ config: statusConfig, statuses: next.statuses });
  await recovered.execute();
  assert.equal(recovered.writes.length, 1);
  assert.equal(recovered.statuses[0].state, "success");
});

test("unsuccessful commit statuses never read jobs or annotations with job-details enabled", async () => {
  const first = harness({ config: { ...statusConfig, jobDetails: true }, runs: [run(10, { conclusion: "failure" })],
    jobs: [{ id: 7, name: "failed job", conclusion: "failure", steps: [] }] });
  await first.execute();
  const next = harness({ config: { ...statusConfig, jobDetails: true }, statuses: first.statuses,
    runs: [run(10, { conclusion: "failure" })] });
  await next.execute();
  for (const h of [first, next]) {
    assert.equal(h.statuses[0].state, "failure");
    assert.ok(!h.calls.some((c) => c.name === "actions.listJobsForWorkflowRunAttempt" || c.name.startsWith("checks.")));
  }
  assert.deepEqual(next.writes, []);
});

test("recent completed statuses wait for the rotating sweep but pending and API errors do not", async () => {
  const prs = [pull(), pull(2)];
  const opts = { config: { ...statusConfig, sweepSize: 1 }, prs, event: "schedule", now: 15 * 60_000 };
  const completed = harness({ ...opts, statuses: await existingStatus() });
  await completed.execute();
  assert.equal(completed.calls.filter((c) => c.name === "actions.listWorkflowRuns").length, 2);
  assert.ok(completed.logs.some((s) => s.includes("skipped completed") && s.includes("fork run 10 attempt 1")));
  const statuses = await existingStatus();
  const pending = harness({ ...opts, statuses: [{ ...statuses[0], state: "pending" }] });
  await pending.execute();
  assert.equal(pending.calls.filter((c) => c.name === "actions.listWorkflowRuns").length, 4);
  const error = harness({ ...opts, statuses: [{ ...statuses[0], state: "error", target_url: "https://github.com/apache/example/actions/runs/123" }] });
  await error.execute();
  assert.equal(error.calls.filter((c) => c.name === "actions.listWorkflowRuns").length, 4);
});

test("logs explain unchanged and published results with PR, SHA, fork run and state", async () => {
  for (const reporting of ["check-run", "commit-status"]) {
    const first = harness({ config: { reporting } });
    await first.execute();
    const next = harness({ config: { reporting }, checks: first.checks, statuses: first.statuses });
    await next.execute();
    for (const h of [first, next]) {
      assert.ok(h.logs.some((s) => s.includes("PR #1") && s.includes(sha.slice(0, 12)) &&
        s.includes("fork run 10 attempt 1") && s.includes("success")));
    }
    assert.ok(next.logs.some((s) => s.includes("skipped unchanged")));
  }
});

const job = (id, overrides = {}) => ({
  id, name: `test (${id})`, conclusion: "success", steps: [{ name: "Test", conclusion: "success" }], ...overrides,
});
const failedRun = (overrides = {}) => [run(10, { conclusion: "failure", ...overrides })];
const named = (h, name) => h.calls.filter((c) => c.name === name);

test("lists jobs that did not succeed with their failed step and own failure annotation", async () => {
  const h = harness({ runs: failedRun(), jobs: [
    job(1), job(2, { conclusion: "skipped" }), job(5, { conclusion: "neutral" }),
    job(3, { name: "compile `x` | y\n@team", conclusion: "failure", steps: [{ name: "Build", conclusion: "failure" }] }),
    job(4, { name: "macos", conclusion: "cancelled", steps: [] }),
  ], annotations: {
    3: [{ annotation_level: "warning", message: "ignore me" }, { annotation_level: "failure", message: "Process completed with exit code 1." },
      { annotation_level: "failure", message: "TestExample failed" }],
    4: [{ annotation_level: "notice", message: "capacity notice" }, { annotation_level: "failure", message: "Process completed with exit code 2." }],
  } });
  await h.execute();
  const { title, summary, text } = h.writes[0].output;
  assert.equal(title, "Fork run failure: compile `x` | y @team and 1 more");
  assert.ok(summary.includes("| [`compile 'x' \\| y @team`](https://github.com/contributor/example/actions/runs/10/job/3) | `failure` | `Build` | `TestExample failed` |"));
  assert.ok(summary.includes("| [`macos`](https://github.com/contributor/example/actions/runs/10/job/4) | `cancelled` |  | `Process completed with exit code 2.` |"));
  assert.ok(!summary.includes("test (1)") && !summary.includes("test (2)") && !summary.includes("test (5)"));
  assert.equal(JSON.parse(text).jobs, 2);
  assert.ok(!summary.includes("capacity notice"));
  const { owner, repo, run_id, attempt_number } = named(h, "actions.listJobsForWorkflowRunAttempt")[0].params;
  assert.deepEqual({ owner, repo, run_id, attempt_number }, { owner: "contributor", repo: "example", run_id: 10, attempt_number: 1 });
  assert.deepEqual(named(h, "checks.listAnnotations").map(({ params: p }) => [p.owner, p.repo, p.check_run_id, p.per_page]),
    [["contributor", "example", 3, 100], ["contributor", "example", 4, 100]]);
});

test("lists jobs once per run attempt and reuses them on later sweeps", async () => {
  const jobs = [job(3, { conclusion: "failure" })];
  const first = harness({ runs: failedRun(), jobs });
  await first.execute();
  const again = harness({ event: "schedule", checks: first.checks, runs: failedRun(), jobs });
  await again.execute();
  assert.deepEqual(again.writes, []);
  assert.equal(named(again, "actions.listJobsForWorkflowRunAttempt").length, 0);
  assert.equal(named(again, "checks.listAnnotations").length, 0);
  const rerun = harness({ checks: first.checks, runs: failedRun({ run_attempt: 2 }), jobs });
  await rerun.execute();
  assert.equal(named(rerun, "actions.listJobsForWorkflowRunAttempt")[0].params.attempt_number, 2);
  assert.equal(rerun.writes[0].method, "create");
});

test("a failed run without failed jobs is not listed again", async () => {
  const first = harness({ runs: failedRun(), jobs: [job(1)] });
  await first.execute();
  assert.equal(first.writes[0].output.title, "Fork run failure");
  const again = harness({ checks: first.checks, runs: failedRun(), jobs: [job(1)] });
  await again.execute();
  assert.deepEqual(again.writes, []);
  assert.equal(named(again, "actions.listJobsForWorkflowRunAttempt").length, 0);
});

test("caps listed and annotated jobs", async () => {
  const jobs = Array.from({ length: 130 }, (_, i) => job(i + 1, { conclusion: "failure" }));
  const h = harness({ runs: failedRun(), jobs });
  await h.execute();
  const { title, summary } = h.writes[0].output;
  assert.equal(title, "Fork run failure: test (1) and 129 more");
  assert.equal(summary.split("\n").filter((l) => l.startsWith("| [")).length, 20);
  assert.ok(summary.includes("110 more job(s) did not succeed."));
  assert.equal(named(h, "checks.listAnnotations").length, 5);
  assert.equal(named(h, "actions.listJobsForWorkflowRunAttempt").length, 2);
});

test("a jobs API error keeps the run link and does not fail the sweep", async () => {
  const h = harness({ runs: failedRun(), jobsError: 403 });
  assert.equal((await h.execute()).failed, 0);
  assert.equal(h.writes[0].output.title, "Fork run failure");
  assert.equal(JSON.parse(h.writes[0].output.text).jobs, undefined);
  assert.deepEqual(h.warnings, ["PR #1: cannot list fork jobs (HTTP 403)."]);
  const retry = harness({ checks: h.checks, runs: failedRun(), jobs: [job(3, { conclusion: "failure" })] });
  await retry.execute();
  assert.equal(retry.writes[0].method, "update");
  assert.equal(retry.writes[0].output.title, "Fork run failure: test (3)");
});

test("an annotations API error keeps the job row", async () => {
  const h = harness({ runs: failedRun(), jobs: [job(3, { name: "unit", conclusion: "failure" })], annotationsError: 403 });
  assert.equal((await h.execute()).failed, 0);
  const { title, summary, text } = h.writes[0].output;
  assert.equal(title, "Fork run failure: unit");
  assert.ok(summary.includes("| [`unit`](https://github.com/contributor/example/actions/runs/10/job/3) | `failure` |  |  |"));
  assert.equal(JSON.parse(text).jobs, 1);
  assert.deepEqual(h.warnings, ["PR #1: cannot read annotations of fork job 3 (HTTP 403)."]);
});

for (const [name, runs] of [
  ["a new run of the same commit", failedRun({ id: 11 })],
  ["a changed conclusion", failedRun({ conclusion: "timed_out" })],
]) {
  test(`${name} lists jobs again`, async () => {
    const first = harness({ runs: failedRun(), jobs: [job(3, { conclusion: "failure" })] });
    await first.execute();
    const again = harness({ checks: first.checks, runs, jobs: [job(4, { conclusion: "failure" })] });
    await again.execute();
    assert.equal(named(again, "actions.listJobsForWorkflowRunAttempt").length, 1);
    assert.match(again.writes[0].output.title, /: test \(4\)$/);
  });
}

test("fork text drops control characters and is truncated without splitting a character", async () => {
  const title = "a\u0001b" + "x".repeat(96) + "\u{1F600}", row = "c" + "x".repeat(97) + "\u{1F600}zz";
  const h = harness({ runs: failedRun(), jobs: [job(3, { name: title, conclusion: "failure" }), job(4, { name: row, conclusion: "failure" })] });
  await h.execute();
  const { output } = h.writes[0];
  assert.equal(output.title, `Fork run failure: a b${"x".repeat(96)}\u{1F600} and 1 more`);
  assert.ok(output.summary.includes(`[\`c${"x".repeat(97)}\u{1F600}…\`]`));
});

for (const [name, options] of [
  ["successful runs", { runs: [run()] }],
  ["active runs", { runs: failedRun({ status: "in_progress", conclusion: null }) }],
  ["job-details: false", { runs: failedRun(), config: { jobDetails: false } }],
]) {
  test(`${name} do not list jobs`, async () => {
    const h = harness({ ...options, jobs: [job(3, { conclusion: "failure" })] });
    await h.execute();
    assert.equal(named(h, "actions.listJobsForWorkflowRunAttempt").length, 0);
    assert.equal(JSON.parse(h.writes[0].output.text).jobs, undefined);
  });
}

test("job-details accepts only true or false", () => {
  assert.equal(configuration({ FORK_CI_WORKFLOW: "ci.yml" }).jobDetails, true);
  assert.equal(configuration({ FORK_CI_WORKFLOW: "ci.yml", FORK_CI_JOB_DETAILS: "false" }).jobDetails, false);
  assert.throws(() => configuration({ FORK_CI_WORKFLOW: "ci.yml", FORK_CI_JOB_DETAILS: "yes" }));
});
