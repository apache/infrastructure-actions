# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ruyaml",
# ]
# ///

import os
import re
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Dict, NotRequired, TypedDict
from urllib.request import Request, urlopen
from http.client import HTTPResponse

import ruyaml
from ruyaml import CommentedMap, CommentedSeq

indefinitely = date(2050, 1, 1)

class RefDetails(TypedDict):
    """
    Type definition for reference details of GitHub Actions for actions.yml

    Attributes:
        expires_at: After this date the reference will be removed
        keep: Optional flag to retain the reference regardless of expiry
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
            return (len(ref) >= 40 and
                    not details.get("keep") and
                    details["expires_at"] == indefinitely)

        ref_to_update = list(filter(is_updatable, refs))

        if len(ref_to_update) > 1:
            raise ValueError(f"multiple candidates for auto-updates for {name}")
        elif len(ref_to_update) == 1:
            ref = ref_to_update[0]
            details = refs[ref]
            steps.append(f"      - uses: {name}@{ref}" + (f"  # {details['tag']}" if 'tag' in details else ''))
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

            refs[new_ref] = {"expires_at": indefinitely}
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
        if date.today() < details.get("expires_at") or details.get("keep")
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
            if details["expires_at"] <= date.today() and not details.get("keep")
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


re_github_actions_repo_wildcard = r"^[A-Za-z0-9-_.]+/[*]$"
re_github_actions_repo = r"^([A-Za-z0-9-_.]+/[A-Za-z0-9-_.]+)(/.+)?$"
# Something like 'pytooling/actions/with-post-step' or 'readthedocs/actions/preview'.
re_docker_image = r"^docker://.+"
re_git_sha = r"^[a-f0-9]{7,}$"

def _gh_api_get(url_abspath: str) -> HTTPResponse:
    headers: dict[str, str] = {
        'Accept': 'application/vnd.github.v3+json',
    }
    # Use GH_TOKEN, if available.
    # Unauthorized GH API requests are quite rate-limited.
    # Tip: add an extra space before 'export' to prevent adding the line to the shell history.
    #    export GH_TOKEN=$(gh auth token)
    gh_token = os.environ['GH_TOKEN']
    if gh_token:
        headers['Authorization'] = f"Bearer {gh_token}"
    request = Request(url=f"https://api.github.com{url_abspath}", headers=headers)
    return urlopen(request)

def _gh_get_commit_object(owner_repo: str, sha: str) -> HTTPResponse:
    return _gh_api_get(f"/repos/{owner_repo}/git/commits/{sha}")

def _gh_get_tag(owner_repo: str, tag_sha: str) -> HTTPResponse:
    return _gh_api_get(f"/repos/{owner_repo}/git/tags/{tag_sha}")

def _gh_matching_tags(owner_repo: str, tag: str) -> HTTPResponse:
    return _gh_api_get(f"/repos/{owner_repo}/git/matching-refs/tags/{tag}")

def verify_actions(actions_path: Path):
    """
    Validates the contents of the actions file against GitHub.

    The function verifies that the SHAs specified in `actions.yml` exist in the GH repo.
    Also ensures that the SHA exists on the Git tag, if the `tag` attribute is specified.

    The algorithm roughly works like this, for each action specified in `actions.yml`:
    * Issue a warning and stop, if the name is like `OWNER/*` ("wildcard" repository).
      Can't verify Git SHAs in this case.
    * Issue a warning and stop, if the name is like `docker:*` (not implemented)
    * Issue an error and stop, if the name doesn't start with an `OWNER/REPO` pattern.
    * Each expired entry is just skipped
    * If there is a wildcard reference and a SHA reference, issue an error.

    Then, for each reference for an action:
    * If no `tag` is specified, let GH resolve the commit SHA.
      Emit a warning to add the value of the `tag` attribute, if the SHA can be resolved.
      Otherwise, emit an error.
    * If `tag` is specified:
      * Add the SHA to the set of requested-shas-by-tag
      * Call GH's "matching-refs" endpoint for the 'tag' value
        * Emit en error, if the object type is not a tag or commit.
        * Also resolve 'tag' object types to 'commit' object types.
        * Add each returned SHA to the set of valid-shas-by-tag.
    * For each "requested tag" verify that the sets of valid and requested shas intersect. If not, emit an error.

    Args:
        actions_path: Path to the actions list file

    TODO: return reasonable values
    """
    actions: ActionsYAML = load_yaml(actions_path)

    failures: list[str] = []
    warnings: list[str] = []

    for name, action in actions.items():
        gh_repo_matcher = re.match(re_github_actions_repo, name)
        if gh_repo_matcher is not None:
            owner_repo = gh_repo_matcher.group(1)
            print(f"Checking GitHub action 'https://github.com/{owner_repo}' ...")
            valid_shas_by_tag: dict[str, set[str]] = {}
            requested_shas_by_tag: dict[str, set[str]] = {}
            has_wildcard = False
            for ref, details in action.items():
                expires_at: date = details.get('expires_at')
                # TODO consider the 'keep=true' flag?
                if expires_at < date.today():
                    # skip expired entries
                    print(f"  .. ref '{ref}' is expired, skipping")
                    continue

                if ref == '*':
                    # "wildcard" SHA - what would we...
                    print(f"  .. detected wildcard ref")
                    if len(requested_shas_by_tag) > 0:
                        m = f"GitHub action 'https://github.com/{owner_repo}' references a wildcard SHA but also has specific SHAs"
                        print(f"    .. ❌ {m}")
                        failures.append(m)
                    has_wildcard = True
                    continue
                elif re.match(re_git_sha, ref):
                    print(f"  .. detected entry with Git SHA '{ref}'")
                    if has_wildcard:
                        m = f"GitHub action 'https://github.com/{owner_repo}' references a wildcard SHA but also has specific SHAs"
                        print(f"    .. ❌ {m}")
                        failures.append(m)

                    tag: str = details.get('tag')
                    print(f"    .. collecting Gig SHAs for tag {tag}")

                    if tag is None:
                        # https://docs.github.com/en/rest/git/commits?apiVersion=2022-11-28#get-a-commit-object
                        with _gh_get_commit_object(owner_repo, ref) as response:
                            match response.status:
                                case 200:
                                    m = f"GitHub action 'https://github.com/{owner_repo}' references existing commit SHA '{ref}' but but does specify the tag name for it."
                                    print(f"    .. ⚡ {m}")
                                    warnings.append(m)
                                case 404:
                                    m = f"GitHub action 'https://github.com/{owner_repo}' references non existing commit SHA '{ref}' (HTTP/{response.status}: {response.reason})"
                                    print(f"    .. ❌ {m}")
                                    failures.append(m)
                                case _:
                                    m = f"Failed to fetch Git SHA '{ref}' from GitHub repo 'https://github.com/{owner_repo}' (HTTP/{response.status}: {response.reason})"
                                    print(f"    .. ❌ {m}")
                                    failures.append(m)
                    else:
                        if not tag in requested_shas_by_tag:
                            requested_shas_by_tag[tag] = set()
                        requested_shas_by_tag[tag].add(ref)

                        if not tag in valid_shas_by_tag:
                            valid_shas_by_tag[tag] = set()
                        valid_shas_for_tag = valid_shas_by_tag[tag]

                        # https://docs.github.com/en/rest/git/refs?apiVersion=2022-11-28#list-matching-references
                        with _gh_matching_tags(owner_repo, tag) as response:
                            if response.status == 200:
                                response_json: CommentedSeq = ruyaml.YAML().load(response)
                                for elem in response_json:
                                    tag_ref_map: CommentedMap = elem
                                    tag_object: CommentedMap = tag_ref_map["object"]
                                    tab_object_type: str = tag_object["type"]
                                    tag_object_sha: str = tag_object["sha"]
                                    print(f"      .. GH yields {tab_object_type} SHA '{tag_object_sha}' for '{tag_ref_map['ref']}'")
                                    match tab_object_type:
                                        case "tag":
                                            valid_shas_for_tag.add(tag_object_sha)
                                            # https://docs.github.com/en/rest/git/tags?apiVersion=2022-11-28#get-a-tag
                                            with _gh_get_tag(owner_repo, tag_object_sha) as response2:
                                                match response2.status:
                                                    case 200:
                                                        tag_object_sha = ruyaml.YAML().load(response2)["object"]["sha"]
                                                        valid_shas_for_tag.add(tag_object_sha)
                                                        print(f"        .. GH returns commit SHA '{tag_object_sha}' for previous tag SHA")
                                                    case 404:
                                                        print(f"        .. commit SHA '{tag_object_sha}' does not exist")
                                                        pass
                                                    case _:
                                                        m = f"Failed to fetch details for Git tag '{tag}' from GitHub repo 'https://github.com/{owner_repo}', status code: {response2.status} ${response2.reason}, headers: {response2.headers} {response2.read()}"
                                                        print(f"        .. ❌ {m}")
                                                        failures.append(m)
                                            pass
                                        case "commit":
                                            valid_shas_for_tag.add(tag_object_sha)
                                        case "branch":
                                            m = f"Branch references mentioned for Git tag '{tag}' for GitHub action 'https://github.com/{owner_repo}'"
                                            print(f"        .. ❌ {m}")
                                            failures.append(m)
                                        case _:
                                            m = f"Invalid Git object type '{tag_object['type']}' for Git tag '{tag}' in GitHub repo 'https://github.com/{owner_repo}'"
                                            print(f"        .. ❌ {m}")
                                            failures.append(m)
                                            pass

                                    # tag_ref: str = tag_ref_map["ref"]
                                    # tag_to_sha[tag_ref.replace("refs/tags/", "")] = tag_object_sha
                            else:
                                m = f"Failed to fetch Git tag '{tag}' from GitHub repo 'https://github.com/{owner_repo}' (HTTP/{response.status}: {response.reason})"
                                print(f"      .. ❌ {m}")
                                failures.append(m)
                else:
                    m = f"GitHub action 'https://github.com/{owner_repo}' references an invalid Git SHA '{ref}'"
                    print(f"      .. ❌ {m}")
                    failures.append(m)

            for req_tag, req_shas in requested_shas_by_tag.items():
                print(f"  .. checking tag '{req_tag}'")
                print(f"    .. referenced SHAs: {req_shas}")
                valid_shas = valid_shas_by_tag.get(req_tag)
                print(f"    .. verified SHAs: {valid_shas}")
                if not valid_shas:
                    m = f"GitHub action 'https://github.com/{owner_repo}' references Git tag '{req_tag}' but no SHAs for tag could be found"
                    failures.append(m)
                    print(f"  ❌ {m}")
                elif req_shas.isdisjoint(valid_shas):
                    m = f"GitHub action 'https://github.com/{owner_repo}' references Git tag '{req_tag}' via SHAs '{req_shas}' but none of those matches the valid SHAs '{valid_shas}'"
                    failures.append(m)
                    print(f"  ❌ {m}")
                else:
                    print(f"  ✅ GitHub action 'https://github.com/{owner_repo}' definition for tag '{req_tag}' is good!")

        elif re.match(re_github_actions_repo_wildcard, name):
            m =f"Ignoring '{name}' having a GitHub repository wildcard ..."
            warnings.append(m)
            print(f"⚡ {m}")

        elif re.match(re_docker_image, name):
            m =f"Ignoring '{name}' referencing a Docker image ..."
            warnings.append(m)
            print(f"⚡ {m}")

        else:
            m = f"Cannot determine action kind for '{name}'"
            failures.append(m)
            print(f"❌ {m}")

    for failure in failures:
        print(f"FAILURE: {failure}")
    for warning in warnings:
        print(f"WARN: {warning}")
