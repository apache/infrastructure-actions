# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ruyaml",
# ]
# ///

import os
import re
from urllib.error import HTTPError

import ruyaml

from datetime import date
from urllib.request import Request, urlopen
from pathlib import Path
from ruyaml import CommentedMap, CommentedSeq
from gateway import ActionsYAML, load_yaml, on_gha

re_github_actions_repo_wildcard = r"^[A-Za-z0-9-_.]+/[*]$"
re_github_actions_repo = r"^([A-Za-z0-9-_.]+/[A-Za-z0-9-_.]+)(/.+)?$"
# Something like 'pytooling/actions/with-post-step' or 'readthedocs/actions/preview'.
re_docker_image = r"^docker://.+"
re_git_sha = r"^[a-f0-9]{7,}$"

class ActionTagsCheckResult(object):
    def __init__(self, log_to_console: bool = True):
        self.log_to_console = log_to_console
        self.logs = []
        self.failures = []
        self.warnings = []

    def log(self, message: str) -> None:
        if self.log_to_console:
            print(message)
        self.logs.append(message)

    def failure(self, message: str, indent: str) -> None:
        self.log(f"{indent} ❌ {message}")
        self.failures.append(message)

    def warning(self, message: str, indent: str) -> None:
        self.log(f"{indent} ⚡ {message}")
        self.warnings.append(message)

    def has_failures(self) -> bool:
        return len(self.failures) > 0

    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def __str__(self):
        return (
            ''.join([f"FAILURE: {failure}\n" for failure in self.failures])
          + ''.join([f"WARNING: {warning}\n" for warning in self.warnings]))


class ApiResponse(object):
    def __init__(self, req_url: str, status: int, reason: str, headers: dict[str, str], body: str):
        self.req_url = req_url
        self.status = status
        self.reason = reason
        self.headers = headers
        self.body = body


def _gh_api_get(url_abspath: str) -> ApiResponse:
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
    req_url = f"https://api.github.com{url_abspath}"
    request = Request(url=req_url, headers=headers)
    try:
        with urlopen(request) as response:
            return ApiResponse(req_url, response.status, response.reason, dict(response.headers), response.read().decode('utf-8'))
    except HTTPError as e:
        return ApiResponse(req_url, e.code, e.reason, dict(e.headers), e.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to fetch '{req_url}' from GitHub API")
        raise e

def _gh_get_commit_object(owner_repo: str, sha: str) -> ApiResponse:
    return _gh_api_get(f"/repos/{owner_repo}/git/commits/{sha}")

def _gh_get_tag(owner_repo: str, tag_sha: str) -> ApiResponse:
    return _gh_api_get(f"/repos/{owner_repo}/git/tags/{tag_sha}")

def _gh_matching_tags(owner_repo: str, tag: str) -> ApiResponse:
    return _gh_api_get(f"/repos/{owner_repo}/git/matching-refs/tags/{tag}")

def verify_actions(actions: Path | ActionsYAML | str, log_to_console: bool = True, today: date = date.today()) -> ActionTagsCheckResult:
    """
    Validates the contents of the actions file against GitHub.

    The function verifies that the SHAs specified in `actions.yml` exist in the GH repo.
    Also ensures that the SHA exists on the Git tag if the `tag` attribute is specified.

    The algorithm roughly works like this, for each action specified in `actions.yml`:
    * Issue a warning and stop if the name is like `OWNER/*` ("wildcard" repository).
      Can't verify Git SHAs in this case.
    * Issue a warning and stop if the name is like `docker:*` (not implemented)
    * Issue an error and stop if the name doesn't start with an `OWNER/REPO` pattern.
    * Each expired entry is just skipped
    * If there is a wildcard reference and an SHA reference, issue an error.

    Then, for each reference for an action:
    * If no `tag` is specified, let GH resolve the commit SHA.
      Emit a warning to add the value of the `tag` attribute if the SHA can be resolved.
      Otherwise, emit an error.
    * If `tag` is specified:
      * Add the SHA to the set of requested-shas-by-tag
      * Call GitHub's "matching-refs" endpoint for the 'tag' value
        * Emit en error if the object type is not a tag or commit.
        * Also resolve 'tag' object types to 'commit' object types.
        * Add each returned SHA to the set of valid-shas-by-tag.
    * For each "requested tag" verify that the sets of valid and requested shas intersect. If not, emit an error.

    Args:
        actions: Path to the actions list file (mandatory)
        log_to_console: Whether to log messages immediately to the console (default: True)
        today: The current date (default: today)
    """
    if on_gha():
        print(f"::group::Verify GitHub Actions")
        gh_token = os.environ['GH_TOKEN']
        if not gh_token or len(gh_token) == 0:
            raise Exception("GH_TOKEN environment variable is not set or empty")

    if isinstance(actions, Path) or isinstance(actions, str):
        actions = load_yaml(actions)
    actions_yaml: ActionsYAML = actions

    result = ActionTagsCheckResult(log_to_console=log_to_console or on_gha())

    for name, action in actions_yaml.items():
        gh_repo_matcher = re.match(re_github_actions_repo, name)
        if gh_repo_matcher is not None:
            owner_repo = gh_repo_matcher.group(1)
            result.log(f"Checking GitHub action {name} in GH repo 'https://github.com/{owner_repo}'...")
            valid_shas_by_tag: dict[str, set[str]] = {}
            requested_shas_by_tag: dict[str, set[str]] = {}
            has_wildcard = False
            has_wildcard_msg_emitted = False
            # Flag whether to not error out on tag/SHA mismatches due to explicitly ignored GH API errors.
            has_ignored_api_errors = False
            for ref, details in action.items():
                if details and 'expires_at' in details:
                    expires_at: date = details.get('expires_at')
                    if expires_at < today:
                        # skip expired entries
                        result.log(f"  .. ref '{ref}' is expired, skipping")
                        continue

                # noinspection PyTypedDict
                ignore_gh_api_errors = details and 'ignore_gh_api_errors' in details and details['ignore_gh_api_errors'] == True
                if ignore_gh_api_errors:
                    result.warning(f"ignore_gh_api_errors is set to true: will ignore GH API errors for action {name} ref '{ref}'", "  ..")

                if ref == '*':
                    # "wildcard" SHA - what would we...
                    result.log(f"  .. detected wildcard ref")
                    if len(requested_shas_by_tag) > 0 and not has_wildcard_msg_emitted:
                        result.warning(f"GitHub action {name} references a wildcard SHA but also has specific SHAs", "    ..")
                        has_wildcard_msg_emitted = True
                    has_wildcard = True
                    continue
                elif re.match(re_git_sha, ref):
                    result.log(f"  .. detected entry with Git SHA '{ref}'")
                    if has_wildcard and not has_wildcard_msg_emitted:
                        result.warning(f"GitHub action {name} references a wildcard SHA but also has specific SHAs", "    ..")
                        has_wildcard_msg_emitted = True

                    if not details or not 'tag' in details:
                        result.log(f"    .. no Git tag")
                        # https://docs.github.com/en/rest/git/commits?apiVersion=2022-11-28#get-a-commit-object
                        response = _gh_get_commit_object(owner_repo, ref)
                        match response.status:
                            case 200:
                                result.warning(f"GitHub action {name} references existing commit SHA '{ref}' but does not specify the tag name for it.", "    ..")
                            case 404:
                                result.failure(f"GitHub action {name} references non existing commit SHA '{ref}': HTTP/{response.status}: {response.reason}, API URL: {response.req_url}", "    ..")
                            case _:
                                m = f"Failed to fetch Git SHA '{ref}' from GitHub repo 'https://github.com/{owner_repo}': HTTP/{response.status}: {response.reason}, API URL: {response.req_url}\n{response.body}"
                                if ignore_gh_api_errors:
                                    has_ignored_api_errors = True
                                    result.warning(m, "    ..")
                                else:
                                    result.failure(m, "    ..")
                    else:
                        tag: str = details.get('tag')
                        result.log(f"    .. collecting Git SHAs for tag {tag}")

                        if not tag in requested_shas_by_tag:
                            requested_shas_by_tag[tag] = set()
                        requested_shas_by_tag[tag].add(ref)

                        if not tag in valid_shas_by_tag:
                            valid_shas_by_tag[tag] = set()
                        valid_shas_for_tag = valid_shas_by_tag[tag]

                        # https://docs.github.com/en/rest/git/refs?apiVersion=2022-11-28#list-matching-references
                        response = _gh_matching_tags(owner_repo, tag)
                        match response.status:
                            case 200:
                                response_json: CommentedSeq = ruyaml.YAML().load(response.body)
                                for msg in response_json:
                                    tag_ref_map: CommentedMap = msg
                                    tag_object: CommentedMap = tag_ref_map["object"]
                                    tab_object_type: str = tag_object["type"]
                                    tag_object_sha: str = tag_object["sha"]
                                    result.log(f"      .. GH yields {tab_object_type} SHA '{tag_object_sha}' for '{tag_ref_map['ref']}'")
                                    match tab_object_type:
                                        case "tag":
                                            valid_shas_for_tag.add(tag_object_sha)
                                            # https://docs.github.com/en/rest/git/tags?apiVersion=2022-11-28#get-a-tag
                                            response2 = _gh_get_tag(owner_repo, tag_object_sha)
                                            match response2.status:
                                                case 200:
                                                    tag_object_sha = ruyaml.YAML().load(response2.body)["object"]["sha"]
                                                    valid_shas_for_tag.add(tag_object_sha)
                                                    result.log(f"        .. GH returns commit SHA '{tag_object_sha}' for previous tag SHA")
                                                case 404:
                                                    result.log(f"        .. commit SHA '{tag_object_sha}' does not exist")
                                                case _:
                                                    m = f"Failed to fetch details for Git tag '{tag}' from GitHub repo 'https://github.com/{owner_repo}': HTTP/{response2.status}: {response2.reason}, API URL: {response2.req_url}\n{response2.body}"
                                                    if ignore_gh_api_errors:
                                                        has_ignored_api_errors = True
                                                        result.warning(m, "        ..")
                                                    else:
                                                        result.failure(m, "        ..")
                                        case "commit":
                                            valid_shas_for_tag.add(tag_object_sha)
                                        case "branch":
                                            result.failure(f"Branch references mentioned for Git tag '{tag}' for GitHub action {name}", "        ..")
                                        case _:
                                            result.failure(f"Invalid Git object type '{tag_object['type']}' for Git tag '{tag}' in GitHub repo 'https://github.com/{owner_repo}'", "        ..")
                            case _:
                                m = f"Failed to fetch matching Git tags for '{tag}' from GitHub repo 'https://github.com/{owner_repo}': HTTP/{response.status}: {response.reason}, API URL: {response.req_url}\n{response.body}"
                                if ignore_gh_api_errors:
                                    result.warning(m, "      ..")
                                    has_ignored_api_errors = True
                                else:
                                    result.failure(m, "      ..")
                else:
                    result.failure(f"GitHub action {name} references an invalid Git SHA '{ref}'", "      ..")

            for req_tag, req_shas in requested_shas_by_tag.items():
                result.log(f"  .. checking tag '{req_tag}'")
                result.log(f"    .. referenced SHAs: {req_shas}")
                valid_shas = valid_shas_by_tag.get(req_tag)
                result.log(f"    .. verified SHAs: {valid_shas if len(valid_shas)>0 else '(none)'}")
                if not valid_shas:
                    m = f"GitHub action {name} references Git tag '{req_tag}' via SHAs '{req_shas}' but no SHAs for tag could be found - does the Git tag exist?"
                    if has_ignored_api_errors:
                        result.warning(m, "")
                    else:
                        result.failure(m, "")
                elif req_shas.isdisjoint(valid_shas):
                    m = f"GitHub action {name} references Git tag '{req_tag}' via SHAs '{req_shas}' but none of those matches the valid SHAs '{valid_shas}'"
                    result.failure(m, "")
                else:
                    result.log(f"  ✅ GitHub action {name} definition for tag '{req_tag}' is good!")

        elif re.match(re_github_actions_repo_wildcard, name):
            result.warning(f"Ignoring '{name}' because it uses a GitHub repository wildcard ...", "")

        elif re.match(re_docker_image, name):
            result.warning(f"Ignoring '{name}' because it references a Docker image ...", "")

        else:
            m = f"Cannot determine action kind for '{name}'"
            result.failure(m, "")

    if on_gha():
        if result.has_failures() or result.has_warnings():
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write(f"# GitHub Actions verification result\n")
                if len(result.failures) > 0:
                    f.write(f"## Failures ({len(result.failures)})\n")
                    f.write('```\n')
                    for msg in result.failures:
                        f.write(f"{msg}\n\n")
                    f.write('```\n')
                if len(result.warnings) > 0:
                    f.write(f"## Warnings ({len(result.warnings)})\n")
                    f.write('```\n')
                    for msg in result.warnings:
                        f.write(f"{msg}\n\n")
                f.write('```\n')
                f.write(f"## Log\n")
                f.write('```\n')
                for msg in result.logs:
                    f.write(f"{msg}\n")
                f.write('```\n')
        print("::endgroup::")

    return result
