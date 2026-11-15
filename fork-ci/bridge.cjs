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

// Derived from apache/datafusion's fork_ci_status.yml at
// 350ca76505c9513d16e7dd9910bcd9bde2c911ed. No PR code or artifacts are loaded.
const CONCLUSIONS = new Set([
  "action_required", "cancelled", "failure", "neutral", "skipped", "success", "timed_out",
]);
const EVENTS = new Set(["push", "workflow_dispatch"]);
const equalRepo = (a, b) => typeof a === "string" && typeof b === "string" &&
  a.toLowerCase() === b.toLowerCase();

/** Validate the inputs supplied through the composite step's environment. */
function configuration(env = process.env) {
  const workflow = env.FORK_CI_WORKFLOW || "";
  if (!/^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml$/.test(workflow)) {
    throw new Error("workflow must be a file name such as ci.yml");
  }
  const checkName = env.FORK_CI_CHECK_NAME || "CI (fork)";
  if (checkName.length > 100 || /[\r\n]/.test(checkName)) {
    throw new Error("check-name must be one line of at most 100 characters");
  }
  const reporting = env.FORK_CI_REPORTING || "check-run";
  if (!["check-run", "commit-status"].includes(reporting)) {
    throw new Error("reporting must be check-run or commit-status");
  }
  const baseBranches = (env.FORK_CI_BASE_BRANCHES || "main")
    .split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!baseBranches.length) throw new Error("base-branches must contain an exact branch name");
  const number = (key, fallback, min, max) => {
    const n = Number(env[key] || fallback);
    if (!Number.isInteger(n) || n < min || n > max) {
      throw new Error(`${key} must be an integer between ${min} and ${max}`);
    }
    return n;
  };
  const guide = env.FORK_CI_GUIDE_URL || "";
  if (guide && (new URL(guide).protocol !== "https:" || /[\s<>]/.test(guide))) {
    throw new Error("guide-url must be an HTTPS URL without whitespace");
  }
  return {
    workflow, checkName, reporting, baseBranches, guide,
    recentHours: number("FORK_CI_RECENT_HOURS", 6, 1, 168),
    sweepSize: number("FORK_CI_SWEEP_SIZE", 25, 1, 100),
    sweepMinutes: number("FORK_CI_SWEEP_MINUTES", 15, 5, 1440),
  };
}

function eligible(pr, upstream, config) {
  return pr?.state === "open" && Boolean(pr.head?.repo) &&
    equalRepo(pr.base?.repo?.full_name, upstream) &&
    !equalRepo(pr.head.repo.full_name, upstream) &&
    config.baseBranches.includes(pr.base.ref);
}

// Missing or malformed metadata must trigger a refresh of an owned check.
function checkMetadata(check) {
  try {
    return JSON.parse(check?.output?.text || "null");
  } catch {
    return null;
  }
}

/** Synchronize eligible PRs using the client and context from github-script. */
async function bridge({ github, context, core, config = configuration(), now = Date.now() }) {
  if (!["schedule", "workflow_dispatch", "workflow_run"].includes(context.eventName)) {
    throw new Error("fork-ci requires schedule, workflow_dispatch, or workflow_run");
  }
  const upstream = context.repo;
  const upstreamName = `${upstream.owner}/${upstream.repo}`;
  const server = context.serverUrl || "https://github.com";
  const listing = { ...upstream, state: "open", per_page: 100 };
  const counts = { processed: 0, written: 0, failed: 0 };
  const useStatus = config.reporting === "commit-status";
  const identity = (pr) => `fork-ci:${config.workflow}:pr:${pr.number}`;
  // A commit can be the head of several PRs with different fork/branch runs.
  const statusContext = (pr) => `${config.checkName} (PR #${pr.number})`;
  const forkRoot = (pr) => `${server}/${encodeURIComponent(pr.head.repo.owner.login)}/${encodeURIComponent(pr.head.repo.name)}`;
  const metadata = (pr, run) => JSON.stringify({
    version: 1, pr: pr.number, repository: pr.head.repo.full_name,
    workflow: config.workflow, head_sha: pr.head.sha,
    run_id: run?.id ?? null, run_attempt: run?.run_attempt ?? null,
  });
  const explanation = "This check reports contributor-controlled CI for the PR head. " +
    "It does not verify the merge result and must not replace trusted required checks.";

  function log(pr, message, recorded) {
    const run = recorded?.run_id == null ? "fork run none" :
      `fork run ${recorded.run_id} attempt ${recorded.run_attempt ?? "unknown"}`;
    core.info(`PR #${pr.number} (${pr.head?.sha?.slice(0, 12) || "unknown"}): ${run}; ${message}.`);
  }

  function statusMetadata(pr, report) {
    const prefix = `${forkRoot(pr)}/actions/runs/`;
    if (!report?.target_url?.startsWith(prefix)) return null;
    const match = report.target_url.slice(prefix.length).match(/^(\d+)\/attempts\/(\d+)$/);
    return match ? { run_id: match[1], run_attempt: match[2] } : null;
  }

  async function latestStatus(pr) {
    // The statuses endpoint is newest-first. Stop at the first matching
    // context instead of reading its entire append-only history.
    const statuses = await github.paginate(github.rest.repos.listCommitStatusesForRef, {
      ...upstream, ref: pr.head.sha, per_page: 100,
    }, ({ data }, done) => {
      const status = data.find((s) => s.context.toLowerCase() === statusContext(pr).toLowerCase());
      if (status) done();
      return status ? [status] : [];
    });
    return statuses[0];
  }

  function commitStatus(pr, payload) {
    const recorded = checkMetadata(payload);
    const found = recorded.run_id !== null;
    let state = "pending";
    if (payload.status === "completed") {
      if (!found) state = payload.conclusion === "neutral" ? "pending" : "error";
      else if (payload.conclusion === "success") state = "success";
      else state = ["failure", "timed_out"].includes(payload.conclusion) ? "failure" : "error";
    }
    const attempt = recorded.run_attempt ?? 1;
    return {
      context: statusContext(pr), state,
      description: found
        ? `Fork run ${recorded.run_id} attempt ${attempt}: ${payload.status === "completed" ? payload.conclusion : payload.status}`
        : payload.output.title,
      target_url: found ? `${payload.details_url}/attempts/${attempt}` : payload.details_url,
    };
  }

  async function latestCheck(pr) {
    const checks = await github.paginate(github.rest.checks.listForRef, {
      ...upstream, ref: pr.head.sha, check_name: config.checkName, filter: "all", per_page: 100,
    });
    // A same-name check from another app or PR is not ours to update.
    return checks.filter((c) => c.name === config.checkName &&
      c.app?.slug === "github-actions" && c.external_id === identity(pr))
      .sort((a, b) => b.id - a.id)[0];
  }

  async function forkResult(pr) {
    const fork = { owner: pr.head.repo.owner.login, repo: pr.head.repo.name };
    const root = forkRoot(pr);
    const workflowUrl = `${root}/actions/workflows/${encodeURIComponent(config.workflow)}`;
    // Query each accepted event separately: unrelated events cannot hide a
    // matching push behind the API's 1,000-result filtered-search limit.
    const results = await Promise.allSettled([...EVENTS].map(async (event) => {
      const { data } = await github.rest.actions.listWorkflowRuns({
        ...fork, workflow_id: config.workflow, head_sha: pr.head.sha,
        branch: pr.head.ref, event, per_page: 1,
      });
      return data.workflow_runs;
    }));
    const runs = [];
    for (const result of results) {
      if (result.status === "fulfilled") runs.push(...result.value);
      else if (result.reason.status !== 404) throw result.reason;
    }
    // Verify the response too. A merge-ref run or another branch at the same
    // commit is not the contributor's branch-head run.
    const run = runs.filter((r) => r.head_sha === pr.head.sha &&
      r.head_branch === pr.head.ref && equalRepo(r.head_repository?.full_name, pr.head.repo.full_name) &&
      EVENTS.has(r.event)).sort((a, b) => b.id - a.id)[0];
    if (run) {
      const status = run.status === "completed" ? "completed" :
        run.status === "in_progress" ? "in_progress" : "queued";
      const conclusion = CONCLUSIONS.has(run.conclusion) ? run.conclusion : "failure";
      const details = `${root}/actions/runs/${run.id}`;
      return {
        status, ...(status === "completed" ? { conclusion } : {}), details_url: details,
        output: {
          title: `Fork run ${status === "completed" ? conclusion : status}`,
          summary: `[Open the fork workflow run](${details}).\n\n${explanation}`,
          text: metadata(pr, run),
        },
      };
    }
    let state;
    try {
      state = (await github.rest.actions.getWorkflow({ ...fork, workflow_id: config.workflow })).data.state;
    } catch (error) {
      if (error.status !== 404) throw error;
    }
    // A fork that never enabled Actions, or lacks the workflow file, returns 404.
    // Either way the contributor has to act, so it is not a neutral result.
    const missing = state === undefined;
    const disabled = typeof state === "string" && state.startsWith("disabled");
    const advice = missing
      ? "The fork has no visible workflow. Enable workflows in the fork's Actions tab, and sync the fork " +
        "if its default branch lacks the workflow file. Then push again or dispatch the workflow on the PR branch."
      : disabled
        ? "Enable workflows in the fork's Actions tab, then push again or dispatch the workflow on the PR branch."
        : "No matching run is visible. Branch or path filters can prevent a run. " +
          "A new run can also take time to appear.";
    return {
      status: "completed", conclusion: missing || disabled ? "action_required" : "neutral",
      details_url: workflowUrl,
      output: {
        title: missing ? "Workflow not available on the fork" :
          disabled ? "Workflows are disabled on the fork" : "No fork run for this commit",
        summary: `[Open the fork workflow](${workflowUrl}).\n\n${advice}\n\nThe scheduled sweep will look again.\n\n${explanation}` +
          (config.guide ? `\n\nContributor guide: <${config.guide}>` : ""),
        text: metadata(pr, null),
      },
    };
  }

  async function publish(pr, check, payload) {
    const recorded = checkMetadata(payload);
    const status = useStatus ? commitStatus(pr, payload) : null;
    // GitHub keeps the check page as details_url for GITHUB_TOKEN writes, so a
    // stored check never matches the requested link. Each summary carries its link.
    const unchanged = useStatus
      ? check && ["state", "description", "target_url"].every((key) => check[key] === status[key]) :
      check && check.status === payload.status &&
      (check.conclusion ?? null) === (payload.conclusion ?? null) &&
      ["title", "summary", "text"].every((key) => check.output?.[key] === payload.output[key]);
    if (unchanged) {
      const state = useStatus ? `commit status ${status.state}` : `check ${payload.status}/${payload.conclusion || "none"}`;
      log(pr, `skipped unchanged ${state}`, recorded);
      return;
    }
    // Re-read before writing: list results and notification payloads can be
    // stale after a push, retarget, close, or repository deletion.
    const { data: current } = await github.rest.pulls.get({ ...upstream, pull_number: pr.number });
    if (
      !eligible(current, upstreamName, config) || current.head.sha !== pr.head.sha ||
      current.head.ref !== pr.head.ref || !equalRepo(current.head.repo.full_name, pr.head.repo.full_name)
    ) {
      log(pr, "skipped write because the PR state, branch, or head changed", recorded);
      return;
    }
    if (useStatus) {
      await github.rest.repos.createCommitStatus({ ...upstream, sha: pr.head.sha, ...status });
      counts.written++;
      log(pr, `published commit status ${status.state}`, recorded);
      return;
    }
    // A new check for a new attempt avoids carrying a terminal conclusion
    // into an active rerun. Ordinary polling updates the same check.
    const next = JSON.parse(payload.output.text);
    const previous = checkMetadata(check);
    const newAttempt = next.run_id !== null &&
      (next.run_id !== previous?.run_id || next.run_attempt !== previous?.run_attempt);
    const create = !check || (check.status === "completed" && (newAttempt || payload.status !== "completed"));
    if (create) {
      await github.rest.checks.create({
        ...upstream, name: config.checkName, head_sha: pr.head.sha, external_id: identity(pr), ...payload,
      });
    } else {
      await github.rest.checks.update({ ...upstream, check_run_id: check.id, ...payload });
    }
    counts.written++;
    log(pr, `${create ? "created" : "updated"} check ${payload.status}/${payload.conclusion || "none"}`, recorded);
  }

  async function sync(pr, includeCompleted) {
    const check = await (useStatus ? latestStatus(pr) : latestCheck(pr));
    const recorded = useStatus ? statusMetadata(pr, check) : checkMetadata(check);
    const completed = useStatus ? check && check.state !== "pending" : check?.status === "completed";
    if (!includeCompleted && completed && recorded?.run_id) {
      log(pr, "skipped completed fork result until its rotating sweep", recorded);
      return;
    }
    let payload;
    try {
      payload = await forkResult(pr);
    } catch (error) {
      // Do not turn permission errors, rate limits, or outages into a missing
      // run or leave an old green result as the latest observation.
      const bridgeRun = `${server}/${upstreamName}/actions/runs/${context.runId}`;
      await publish(pr, check, {
        status: "completed", conclusion: "action_required",
        details_url: bridgeRun,
        output: {
          title: "Cannot read fork CI",
          summary: `[Open the bridge run](${bridgeRun}).\n\n` +
            "The bridge could not read the fork API. Inspect the bridge workflow permissions and logs, then retry.\n\n" + explanation,
          text: metadata(pr, null),
        },
      });
      throw error;
    }
    await publish(pr, check, payload);
  }

  let candidates;
  let sweepNumbers;
  if (context.eventName === "workflow_run") {
    const run = context.payload.workflow_run;
    // The signal is an upstream pull_request workflow, not the fork CI run.
    // Never trust its conclusion, artifacts, or its often-empty PR array.
    if (run?.event !== "pull_request" || !run.head_repository?.owner?.login || !run.head_branch) {
      core.info("Fork CI: skipped signal without pull_request fork/branch metadata.");
      return counts;
    }
    candidates = (await github.paginate(github.rest.pulls.list, {
      ...listing, head: `${run.head_repository.owner.login}:${run.head_branch}`,
    })).filter((pr) => equalRepo(pr.head?.repo?.full_name, run.head_repository.full_name) &&
      pr.head.ref === run.head_branch && pr.head.sha === run.head_sha);
  } else {
    // Read every page. A hard PR limit can permanently starve older PRs.
    candidates = (await github.paginate(github.rest.pulls.list, {
      ...listing, sort: "created", direction: "asc",
    })).filter((pr) => eligible(pr, upstreamName, config));
    const slices = Math.max(1, Math.ceil(candidates.length / config.sweepSize));
    const slot = Math.floor(now / (config.sweepMinutes * 60_000)) % slices;
    sweepNumbers = new Set(candidates.slice(slot * config.sweepSize, (slot + 1) * config.sweepSize).map((pr) => pr.number));
    const cutoff = now - config.recentHours * 3_600_000;
    candidates = candidates.filter((pr) => sweepNumbers.has(pr.number) || Date.parse(pr.updated_at) >= cutoff);
  }
  for (const pr of candidates) {
    if (!eligible(pr, upstreamName, config)) continue;
    counts.processed++;
    try {
      await sync(pr, !sweepNumbers || sweepNumbers.has(pr.number));
    } catch (error) {
      counts.failed++;
      // API response bodies can contain untrusted text. Log only status and PR.
      core.error(`PR #${pr.number} (${pr.head.sha.slice(0, 12)}): fork CI sync failed (HTTP ${Number(error.status) || "unknown"}).`);
    }
  }
  if (counts.failed) core.setFailed(`Could not synchronize ${counts.failed} pull request(s). Other pull requests were processed.`);
  core.info(`Fork CI: ${counts.processed} processed, ${counts.written} ${useStatus ? "statuses" : "checks"} written, ${counts.failed} failed.`);
  return counts;
}

module.exports = { bridge, configuration, eligible };
