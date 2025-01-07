import os
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, NotRequired, TypedDict

import yaml


class RefDetails(TypedDict):
    expires_at: date
    keep: NotRequired[bool]


ActionRefs = Dict[str, RefDetails]

ActionsYAML = Dict[str, ActionRefs]


def calculate_expiry(weeks=4):
    return date.today() + timedelta(weeks=weeks)


def load_yaml(path: Path) -> dict:
    with open(path, "r") as file:
        actions = yaml.safe_load(file)
    return actions


class IndentDumper(yaml.Dumper):

    def increase_indent(self, flow=False, indentless=False):
        return super(IndentDumper, self).increase_indent(flow, False)


def write_yaml(path: Path, yaml_dict: dict | list):
    with open(path, "w") as file:
        yaml.dump(yaml_dict, file, Dumper=IndentDumper, sort_keys=False)


def write_str(path: Path, content: str):
    with open(path, "w") as file:
        file.write(content)


def on_gha():
    return os.environ.get("GITHUB_ACTION") is not None


def gha_print(content: str, title: str = ""):
    if not on_gha():
        return

    print(f"::group::{title}")
    print(content)
    print("::endgroup::")


def generate_workflow(actions: ActionsYAML) -> str:
    # Github Workflow 'yaml' has slight deviations from the yaml spec. (e.g. keys with no values)
    # Because of that it's much easier to generate this as a string rather
    # then use pyyaml to dump this from a dict.
    header = """name: Dummy Workflow

on:
  workflow_dispatch:

jobs:
  dummy:
    if: false
    runs-on: ubuntu-latest
    steps:
"""
    steps = []
    steps.extend(
        f"      - uses: {name}@{ref}"
        for name, refs in actions.items()
        for ref, details in refs.items()
        # exclude actions that entered expiry range, use gt to also exclude actions that were expired today.
        if details["expires_at"] > calculate_expiry()
        and not details.get("keep")  # Exclude refs with "keep"
    )

    return header + "\n".join(steps)


def update_refs(
    dummy_steps: list[dict[str, str]], action_refs: ActionsYAML
) -> ActionsYAML:
    for step in dummy_steps:
        name, new_ref = step["uses"].split("@")

        if name not in action_refs:
            action_refs[name] = {}

        refs = action_refs[name]
        if new_ref not in refs:
            for ref, details in refs.items():
                # expire old versions in 4 weeks this will also bump already expired refs by 4 weeks
                # this allows projects some more time in the case of rapid releases
                # CVE releases should be handled manually by removing old versions explicitly
                details["expires_at"] = calculate_expiry(4)

            refs[new_ref] = {"expires_at": date(2100, 1, 1), "keep": False}

    return action_refs


def update_actions(dummy_path: Path, actions_path: Path):
    dummy = load_yaml(dummy_path)
    steps: list[dict[str, str]] = dummy["jobs"]["dummy"]["steps"]

    actions: ActionsYAML = load_yaml(actions_path)

    update_refs(steps, actions)
    gha_print(yaml.safe_dump(actions), "Generated List")
    write_yaml(actions_path, actions)


def create_pattern(actions: ActionsYAML) -> list[str]:
    pattern: list[str] = []

    pattern.extend(
        f"{name}@{ref}"
        for name, refs in actions.items()
        for ref, details in refs.items()
        if date.today() < details.get("expires_at") or details.get("keep")
    )
    return pattern


def update_patterns(pattern_path: Path, list_path: Path):
    actions: ActionsYAML = load_yaml(list_path)
    patterns = create_pattern(actions)
    comment = "# This is a generated file. DO NOT UPDATE MANUALLY.\n"
    patterns_str =  comment + yaml.safe_dump(patterns)
    gha_print(patterns_str , "Generated Patterns")
    write_str(pattern_path, patterns_str)


def update_workflow(dummy_path: Path, list_path: Path):
    actions: ActionsYAML = load_yaml(list_path)
    workflow = generate_workflow(actions)
    gha_print(workflow, "Generated Workflow")
    write_str(dummy_path, workflow)


def remove_expired_refs(actions: ActionsYAML):
    refs_to_remove: list[tuple[str, str]] = []

    for name, action in actions.items():
        refs_to_remove.extend(
            (name, ref)
            for ref, details in action.items()
            if details["expires_at"] <= date.today() and not details.get('keep') 
        )

    # Changing the iterable during iteration raises a RuntimeError
    for name, ref in refs_to_remove:
        del actions[name][ref]
        
        # remove Actions without refs
        if not actions[name]:
            del actions[name]


def clean_actions(actions_path: Path):
    actions: ActionsYAML = load_yaml(actions_path)
    remove_expired_refs(actions)
    gha_print(yaml.safe_dump(actions), "Cleaned Actions")
    write_yaml(actions_path, actions)
