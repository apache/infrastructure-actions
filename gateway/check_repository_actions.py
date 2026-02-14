# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ruyaml",
# ]
# ///
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
#

import fnmatch
import os
import re
import sys

from pathlib import Path

from gateway import load_yaml, on_gha

re_action = r"^([A-Za-z0-9-_.]+/[A-Za-z0-9-_.]+)(/.+)?(@(.+))?$"
re_local_file = r"^[.]/.+"


def _iter_uses_nodes(node: dict, yaml_path: str = ""):
    """
    Walk the entire YAML structure (dicts/lists/scalars) and yield every value
    whose key is exactly 'uses', along with a best-effort YAML-path string.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            next_path = f"{"" if len(yaml_path) == 0 else f"{yaml_path}."}{k}"
            if k == "uses":
                yield next_path, v
            yield from _iter_uses_nodes(v, next_path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            next_path = f"{yaml_path}[{i}]"
            yield from _iter_uses_nodes(item, next_path)
    else:
        return


def check_project_actions(repository: str | os.PathLike, approved_patterns_file: str | os.PathLike) -> None:
    """
    Check that all GitHub actions used in workflows and actions are approved.

    See GitHub documentation https://docs.github.com/en/enterprise-cloud@latest/admin/enforcing-policies/enforcing-policies-for-your-enterprise/enforcing-policies-for-github-actions-in-your-enterprise

    @param repository: Path to the repository root directory to check. YAML files under '.github/workflows' and '.github/actions' will be checked.
    @param approved_patterns_file: Path to the YAML file containing approved action patterns.
    """
    repo_root = Path(repository)
    if not repo_root.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo_root}")

    # Only consider workflows under '.github/workflows' (the only directory mentioned).
    github_dir = repo_root / ".github"
    if not github_dir.is_dir():
        print(f"No directory found at: {github_dir}")
        return

    yaml_files: list[Path] = sorted(
        [
            *github_dir.rglob("workflows/*.yml"),
            *github_dir.rglob("workflows/*.yaml"),
            *github_dir.rglob("actions/**/*.yml"),
            *github_dir.rglob("actions/**/*.yaml")
        ]
    )

    approved_patterns_yaml = load_yaml(Path(approved_patterns_file))
    if not isinstance(approved_patterns_yaml, list):
        raise ValueError(
            f"Approved patterns file {approved_patterns_file} must contain a list of strings, got {type(approved_patterns_yaml)}")
    approved_patterns: list[str] = []
    for entry in approved_patterns_yaml:
        if not isinstance(entry, str):
            raise ValueError(
                f"Approved patterns file {approved_patterns_file} must contain a list of strings, got {type(entry)}")
        for e in entry.split(","):
            approved_patterns.append(e.strip())
    print(f"There are {len(approved_patterns)} entries in the approved patterns file {approved_patterns_file}:")
    for p in sorted(approved_patterns):
        print(f"- {p}")

    print(f"Found {len(yaml_files)} workflow or action YAML file(s) under {github_dir}:")
    failures: list[str] = []
    for p in yaml_files:
        relative_path = p.relative_to(repo_root)
        print(f"Checking file {relative_path}")
        yaml = load_yaml(p)
        uses_entries = list(_iter_uses_nodes(yaml))
        for yaml_path, uses_value in uses_entries:
            matcher = re.match(re_action, uses_value)
            if matcher is not None:
                print(f"  {yaml_path}: {uses_value}")
                if uses_value.startswith("./"):
                    print(f"    ✅ Local file reference, allowing")
                elif uses_value.startswith("docker://apache/"):
                    print(f"    ✅ Apache project image, allowing")
                elif uses_value.startswith("apache/"):
                    print(f"    ✅ Apache action reference, allowing")
                elif uses_value.startswith("actions/"):
                    print(f"    ✅ GitHub action reference, allowing")
                else:
                    approved = False
                    blocked = False
                    for pattern in approved_patterns:
                        blocked = pattern.startswith("!")
                        if blocked:
                            pattern = pattern[1:]
                        matches = fnmatch.fnmatch(uses_value, pattern)
                        if matches:
                            if blocked:
                                approved = False
                                break
                            approved = True
                    if approved:
                        print(f"    ✅ Approved pattern")
                    elif blocked:
                        print(f"    ❌ Action is explicitly blocked")
                        failures.append(f"❌ {relative_path} {yaml_path}: '{uses_value}' is explicitly blocked")
                    else:
                        print(f"    ❌ Not approved")
                        failures.append(f"❌ {relative_path} {yaml_path}: '{uses_value}' is not approved")

    if on_gha():
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"# GitHub Actions verification result\n")
            f.write("\n")
            f.write("For more information visit the [ASF Infrastructure GitHub Actions Policy](https://infra.apache.org/github-actions-policy.html) page\n")
            f.write("and the [ASF Infrastructure Actions](https://github.com/apache/infrastructure-actions) repository.\n")
            f.write("\n")
            if len(failures) > 0:
                f.write(f"## Failures ({len(failures)})\n")
                for msg in failures:
                    f.write(f"{msg}\n\n")
            else:
                f.write(f"✅ Success, all action usages match the currently approved patterns.\n")

    if len(failures) > 0:
        raise Exception(f"One or more action references are not approved or explicitly blocked:\n{"\n".join(failures)}")


def run_main(args: list[str]):
    approved_patterns_file = Path(os.getcwd()) / "approved_patterns.yml"
    if len(args) > 0:
        check_path = args[0]
        if len(args) > 1:
            approved_patterns_file = args[1]
    else:
        check_path = Path(os.getcwd())
    check_project_actions(check_path, approved_patterns_file)


if __name__ == "__main__":
    run_main(sys.argv[1:])
