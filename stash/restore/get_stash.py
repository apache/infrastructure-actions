# Copyright (c) The stash contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import os
import subprocess
from typing import List


def print_debug(msg: str):
    """Print a message that is only visible when the GHA debug flag is set."""
    print(f"::debug::{msg}")


def set_output(name: str, value: str):
    """Set a GHA output variable."""
    with open(ensure_env_var("GITHUB_OUTPUT"), "a") as f:
        f.write(f"{name}={value}\n")


def ensure_env_var(var: str) -> str:
    """Return value of envvar `var`, throw if it's not set."""
    value = os.environ.get(var)
    if value is None or len(value) == 0:
        raise ValueError(f"Environment variable {var} is not set")
    return value


def run_checked(args, **kwargs):
    """Run command and caputre it's output and check that it exists succesfully."""
    result = subprocess.run(args, **kwargs, capture_output=True, check=True, text=True)
    return result


def jq(file: str, query: str, args: List[str] = []):
    """Wrapper to run `jq` query on a file on disk or on a JSON string."""
    if os.path.isfile(file):
        result = run_checked(["jq", *args, query, file])
    elif file.startswith("{"):
        result = run_checked(["jq", *args, query], input=file)
    else:
        raise ValueError("Input 'file' not found and not valid json string")

    return result


def gh_api(endpoint: str, method: str = "get", options: List[str] = []):
    """Wrapper to run `gh` REST API calls."""
    args = [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        f"--method={method}",
        *options,
        endpoint,
    ]
    result = run_checked(args)
    return result


def ensure_json(output: str):
    """Always return valid JSON."""
    if output.isspace():
        return json.loads("{}")
    else:
        return json.loads(output)


def get_workflow_stash(repo: str, run_id: str, name: str):
    ops = ["-q", ".artifacts | max_by(.updated_at | fromdate)", "-f", f"name={name}"]
    res = gh_api(f"repos/{repo}/actions/runs/{run_id}/artifacts", options=ops)
    print_debug(f"Returned stash: {res.stdout}")
    return ensure_json(res.stdout)


def get_branch_stash(repo: str, name: str, branch: str, repo_id: int):
    query = f"""
    .artifacts | map(select(
                    .expired == false and
                    .workflow_run.head_branch == "{branch}"
                    and .workflow_run.head_repository_id == {repo_id}))
               | max_by(.updated_at | fromdate)
    """
    ops = ["-q", query, "-f", f"name={name}"]
    res = gh_api(f"repos/{repo}/actions/artifacts", options=ops)
    print_debug(f"Returned stash: {res.stdout}")
    return ensure_json(res.stdout)
