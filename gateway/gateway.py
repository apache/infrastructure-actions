# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ruyaml",
# ]
# ///

import os
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Dict, NotRequired, TypedDict

import ruyaml

class RefDetails(TypedDict):
    """
    Type definition for reference details of GitHub Actions for actions.yml

    Attributes:
        expires_at: After this date the reference will be removed
        keep: Optional flag to retain the reference, regardless of expiry, and not start the clock on expiry when new tags appear
    """

    expires_at: date
    keep: NotRequired[bool]
    tag: NotRequired[str]


ActionRefs = Dict[str, RefDetails]
"""Dictionary mapping action references to their details"""

ActionsYAML = Dict[str, ActionRefs]
"""Dictionary mapping action names to their reference details"""


def calculate_expiry(weeks=4):
    """
    Calculate an expiration date from today.

    Args:
        weeks: Number of weeks from today (default: 4)

    Returns:
        date: The calculated expiry date
    """
    return date.today() + timedelta(weeks=weeks)


def load_yaml(path: Path) -> dict:
    """
    Load and parse a YAML file.

    Args:
        path: Path to the YAML file

    Returns:
        dict: Parsed YAML content
    """
    with open(path, "r") as file:
        yaml = ruyaml.YAML()
        actions = yaml.load(file)
    return actions


def write_yaml(path: Path, yaml_dict: dict | list):
    """
    Write data as YAML to a file using custom indentation.

    Args:
        path: Path to write the YAML file
        yaml_dict: Data to write as YAML
    """
    with open(path, "w") as file:
        yaml = ruyaml.YAML()
        yaml.dump(yaml_dict, file)

def to_yaml_string(yaml_dict: dict | list):
    yaml = ruyaml.YAML()
    stream = StringIO()
    yaml.dump(yaml_dict, stream)
    return stream.getvalue()

def write_str(path: Path, content: str):
    with open(path, "w") as file:
        file.write(content)


def on_gha():
    """
    Check if the code is running in a GitHub Actions environment.

    Returns:
        bool: True if running in GitHub Actions, False otherwise
    """
    return os.environ.get("GITHUB_ACTION") is not None


def gha_print(content: str, title: str = ""):
    """
    Print content in GitHub Actions with group formatting.
    Does nothing if not running in GitHub Actions.

    Args:
        content: The content to print
        title: Optional title for the group (default: empty string)
    """
    if not on_gha():
        return

    print(f"::group::{title}")
    print(content)
    print("::endgroup::")


def generate_workflow(actions: ActionsYAML) -> str:
    """
    Generate a GitHub workflow file as a string from the actions.yml dictionary.

    Args:
        actions: Dictionary of actions and their references

    Returns:
        str: Generated workflow file content
    """
    # Github Workflow 'yaml' has slight deviations from the yaml spec. (e.g. keys with no values)
    # Because of that it's much easier to generate this as a string rather
    # then use pyyaml to dump this from a dict.
    header = """name: Dummy Workflow

on:
  workflow_dispatch:
  pull_request:
    paths:
      - .github/workflows/dummy.yml
  push:
    paths:
      - .github/workflows/dummy.yml

permissions: {}

jobs:
  dummy:
    runs-on: ubuntu-latest
    steps:
"""
    steps = []
    for name, refs in actions.items():
        def is_updatable(ref):
            details = refs[ref]
            return len(ref) >= 40 and (
                    not details or (
                        not "keep" in details and
                        not "expires_at" in details))

        ref_to_update = list(filter(is_updatable, refs))

        if len(ref_to_update) > 1:
            raise ValueError(f"multiple candidates for auto-updates for {name}")
        elif len(ref_to_update) == 1:
            ref = ref_to_update[0]
            details = refs[ref]
            steps.append(f"      - uses: {name}@{ref}" + (f"  # {details['tag']}" if details and 'tag' in details else ''))
            steps.append( "        if: false")

    return header + "\n".join(steps) + "\n" + "      - run: echo Success!\n"


def update_refs(
    dummy_steps: list[dict[str, str]], action_refs: ActionsYAML
) -> ActionsYAML:
    """
    Update action references based on steps from a dummy workflow.

    Args:
        dummy_steps: List of steps from a dummy workflow
        action_refs: Current action references

    Returns:
        ActionsYAML: Updated action references
    """
    for step in dummy_steps:
        uses = step.get("uses", None)
        if uses is None:
            # The last step is - run:
            continue

        name, new_ref = uses.split("@")
        new_tag = None
        if hasattr(step, 'ca') and 'uses' in step.ca.items:
            new_tag = step.ca.items['uses'][2].value[1:].strip()

        if name not in action_refs:
            action_refs[name] = {}

        refs = action_refs[name]
        if new_ref not in refs:
            for _, details in refs.items():
                if not details.get("keep"):
                    new_expiry = calculate_expiry(12)
                    if "expires_at" not in details or details["expires_at"] > new_expiry:
                        details["expires_at"] = new_expiry

            refs[new_ref] = {}
            if new_tag:
                refs[new_ref]['tag'] = new_tag

    return action_refs


def update_actions(dummy_path: Path, actions_path: Path):
    """
    Update actions file based on a dummy workflow.

    Args:
        dummy_path: Path to the dummy workflow file
        actions_path: Path to the actions list file
    """
    dummy = load_yaml(dummy_path)
    steps: list[dict[str, str]] = dummy["jobs"]["dummy"]["steps"]

    actions: ActionsYAML = load_yaml(actions_path)

    update_refs(steps, actions)
    gha_print(to_yaml_string(actions), "Generated List")
    write_yaml(actions_path, actions)

def create_pattern(actions: ActionsYAML) -> list[str]:
    """
    Create a pattern list of valid action references.

    Args:
        actions: Dictionary of actions and their references

    Returns:
        list[str]: List of action patterns (name@ref)
    """
    pattern: list[str] = []

    pattern.extend(
        f"{name}@{ref}"
        for name, refs in actions.items()
        for ref, details in refs.items()
        if (not details or "expires_at" not in details or date.today() < details["expires_at"]) or details.get("keep")
    )
    return pattern


def update_patterns(pattern_path: Path, list_path: Path):
    """
    Update the patterns file based on the actions list.
    This will overwrite the existing file, so any manual changes will be lost!

    Args:
        pattern_path: Path to write the patterns file
        list_path: Path to the actions list file
    """
    actions: ActionsYAML = load_yaml(list_path)
    patterns = create_pattern(actions)
    comment = f"# This file was generated from {list_path} by gateway/gateway.py. DO NOT UPDATE MANUALLY.\n"
    patterns_str = comment + to_yaml_string(patterns)
    gha_print(patterns_str, "Generated Patterns")
    write_str(pattern_path, patterns_str)


def update_workflow(dummy_path: Path, list_path: Path):
    """
    Update the dummy workflow file based on the actions list.
    This will overwrite the existing file, so any manual changes will be lost!

    Args:
        dummy_path: Path to write the dummy workflow file
        list_path: Path to the actions list file
    """
    actions: ActionsYAML = load_yaml(list_path)
    workflow = generate_workflow(actions)
    gha_print(workflow, "Generated Workflow")
    write_str(dummy_path, workflow)


def remove_expired_refs(actions: ActionsYAML):
    """
    Remove expired references from the actions dictionary.

    Args:
        actions: Dictionary of actions and their references
    """
    refs_to_remove: list[tuple[str, str]] = []

    for name, action in actions.items():
        refs_to_remove.extend(
            (name, ref)
            for ref, details in action.items()
            if details and "expires_at" in details and details["expires_at"] <= date.today() and not details.get("keep")
        )

    # Changing the iterable during iteration raises a RuntimeError
    for name, ref in refs_to_remove:
        del actions[name][ref]

        # remove Actions without refs
        if not actions[name]:
            del actions[name]


def clean_actions(actions_path: Path):
    """
    Clean up expired actions from the actions file.

    Args:
        actions_path: Path to the actions list file
    """
    actions: ActionsYAML = load_yaml(actions_path)
    remove_expired_refs(actions)
    gha_print(to_yaml_string(actions), "Cleaned Actions")
    write_yaml(actions_path, actions)
