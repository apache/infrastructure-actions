#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
"""Exercise the actual composite script and the caller examples without GitHub writes."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent


def load(path):
    # BaseLoader preserves the GitHub Actions key `on` as a string.
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


@pytest.fixture
def action():
    return load(ROOT / "action.yml")


def action_environment(action, overrides):
    """Resolve the composite step's environment from its declared inputs."""
    inputs = {name: value.get("default", "") for name, value in action["inputs"].items()}
    inputs.update(overrides)
    env = dict(os.environ)
    for name, expression in action["runs"]["steps"][0]["env"].items():
        if expression == "${{ github.action_path }}":
            env[name] = str(ROOT)
        else:
            input_name = expression.removeprefix("${{ inputs.").removesuffix(" }}")
            env[name] = inputs[input_name]
    return env


def test_composite_entry_point(tmp_path, action):
    step = action["runs"]["steps"][0]
    env = action_environment(action, {"workflow": "ci.yml"})
    # Run the script from action.yml, including its absolute module import.
    # An unrelated workflow_run is a supported no-op with all outputs set.
    wrapper = tmp_path / "entry.cjs"
    wrapper.write_text(
        "const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;\n"
        f"const script = {json.dumps(step['with']['script'])};\n"
        "const outputs = {};\n"
        "const core = {setOutput: (k,v) => outputs[k] = v};\n"
        "const context = {repo: {owner:'apache',repo:'example'}, "
        "eventName:'workflow_run',payload:{workflow_run:{event:'push'}}};\n"
        "new AsyncFunction('require','github','context','core',script)"
        "(require,{},context,core).then(() => console.log(JSON.stringify(outputs)));\n"
    )
    result = subprocess.run(["node", str(wrapper)], env=env, capture_output=True, text=True, check=True)
    outputs = json.loads(result.stdout)
    assert outputs == {"processed": 0, "written": 0, "failed": 0}
    for name, definition in action["outputs"].items():
        assert definition["value"] == "${{ steps.bridge.outputs." + name + " }}"
        assert name in outputs
    assert step["with"]["github-token"] == "${{ inputs.github-token }}"
    assert "${{" not in step["with"]["script"]


def test_signal_and_bridge_share_a_name_and_do_not_checkout_pr_code():
    signal = load(ROOT / "examples/fork-ci-signal.yml")
    bridge = load(ROOT / "examples/fork-ci-status.yml")
    assert bridge["on"]["workflow_run"]["workflows"] == [signal["name"]]
    assert signal["permissions"] == {}
    assert bridge["concurrency"]["cancel-in-progress"] == "false"
    assert bridge["permissions"] == {"actions": "read", "checks": "write", "pull-requests": "read"}
    for workflow in (signal, bridge):
        assert "pull_request_target" not in workflow["on"]
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                assert "checkout" not in step.get("uses", "")
                assert "${{" not in step.get("run", "")


@pytest.mark.parametrize("event,external,base,repository,expected", [
    ("pull_request", True, "main", "apache/example", False),
    ("pull_request", False, "main", "apache/example", True),
    ("pull_request", True, "release/1", "apache/example", True),
    ("push", False, "", "apache/example", True),
    ("workflow_dispatch", False, "", "apache/example", True),
    ("merge_group", False, "", "apache/example", True),
    ("pull_request", True, "main", "contributor/example", True),
])
def test_ci_example_keeps_trusted_validation(event, external, base, repository, expected):
    workflow = load(ROOT / "examples/ci.yml")
    expression = workflow["jobs"]["test"]["if"].strip().removeprefix("${{").removesuffix("}}")
    github = {
        "event_name": event, "repository": repository, "base_ref": base,
        "event": {"pull_request": {"head": {"repo": {"full_name": "other/example" if external else repository}}}},
    }
    # This example only uses JS-compatible comparisons and boolean operators.
    result = subprocess.run(
        ["node", "-e", "const github = JSON.parse(process.argv[1]); "
         f"console.log(Boolean({expression}));", json.dumps(github)],
        capture_output=True, text=True, check=True,
    )
    assert (result.stdout.strip() == "true") == expected
    assert "merge_group" in workflow["on"]


def concurrency_groups(contexts):
    expression = load(ROOT / "examples/ci.yml")["concurrency"]["group"]
    script = r"""
    const template = JSON.parse(process.argv[1]);
    const contexts = JSON.parse(process.argv[2]);
    console.log(JSON.stringify(contexts.map(github => template.replace(
      /\$\{\{\s*(.*?)\s*\}\}/g,
      (_, expression) => new Function('github', `return (${expression});`)(github)
    ))));
    """
    result = subprocess.run(
        ["node", "-e", script, json.dumps(expression), json.dumps(contexts)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_ci_concurrency_keeps_different_prs_separate():
    first = {"workflow": "CI", "event_name": "pull_request", "repository": "apache/example",
             "event": {"pull_request": {"number": 1}}, "sha": "first", "head_ref": "feature",
             "ref": "refs/pull/1/merge"}
    updated = {**first, "sha": "second"}
    second = {**first, "event": {"pull_request": {"number": 2}}, "ref": "refs/pull/2/merge"}
    a, b, c = concurrency_groups([first, updated, second])
    assert a == b  # Superseded commits in one PR still cancel.
    assert a != c  # Two forks can both name their branch 'feature'.


def test_ci_concurrency_cancels_only_superseded_fork_pushes():
    # Empty properties model GitHub's missing-property lookup for non-PR events.
    first = {"workflow": "CI", "event_name": "push", "repository": "contributor/example",
             "event": {"pull_request": {"number": ""}}, "sha": "first", "head_ref": "",
             "ref": "refs/heads/feature"}
    updated = {**first, "sha": "second"}
    other = {**first, "ref": "refs/heads/other"}
    queue = {**first, "event_name": "merge_group", "repository": "apache/example"}
    a, b, c, d, e = concurrency_groups([first, updated, other, queue, {**queue, "sha": "second"}])
    assert a == b
    assert a != c
    assert d != e


@pytest.mark.parametrize("mode,conclusion", [
    ("success", "success"), ("disabled", "action_required"), ("error", "action_required"),
])
def test_composite_publishes_results_and_failure_outputs(tmp_path, action, mode, conclusion):
    step = action["runs"]["steps"][0]
    env = action_environment(action, {
        "workflow": "build.yaml",
        "check-name": "Build on fork",
        "base-branches": "stable",
        "guide-url": "https://example.org/contributing",
        "recent-hours": "1",
        "sweep-size": "1",
        "sweep-minutes": "5",
    })
    wrapper = tmp_path / "publish.cjs"
    wrapper.write_text(
        f"const script = {json.dumps(step['with']['script'])};\n"
        f"const mode = {json.dumps(mode)};\n" + r"""
        const assert = require('node:assert/strict');
        const outputs = {}, writes = [], errors = [];
        const sha = 'a'.repeat(40);
        const fork = {owner: {login: 'contributor'}, name: 'example', full_name: 'contributor/example'};
        const pr = {number: 7, state: 'open', updated_at: new Date().toISOString(),
          base: {ref: 'stable', repo: {full_name: 'apache/example'}},
          head: {sha, ref: 'fix', repo: fork}};
        const github = {rest: {
          pulls: {list() {}, get: async () => ({data: pr})},
          checks: {listForRef() {}, create: async payload => {writes.push(payload);}},
          actions: {
            listWorkflowRuns: async p => {
              assert.equal(p.workflow_id, 'build.yaml');
              assert.equal(p.branch, 'fix');
              assert.equal(p.head_sha, sha);
              if (mode === 'error') throw Object.assign(new Error('API failure'), {status: 503});
              return {data: {workflow_runs: mode === 'disabled' || p.event !== 'push' ? [] : [{
                id: 99, run_attempt: 1, event: 'push', status: 'completed', conclusion: 'success',
                head_sha: sha, head_branch: 'fix', head_repository: fork,
              }]}};
            },
            getWorkflow: async () => ({data: {state: 'disabled_fork'}}),
          },
        }};
        github.paginate = async method => {
          if (method === github.rest.pulls.list) return [pr];
          assert.equal(method, github.rest.checks.listForRef);
          return [];
        };
        const context = {repo: {owner: 'apache', repo: 'example'}, runId: 123,
          eventName: 'schedule', payload: {}};
        const core = {info() {}, error: s => errors.push(s),
          setOutput: (k, v) => outputs[k] = v, setFailed: () => {process.exitCode = 1;}};
        const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
        new AsyncFunction('require','github','context','core',script)(require,github,context,core)
          .then(() => console.log(JSON.stringify({outputs,writes,errors})));
        """
    )
    result = subprocess.run(["node", str(wrapper)], env=env, capture_output=True, text=True)
    assert result.returncode == (1 if mode == "error" else 0), result.stderr
    response = json.loads(result.stdout)
    assert response["outputs"] == {"processed": 1, "written": 1, "failed": int(mode == "error")}
    assert len(response["writes"]) == 1
    check = response["writes"][0]
    assert check["name"] == "Build on fork"
    assert check["external_id"] == "fork-ci:build.yaml:pr:7"
    assert check["head_sha"] == "a" * 40
    assert check["conclusion"] == conclusion
    if mode == "disabled":
        assert "https://example.org/contributing" in check["output"]["summary"]
    if mode == "error":
        assert len(response["errors"]) == 1
