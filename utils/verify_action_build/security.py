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
"""Security analysis checks for GitHub Actions."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

from .console import console
from .github_client import GitHubClient
from .action_ref import (
    fetch_action_yml,
    fetch_file_from_github,
    extract_composite_uses,
    detect_action_type_from_yml,
)
from .approved_actions import find_approved_versions


# Orgs we trust to the point of not descending into their nested action graph.
TRUSTED_ORGS = {"actions", "github"}

# Exemptions file for the lock-file-presence check.  Path matches the
# convention used by approved_actions.ACTIONS_YML.
LOCK_FILE_EXEMPTIONS_YML = (
    Path(__file__).resolve().parent.parent.parent / "lock_file_exemptions.yml"
)


def _load_lock_file_exemptions(
    path: Path = LOCK_FILE_EXEMPTIONS_YML,
) -> dict[tuple[str, str], set[str]]:
    """Parse lock_file_exemptions.yml into {(org, repo): {ecosystems}}.

    Uses a minimal line-based parser rather than PyYAML to keep the dependency
    surface small (the rest of this project also avoids pulling in yaml).
    Supported subset:
        org/repo:
          - ecosystem1
          - ecosystem2
    Comments (``#``) and blank lines are ignored.  Keys are lowercased so
    lookups are case-insensitive (``Pypa/cibuildwheel`` == ``pypa/cibuildwheel``).
    """
    result: dict[tuple[str, str], set[str]] = {}
    if not path.exists():
        return result

    current: tuple[str, str] | None = None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line[0].isspace() and line.endswith(":"):
            orgrepo = line[:-1].strip().strip("'\"")
            if "/" in orgrepo:
                org, repo = orgrepo.split("/", 1)
                current = (org.lower(), repo.lower())
                result.setdefault(current, set())
            else:
                current = None
            continue
        if current is not None:
            stripped = line.lstrip()
            if stripped.startswith("- "):
                ecosystem = stripped[2:].strip().strip("'\"")
                if ecosystem:
                    result[current].add(ecosystem)
    return result


@dataclass
class VisitedAction:
    """One node in a depth-first walk of a composite action graph.

    The root action is yielded with ``depth=0`` and ``incoming_ref=None``.
    Every nested action reached via a ``uses:`` ref is yielded with the
    raw ref info (``incoming_ref``) and its parent's ``action.yml`` body
    (``parent_yml``) so checks can report how it was reached (line number,
    tag comment, etc.) without re-fetching.

    For ``local`` and ``docker-ref`` terminals the walker yields a
    minimal stub (``action_yml=None``) since there's no corresponding
    ``action.yml`` to fetch. For trusted-org refs reached at depth > 0,
    the walker yields a stub as well and does not descend — matching
    the pre-refactor behaviour of the two per-check recursions.
    """
    org: str
    repo: str
    commit_hash: str
    sub_path: str
    depth: int
    action_yml: str | None
    action_type: str  # "composite", "docker", "node<N>", "local", "docker-ref", "trusted", "unknown"
    incoming_ref: dict | None
    parent_yml: str | None
    approved: bool
    trusted: bool


def walk_actions(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    max_depth: int = 3,
) -> Iterator[VisitedAction]:
    """Walk the composite action graph depth-first in pre-order.

    Yields each unique action once (keyed by ``org/repo/sub_path@commit``).
    Fetches each ``action.yml`` at most once per call — and the shared
    ``fetch_action_yml`` cache makes it free across multiple walks within
    a single CLI run. Each check can consume the stream as a pure
    function: ``for v in walk_actions(...): do_check(v)``.

    Descent rules (matching the two legacy per-check recursions):
      * Local (``./path``) and docker (``docker://…``) refs are terminal.
      * Non-hash-pinned refs are yielded but not descended.
      * Trusted-org refs at depth > 0 are yielded as stubs and not
        descended (no ``action.yml`` fetch).
      * Otherwise composite actions — and node actions that themselves
        reference other actions — are descended.
    """
    visited: set[str] = set()

    def _walk(
        o: str, r: str, c: str, s: str, depth: int,
        incoming_ref: dict | None, parent_yml: str | None,
    ) -> Iterator[VisitedAction]:
        if incoming_ref and incoming_ref.get("is_local"):
            yield VisitedAction(
                org=o, repo=r, commit_hash=c, sub_path=s, depth=depth,
                action_yml=None, action_type="local",
                incoming_ref=incoming_ref, parent_yml=parent_yml,
                approved=True, trusted=False,
            )
            return
        if incoming_ref and incoming_ref.get("is_docker"):
            yield VisitedAction(
                org=o, repo=r, commit_hash=c, sub_path=s, depth=depth,
                action_yml=None, action_type="docker-ref",
                incoming_ref=incoming_ref, parent_yml=parent_yml,
                approved=True, trusted=False,
            )
            return

        trusted = o in TRUSTED_ORGS
        if depth > 0 and trusted:
            yield VisitedAction(
                org=o, repo=r, commit_hash=c, sub_path=s, depth=depth,
                action_yml=None, action_type="trusted",
                incoming_ref=incoming_ref, parent_yml=parent_yml,
                approved=True, trusted=True,
            )
            return

        key = f"{o}/{r}/{s}@{c}"
        if key in visited:
            return
        visited.add(key)

        action_yml = fetch_action_yml(o, r, c, s)
        action_type = "unknown"
        if action_yml:
            action_type = detect_action_type_from_yml(action_yml)

        approved = False
        if incoming_ref and incoming_ref.get("is_hash_pinned"):
            approved_list = find_approved_versions(o, r)
            approved = c in {v["hash"] for v in approved_list}

        yield VisitedAction(
            org=o, repo=r, commit_hash=c, sub_path=s, depth=depth,
            action_yml=action_yml, action_type=action_type,
            incoming_ref=incoming_ref, parent_yml=parent_yml,
            approved=approved, trusted=trusted,
        )

        if depth >= max_depth:
            return
        if not action_yml:
            return
        if incoming_ref and not incoming_ref.get("is_hash_pinned"):
            return

        for nested in extract_composite_uses(action_yml):
            yield from _walk(
                nested.get("org", ""), nested.get("repo", ""),
                nested.get("ref", ""), nested.get("sub_path", ""),
                depth + 1, nested, action_yml,
            )

    yield from _walk(org, repo, commit_hash, sub_path, 0, None, None)


def analyze_nested_actions(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    ci_mode: bool = False, gh: GitHubClient | None = None,
) -> tuple[list[str], list[dict]]:
    """Analyze actions referenced in composite steps.

    Consumes :func:`walk_actions` in pre-order so each unique nested
    action is displayed exactly once (matching the legacy ordering)
    while sharing its ``action.yml`` fetches with every other check
    that walks the same graph.

    Returns ``(warnings, checked_actions)`` where ``checked_actions``
    is a list of dicts describing each nested action inspected
    (used by the verification summary).
    """
    warnings: list[str] = []
    checked: list[dict] = []

    first = True
    header_printed = False
    for visit in walk_actions(org, repo, commit_hash, sub_path):
        # The root visit is not "nested"; it only provides the action.yml
        # from which the first layer of uses: refs were resolved. Warn if
        # its action.yml could not be fetched — the graph is unreachable.
        if first:
            first = False
            if visit.action_yml is None and visit.action_type != "trusted":
                warnings.append(
                    f"Could not fetch action.yml for "
                    f"{visit.org}/{visit.repo}@{visit.commit_hash[:12]}"
                )
            continue

        if not header_printed:
            console.print()
            console.rule("[bold]Nested Action Analysis[/bold]")
            header_printed = True

        ref_info = visit.incoming_ref or {}
        parent_yml = visit.parent_yml or ""
        indent = "  " * visit.depth

        raw = ref_info.get("raw", "")
        line = ref_info.get("line_num", 0)

        if visit.action_type == "local":
            console.print(
                f"{indent}[dim]line {line}:[/dim] [cyan]{raw}[/cyan] [dim](local action)[/dim]"
            )
            checked.append({
                "action": raw, "type": "local", "pinned": True,
                "approved": True, "status": "ok",
            })
            continue

        if visit.action_type == "docker-ref":
            console.print(
                f"{indent}[dim]line {line}:[/dim] [cyan]{raw}[/cyan] [dim](docker reference)[/dim]"
            )
            checked.append({
                "action": raw, "type": "docker-ref", "pinned": True,
                "approved": True, "status": "ok",
            })
            continue

        r_org, r_repo, r_sub = visit.org, visit.repo, visit.sub_path
        ref_str = visit.commit_hash
        display_name = f"{r_org}/{r_repo}"
        if r_sub:
            display_name += f"/{r_sub}"

        entry: dict = {
            "action": display_name, "ref": ref_str,
            "pinned": ref_info.get("is_hash_pinned", False),
            "approved": False, "type": "unknown", "status": "ok",
            "depth": visit.depth,
        }

        if not ref_info.get("is_hash_pinned"):
            console.print(
                f"{indent}[dim]line {line}:[/dim] [red]✗[/red] "
                f"{display_name}@{ref_str} [red bold](NOT hash-pinned — uses tag/branch!)[/red bold]"
            )
            warnings.append(
                f"Nested action {display_name}@{ref_str} is NOT pinned to a commit hash"
            )
            entry["status"] = "fail"
            checked.append(entry)
            continue

        entry["approved"] = visit.approved
        tag_comment = ""
        for yml_line in parent_yml.splitlines():
            if ref_str in yml_line and "#" in yml_line:
                tag_comment = yml_line.split("#", 1)[1].strip()
                break
        entry["tag"] = tag_comment

        if visit.approved:
            console.print(
                f"{indent}[dim]line {line}:[/dim] [green]✓[/green] "
                f"[link=https://github.com/{r_org}/{r_repo}/commit/{ref_str}]{display_name}@{ref_str[:12]}[/link] "
                f"[green](hash-pinned, in our approved list)[/green]"
            )
        else:
            tag_display = f" [dim]# {tag_comment}[/dim]" if tag_comment else ""
            console.print(
                f"{indent}[dim]line {line}:[/dim] [green]✓[/green] "
                f"[link=https://github.com/{r_org}/{r_repo}/commit/{ref_str}]{display_name}@{ref_str[:12]}[/link]"
                f"{tag_display} [yellow](hash-pinned, NOT in our approved list)[/yellow]"
            )
            warnings.append(
                f"Nested action {display_name}@{ref_str[:12]} is not in our approved actions list"
            )
            entry["status"] = "warn"

        entry["trusted"] = visit.trusted

        if visit.action_type == "trusted":
            # Trusted-org refs are yielded as stubs (no action.yml fetch).
            # Fetch lazily just to attach the action type for the summary;
            # the shared cache means any other check that already walked
            # this repo gets a hit here.
            trusted_yml = fetch_action_yml(r_org, r_repo, ref_str, r_sub)
            nested_type = detect_action_type_from_yml(trusted_yml) if trusted_yml else "unknown"
            entry["type"] = nested_type
            console.print(
                f"{indent}  [dim]↳ {nested_type} action "
                f"(trusted org '{r_org}' — skipping deep inspection)[/dim]"
            )
        elif visit.action_type == "composite":
            entry["type"] = "composite"
            console.print(
                f"{indent}  [dim]↳ composite action — analyzing nested steps...[/dim]"
            )
        elif visit.action_type.startswith("node"):
            entry["type"] = visit.action_type
            node_ver = visit.action_type.replace("node", "")
            main_path = ""
            if visit.action_yml:
                for yml_line in visit.action_yml.splitlines():
                    main_m = re.match(r"\s+main:\s*['\"]?(\S+?)['\"]?\s*$", yml_line)
                    if main_m:
                        main_path = main_m.group(1)
                        break
            if main_path:
                has_dist = fetch_file_from_github(r_org, r_repo, ref_str, main_path) is not None
            else:
                has_dist = fetch_file_from_github(r_org, r_repo, ref_str, "dist/index.js") is not None
            dist_status = (
                f"[green]has {main_path or 'dist/'}[/green]" if has_dist
                else "[dim]no compiled JS found[/dim]"
            )
            console.print(
                f"{indent}  [dim]↳ {visit.action_type} action (Node.js {node_ver}), {dist_status}[/dim]"
            )
            if visit.action_yml and extract_composite_uses(visit.action_yml):
                nested_uses = extract_composite_uses(visit.action_yml)
                console.print(
                    f"{indent}  [dim]↳ node action also references "
                    f"{len(nested_uses)} other action(s) — inspecting...[/dim]"
                )
        elif visit.action_type == "docker":
            entry["type"] = "docker"
            if visit.action_yml:
                for yml_line in visit.action_yml.splitlines():
                    img_m = re.search(r"image:\s*['\"]?(\S+?)['\"]?\s*$", yml_line.strip())
                    if img_m:
                        image = img_m.group(1)
                        if image.startswith("Dockerfile") or image.startswith("./"):
                            console.print(f"{indent}  [dim]↳ docker action (local Dockerfile)[/dim]")
                        elif "@sha256:" in image:
                            console.print(f"{indent}  [dim]↳ docker action, image digest-pinned[/dim]")
                        else:
                            console.print(f"{indent}  [dim]↳ docker action, image: {image}[/dim]")
                        break
        else:
            entry["type"] = visit.action_type
            console.print(f"{indent}  [dim]↳ {visit.action_type} action[/dim]")

        checked.append(entry)

    return warnings, checked


def analyze_dockerfile(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze Dockerfiles in the action for security concerns."""
    warnings: list[str] = []

    candidates = ["Dockerfile"]
    if sub_path:
        candidates.insert(0, f"{sub_path}/Dockerfile")

    found_dockerfile = False
    for path in candidates:
        content = fetch_file_from_github(org, repo, commit_hash, path)
        if content is None:
            continue
        found_dockerfile = True

        console.print()
        console.rule(f"[bold]Dockerfile Analysis ({path})[/bold]")

        lines = content.splitlines()
        from_lines = []
        suspicious_cmds = []
        stage_names: set[str] = set()

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            from_match = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$", stripped, re.IGNORECASE)
            if from_match:
                image = from_match.group(1).strip()
                stage_alias = from_match.group(2)
                from_lines.append((i, image))
                if image in stage_names:
                    console.print(
                        f"  [green]✓[/green] [dim]line {i}:[/dim] FROM {image} "
                        f"[green](multi-stage reference)[/green]"
                    )
                elif "@sha256:" in image:
                    console.print(
                        f"  [green]✓[/green] [dim]line {i}:[/dim] FROM {image} "
                        f"[green](digest-pinned)[/green]"
                    )
                elif ":" in image and not image.endswith(":latest"):
                    tag = image.split(":")[-1]
                    console.print(
                        f"  [yellow]~[/yellow] [dim]line {i}:[/dim] FROM {image} "
                        f"[yellow](tag-pinned to '{tag}', but not digest-pinned)[/yellow]"
                    )
                    warnings.append(f"Dockerfile FROM {image} is tag-pinned, not digest-pinned")
                else:
                    console.print(
                        f"  [red]✗[/red] [dim]line {i}:[/dim] FROM {image} "
                        f"[red bold](unpinned or :latest!)[/red bold]"
                    )
                    warnings.append(f"Dockerfile FROM {image} is not pinned")
                if stage_alias:
                    stage_names.add(stage_alias)
                continue

            lower = stripped.lower()
            if any(cmd in lower for cmd in ["curl ", "wget ", "git clone"]):
                if "requirements" not in lower and "pip" not in lower:
                    suspicious_cmds.append((i, stripped))
            if re.search(r"https?://(?!github\.com|pypi\.org|registry\.npmjs\.org|dl-cdn\.alpinelinux\.org)", lower):
                url_match = re.search(r"(https?://\S+)", stripped)
                if url_match:
                    suspicious_cmds.append((i, f"External URL: {url_match.group(1)}"))

        if suspicious_cmds:
            console.print()
            console.print("  [yellow]Potentially suspicious commands:[/yellow]")
            for line_num, cmd in suspicious_cmds:
                console.print(f"    [dim]line {line_num}:[/dim] [yellow]{cmd}[/yellow]")
                warnings.append(f"Dockerfile line {line_num}: {cmd[:80]}")
        elif from_lines:
            console.print(f"  [green]✓[/green] No suspicious commands detected")

    if not found_dockerfile:
        action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
        if action_yml:
            for line in action_yml.splitlines():
                m = re.search(r"image:\s*['\"]?(docker://\S+)['\"]?", line.strip())
                if m:
                    console.print()
                    console.rule("[bold]Docker Image Analysis[/bold]")
                    image = m.group(1)
                    console.print(f"  [dim]Docker image reference:[/dim] {image}")
                    if "@sha256:" in image:
                        console.print(f"  [green]✓[/green] Image is digest-pinned")
                    else:
                        console.print(f"  [yellow]![/yellow] Image is NOT digest-pinned")
                        warnings.append(f"Docker image {image} is not digest-pinned")

    return warnings


def analyze_scripts(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze scripts referenced by the action for suspicious patterns."""
    warnings: list[str] = []
    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if not action_yml:
        return warnings

    script_files: set[str] = set()

    for line in action_yml.splitlines():
        stripped = line.strip()
        if "${{" in stripped and "}}" in stripped:
            continue
        for ext in (".py", ".sh", ".bash", ".rb", ".pl"):
            matches = re.findall(r"(?<![.\w])[\w][\w./-]*" + re.escape(ext) + r"\b", stripped)
            for m in matches:
                clean = m.lstrip("./").strip("'\"")
                if "steps." in clean or "outputs." in clean or "inputs." in clean:
                    continue
                if re.search(r"https?://.*" + re.escape(m), stripped):
                    continue
                if clean and ("/" not in clean or clean.count("/") <= 2):
                    script_files.add(clean)

    dockerfile_content = fetch_file_from_github(org, repo, commit_hash, "Dockerfile")
    if sub_path:
        sub_df = fetch_file_from_github(org, repo, commit_hash, f"{sub_path}/Dockerfile")
        if sub_df:
            dockerfile_content = sub_df
    if dockerfile_content:
        for line in dockerfile_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("COPY") or stripped.startswith("ADD"):
                for ext in (".py", ".sh", ".bash"):
                    matches = re.findall(r"[\w./-]+" + re.escape(ext), stripped)
                    for m in matches:
                        clean = m.strip().lstrip("/")
                        if "/" in clean:
                            clean = clean.rsplit("/", 1)[-1]
                        if clean:
                            script_files.add(clean)
            if stripped.startswith("ENTRYPOINT") or stripped.startswith("CMD"):
                for ext in (".py", ".sh", ".bash"):
                    matches = re.findall(r"[\w./-]+" + re.escape(ext), stripped)
                    for m in matches:
                        clean = m.strip().lstrip("/")
                        if "/" in clean:
                            clean = clean.rsplit("/", 1)[-1]
                        if clean:
                            script_files.add(clean)

    if not script_files:
        return warnings

    console.print()
    console.rule("[bold]Script Analysis[/bold]")

    suspicious_patterns = [
        (r"eval\s*\(", "eval() call — potential code injection"),
        (r"exec\s*\(", "exec() call — potential code injection"),
        (r"subprocess\.call\(.*shell\s*=\s*True", "subprocess with shell=True"),
        (r"os\.system\s*\(", "os.system() call"),
        (r"base64\.b64decode|atob\(", "base64 decoding — potential obfuscation"),
        (r"\\x[0-9a-f]{2}", "hex-escaped strings — potential obfuscation"),
        (r"requests?\.(get|post|put|delete|patch)\s*\(", "HTTP request (review target URL)"),
        (r"urllib\.request", "urllib request (review target URL)"),
        (r"socket\.", "socket operations"),
    ]

    for script_path in sorted(script_files):
        base_path = f"{sub_path}/{script_path}" if sub_path else script_path
        content = fetch_file_from_github(org, repo, commit_hash, base_path)
        if content is None:
            content = fetch_file_from_github(org, repo, commit_hash, script_path)
        if content is None:
            console.print(f"  [dim]⊘ {script_path} (not found at commit)[/dim]")
            continue

        line_count = len(content.splitlines())
        console.print(
            f"  [green]✓[/green] [link=https://github.com/{org}/{repo}/blob/{commit_hash}/{base_path}]"
            f"{script_path}[/link] [dim]({line_count} lines)[/dim]"
        )

        findings: list[tuple[int, str, str]] = []
        for i, line in enumerate(content.splitlines(), 1):
            for pattern, description in suspicious_patterns:
                if re.search(pattern, line):
                    findings.append((i, description, line.strip()[:100]))

        if findings:
            seen_patterns: set[str] = set()
            for line_num, desc, snippet in findings:
                if desc not in seen_patterns:
                    seen_patterns.add(desc)
                    console.print(
                        f"    [yellow]![/yellow] [dim]line {line_num}:[/dim] "
                        f"[yellow]{desc}[/yellow]"
                    )
                    console.print(f"      [dim]{snippet}[/dim]")
            if len(findings) > len(seen_patterns):
                console.print(
                    f"    [dim]({len(findings)} total findings, "
                    f"{len(findings) - len(seen_patterns)} similar suppressed)[/dim]"
                )

    return warnings


def analyze_lock_files(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    exemptions: dict[tuple[str, str], set[str]] | None = None,
) -> list[str]:
    """Verify each detected dependency manifest has a matching lock file.

    Downstream build verification relies on a clean rebuild producing the same
    output as the published artifacts. That reproducibility depends on every
    transitive dependency being pinned — which is the lock file's job. A
    manifest (package.json, pyproject.toml, go.mod, ...) without a matching
    lock file means `npm install` / `pip install` / `go get` would resolve to
    whatever version is latest at build time, making verification impossible.

    A missing lock file when the corresponding manifest is present is
    returned as a hard error. Manifests that don't declare dependencies
    (e.g. a bare pyproject.toml with only tool config, a Rust library crate
    that conventionally doesn't commit Cargo.lock) are reported as skipped.

    ``exemptions`` maps ``(org, repo)`` to a set of ecosystem names where a
    missing lock file is tolerated — for library-first projects (cibuildwheel,
    setup-dart) that don't commit lock files per their ecosystem convention.
    Defaults to the contents of ``lock_file_exemptions.yml`` at the repo root.

    Returns a list of error strings (empty = pass).
    """
    if exemptions is None:
        exemptions = _load_lock_file_exemptions()
    exempted_ecosystems = exemptions.get((org.lower(), repo.lower()), set())

    errors: list[str] = []
    header_shown = False

    def _show_header() -> None:
        nonlocal header_shown
        if not header_shown:
            console.print()
            console.rule("[bold]Lock File Presence[/bold]")
            header_shown = True

    def _candidate_paths(name: str) -> list[str]:
        if sub_path:
            return [f"{sub_path}/{name}", name]
        return [name]

    def _find(name: str) -> tuple[str, str] | None:
        for p in _candidate_paths(name):
            c = fetch_file_from_github(org, repo, commit_hash, p)
            if c is not None:
                return p, c
        return None

    # (ecosystem, manifest, [acceptable lock files in priority order])
    ecosystems: list[tuple[str, str, list[str]]] = [
        ("node",   "package.json",   ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb"]),
        ("python", "pyproject.toml", ["uv.lock", "poetry.lock", "pdm.lock", "requirements.txt"]),
        ("python", "Pipfile",        ["Pipfile.lock"]),
        ("deno",   "deno.json",      ["deno.lock"]),
        ("deno",   "deno.jsonc",     ["deno.lock"]),
        ("dart",   "pubspec.yaml",   ["pubspec.lock"]),
        ("ruby",   "Gemfile",        ["Gemfile.lock"]),
        ("go",     "go.mod",         ["go.sum"]),
        ("rust",   "Cargo.toml",     ["Cargo.lock"]),
    ]

    for ecosystem, manifest, lock_options in ecosystems:
        found = _find(manifest)
        if found is None:
            continue
        mpath, mcontent = found

        # Rust libraries conventionally don't commit Cargo.lock — only binary
        # crates / workspaces do. Detect via [lib] without [[bin]] in the
        # root manifest (workspaces are treated as needing a lock).
        if ecosystem == "rust":
            has_lib = bool(re.search(r"(?m)^\s*\[lib\]", mcontent))
            has_bin = bool(re.search(r"(?m)^\s*\[\[bin\]\]", mcontent))
            has_workspace = bool(re.search(r"(?m)^\s*\[workspace\]", mcontent))
            if has_lib and not has_bin and not has_workspace:
                _show_header()
                console.print(
                    f"  [dim]⊘[/dim] {ecosystem}: {mpath} looks like a library crate — "
                    "Cargo.lock is not conventionally committed"
                )
                continue

        # pyproject.toml is often a bare config file (ruff/black/mypy settings)
        # with no dependencies. Skip if no deps section is declared.
        if manifest == "pyproject.toml":
            has_deps = bool(re.search(
                r"(?m)^\s*("
                r"dependencies\s*="
                r"|\[project\.optional-dependencies\]"
                r"|\[tool\.poetry\.dependencies\]"
                r"|\[tool\.poetry\.dev-dependencies\]"
                r"|\[tool\.poetry\.group\..+?\.dependencies\]"
                r"|\[tool\.pdm\.dev-dependencies\]"
                r")",
                mcontent,
            ))
            if not has_deps:
                _show_header()
                console.print(
                    f"  [dim]⊘[/dim] {ecosystem}: {mpath} declares no dependencies"
                )
                continue

        # go.mod without any `require` directives has no third-party deps and
        # thus no go.sum to generate.
        if manifest == "go.mod":
            has_require = bool(re.search(r"(?m)^\s*require\b", mcontent))
            if not has_require:
                _show_header()
                console.print(
                    f"  [dim]⊘[/dim] {ecosystem}: {mpath} has no require directives"
                )
                continue

        found_lock: str | None = None
        for lock in lock_options:
            lp = _find(lock)
            if lp is not None:
                found_lock = lp[0]
                break

        _show_header()
        manifest_link = (
            f"[link=https://github.com/{org}/{repo}/blob/{commit_hash}/{mpath}]"
            f"{mpath}[/link]"
        )
        if found_lock:
            lock_link = (
                f"[link=https://github.com/{org}/{repo}/blob/{commit_hash}/{found_lock}]"
                f"{found_lock}[/link]"
            )
            console.print(
                f"  [green]✓[/green] {ecosystem}: {manifest_link} → {lock_link}"
            )
        elif ecosystem in exempted_ecosystems:
            console.print(
                f"  [dim]⊘[/dim] {ecosystem}: {manifest_link} has no matching lock file "
                f"— exempted in lock_file_exemptions.yml (library-first project)"
            )
        else:
            console.print(
                f"  [red]✗[/red] {ecosystem}: {manifest_link} has no matching lock file "
                f"(expected one of: {', '.join(lock_options)})"
            )
            errors.append(
                f"{mpath}: missing lock file; expected one of "
                f"{', '.join(lock_options)} so transitive dependencies are pinned"
            )

    return errors


def analyze_dependency_pinning(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze dependency files for pinning practices."""
    warnings: list[str] = []

    req_candidates = [
        "requirements.txt", "requirements/runtime.txt",
        "requirements/runtime.in", "requirements/runtime-prerequisites.txt",
        "requirements/runtime-prerequisites.in",
    ]
    if sub_path:
        req_candidates = [f"{sub_path}/{r}" for r in req_candidates] + req_candidates

    found_reqs = False
    for req_path in req_candidates:
        content = fetch_file_from_github(org, repo, commit_hash, req_path)
        if content is None:
            continue

        if not found_reqs:
            console.print()
            console.rule("[bold]Dependency Pinning Analysis[/bold]")
            found_reqs = True

        lines = content.splitlines()
        total_deps = 0
        pinned_deps = 0
        unpinned_deps = []
        has_hashes = False

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            if stripped.startswith("-c "):
                continue

            total_deps += 1
            if "--hash=" in stripped or "\\$" in stripped:
                has_hashes = True

            if "==" in stripped:
                pinned_deps += 1
            elif "~=" in stripped or ">=" in stripped:
                pinned_deps += 1
                pkg_name = re.split(r"[~>=<!\s]", stripped)[0]
                if ".in" in req_path:
                    pass
                else:
                    unpinned_deps.append((pkg_name, stripped))
            else:
                pkg_name = re.split(r"[~>=<!\s\[]", stripped)[0]
                if pkg_name and not pkg_name.startswith("("):
                    unpinned_deps.append((pkg_name, stripped))

        is_compiled = req_path.endswith(".txt")
        file_type = "compiled" if is_compiled else "input"

        if total_deps > 0:
            pin_pct = (pinned_deps / total_deps) * 100
            status = "[green]✓[/green]" if pin_pct >= 90 else "[yellow]![/yellow]"
            console.print(
                f"  {status} [link=https://github.com/{org}/{repo}/blob/{commit_hash}/{req_path}]"
                f"{req_path}[/link] [dim]({file_type})[/dim]: "
                f"{pinned_deps}/{total_deps} deps pinned ({pin_pct:.0f}%)"
            )

            if unpinned_deps and is_compiled:
                for pkg, spec in unpinned_deps[:5]:
                    console.print(f"    [yellow]![/yellow] [dim]{spec}[/dim]")
                    warnings.append(f"{req_path}: {pkg} not strictly pinned")
                if len(unpinned_deps) > 5:
                    console.print(f"    [dim]... and {len(unpinned_deps) - 5} more[/dim]")

    pkg_json_path = f"{sub_path}/package.json" if sub_path else "package.json"
    content = fetch_file_from_github(org, repo, commit_hash, pkg_json_path)
    if content:
        if not found_reqs:
            console.print()
            console.rule("[bold]Dependency Pinning Analysis[/bold]")
            found_reqs = True

        try:
            pkg = json.loads(content)
            for dep_type in ("dependencies", "devDependencies"):
                deps = pkg.get(dep_type, {})
                if not deps:
                    continue
                unpinned = [
                    (name, ver) for name, ver in deps.items()
                    if not re.match(r"^\d+\.\d+\.\d+$", ver)
                ]
                total = len(deps)
                pinned = total - len(unpinned)
                pin_pct = (pinned / total) * 100 if total else 100
                status = "[green]✓[/green]" if pin_pct >= 80 else "[yellow]![/yellow]"
                console.print(
                    f"  {status} {pkg_json_path} [{dep_type}]: "
                    f"{pinned}/{total} deps exact-pinned ({pin_pct:.0f}%)"
                )
                if unpinned[:5]:
                    for name, ver in unpinned[:5]:
                        console.print(f"    [dim]{name}: {ver}[/dim]")
        except (json.JSONDecodeError, KeyError):
            pass

    lock_files = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]
    if sub_path:
        lock_files = [f"{sub_path}/{lf}" for lf in lock_files] + lock_files
    for lf_path in lock_files:
        content = fetch_file_from_github(org, repo, commit_hash, lf_path)
        if content is not None:
            if not found_reqs:
                console.print()
                console.rule("[bold]Dependency Pinning Analysis[/bold]")
                found_reqs = True
            console.print(f"  [green]✓[/green] Lock file present: {lf_path}")
            break

    return warnings


def analyze_action_metadata(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> list[str]:
    """Analyze action.yml metadata for security-relevant fields."""
    warnings: list[str] = []
    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if not action_yml:
        return warnings

    console.print()
    console.rule("[bold]Action Metadata Analysis[/bold]")

    lines = action_yml.splitlines()

    sensitive_input_patterns = [
        (r"default:\s*\$\{\{\s*secrets\.", "input defaults to a secret"),
        (r"default:\s*\$\{\{\s*github\.token", "input defaults to github.token"),
    ]
    for i, line in enumerate(lines, 1):
        for pattern, desc in sensitive_input_patterns:
            if re.search(pattern, line):
                console.print(
                    f"  [yellow]![/yellow] [dim]line {i}:[/dim] "
                    f"[yellow]{desc}[/yellow]"
                )
                console.print(f"    [dim]{line.strip()[:100]}[/dim]")
                warnings.append(f"action.yml line {i}: {desc}")

    in_run_block = False
    dangerous_shell_patterns = [
        (r"curl\s+.*\|\s*(ba)?sh", "pipe-to-shell (curl | sh) — high risk"),
        (r"wget\s+.*\|\s*(ba)?sh", "pipe-to-shell (wget | sh) — high risk"),
        (r'\$\{\{\s*inputs\.', "direct input interpolation in shell (injection risk)"),
        (r'GITHUB_ENV', "writes to GITHUB_ENV (can affect subsequent steps)"),
        (r'GITHUB_PATH', "writes to GITHUB_PATH (can affect subsequent steps)"),
        (r'GITHUB_OUTPUT', None),
    ]

    shell_findings: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"run:\s*\|", stripped) or re.match(r"run:\s+\S", stripped):
            in_run_block = True
            continue
        if in_run_block:
            if stripped and not line[0].isspace():
                in_run_block = False
            elif stripped and re.match(r"\s+\w+:", line) and not line.startswith("        "):
                if not stripped.startswith("#") and not stripped.startswith("-"):
                    in_run_block = False

        if in_run_block or (re.match(r"\s+run:\s+", line)):
            for pattern, desc in dangerous_shell_patterns:
                if desc is None:
                    continue
                if re.search(pattern, line):
                    shell_findings.append((i, desc, stripped[:100]))

    if shell_findings:
        seen: set[str] = set()
        shown = 0
        for line_num, desc, snippet in shell_findings:
            key = desc
            if key not in seen:
                seen.add(key)
                console.print(
                    f"  [yellow]![/yellow] [dim]line {line_num}:[/dim] "
                    f"[yellow]{desc}[/yellow]"
                )
                console.print(f"    [dim]{snippet}[/dim]")
                if "high risk" in desc or "injection" in desc:
                    warnings.append(f"action.yml line {line_num}: {desc}")
                shown += 1
        if len(shell_findings) > shown:
            console.print(
                f"  [dim]({len(shell_findings)} total shell findings, "
                f"{len(shell_findings) - shown} similar suppressed)[/dim]"
            )
    else:
        console.print("  [green]✓[/green] No dangerous shell patterns in run: blocks")

    env_secrets = []
    for i, line in enumerate(lines, 1):
        if re.search(r"\$\{\{\s*secrets\.", line):
            env_secrets.append((i, line.strip()[:100]))
    if env_secrets:
        console.print(f"  [dim]ℹ[/dim] Secrets referenced in {len(env_secrets)} place(s):")
        for line_num, snippet in env_secrets[:5]:
            console.print(f"    [dim]line {line_num}: {snippet}[/dim]")
    else:
        console.print("  [green]✓[/green] No secrets referenced")

    step_count = sum(1 for line in lines if re.match(r"\s+- name:", line))
    run_count = sum(1 for line in lines if re.match(r"\s+run:", line.rstrip()))
    uses_count = sum(1 for line in lines if re.match(r"\s+uses:", line.rstrip()))
    console.print(
        f"  [dim]ℹ[/dim] {step_count} step(s): "
        f"{uses_count} uses: action(s) + {run_count} run: block(s)"
    )

    return warnings


# Commands that fetch resources over HTTP(S). Dockerfile ADD <url> is also a download.
_DOWNLOAD_LINE_PATTERNS = [
    re.compile(r"\bcurl\b", re.IGNORECASE),
    re.compile(r"\bwget\b", re.IGNORECASE),
    re.compile(r"\bInvoke-WebRequest\b", re.IGNORECASE),
    re.compile(r"\biwr\b", re.IGNORECASE),
    re.compile(r"^\s*ADD\s+https?://", re.IGNORECASE),
]

# Extensions that typically indicate binary or executable downloads.
_BINARY_EXTS = (
    ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar", ".zip",
    ".exe", ".msi", ".deb", ".rpm", ".dmg", ".pkg", ".appimage",
    ".jar", ".so", ".dylib", ".dll", ".bin",
)

# Pipe-to-shell: curl/wget output piped straight into a shell interpreter.
_PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n]*\|\s*(ba|z|k|a)?sh\b",
    re.IGNORECASE,
)

# Package-manager invocations that bring their own integrity verification —
# skip these so we don't flag `curl` mentioned as an arg to a package manager.
_PKG_MANAGER_MARKERS = (
    "apt-get", "apt install", "apt update", "apt-cache",
    "apk add", "apk update", "yum install", "dnf install",
    "zypper install", "pacman -s", "brew install",
    "pip install", "pip3 install", "pipx install", "uv pip", "uv tool",
    "npm install", "npm ci", "pnpm install", "yarn install", "yarn add",
    "gem install", "go install ", "go get ", "cargo install ",
    "choco install", "scoop install",
    "tdnf install", "microdnf install",
)

# Patterns indicating cryptographic verification of a downloaded artifact.
_VERIFICATION_PATTERNS = [
    re.compile(r"\bsha(1|256|384|512)sum\b"),
    re.compile(r"\bmd5sum\b"),
    re.compile(r"\bb2sum\b"),
    re.compile(r"\bshasum\s+-a\s+(1|256|384|512)\b"),
    re.compile(r"\bopenssl\s+(dgst|sha256|sha512)", re.IGNORECASE),
    re.compile(r"\bgpg2?\b[^\n]*--verify", re.IGNORECASE),
    re.compile(r"\bcosign\s+verify", re.IGNORECASE),
    re.compile(r"\bslsa-verifier\b"),
    re.compile(r"\bgh\s+attestation\s+verify\b"),
    re.compile(r"\bminisign\b"),
    re.compile(r"\bssh-keygen\s+-Y\s+verify\b"),
    re.compile(r"\bCertUtil\b[^\n]*-hashfile", re.IGNORECASE),
    re.compile(r"\bGet-FileHash\b", re.IGNORECASE),
    # Inline checksum compare:  echo "<hash>  file" | sha256sum -c
    re.compile(r'["\'][a-f0-9]{32,}\s+\*?\S+["\']', re.IGNORECASE),
]


# Patterns indicating a JS/TS download of a remote artifact. Most JS actions
# that fetch binaries go through @actions/tool-cache's downloadTool (which
# does NOT verify checksums), or via node's http/https, fetch, axios, or
# @actions/http-client. Each of these should have a companion hash/signature
# check in the same file to count as verified.
_JS_DOWNLOAD_PATTERNS = [
    re.compile(r"\btc\.downloadTool\s*\("),
    re.compile(r"(?<![a-zA-Z_.])downloadTool\s*\("),
    re.compile(r"\bfetch\s*\([^)]*['\"`]https?://"),
    re.compile(r"\bhttps?\.(?:get|request)\s*\("),
    re.compile(r"\baxios(?:\.(?:get|post|request))?\s*\("),
    re.compile(r"\bnew\s+HttpClient\s*\("),
    re.compile(r"\brequire\(\s*['\"`]node-fetch['\"`]"),
]

# Verification patterns in JS/TS source: node crypto, WebCrypto, or common
# sigstore/cosign / custom "verify" helper names.
_JS_VERIFICATION_PATTERNS = [
    re.compile(r"\bcrypto\.createHash\s*\("),
    re.compile(r"\bcrypto\.subtle\.digest\b"),
    re.compile(r"\bsubtle\.verify\s*\("),
    re.compile(r"\b@noble/hashes\b"),
    re.compile(r"\bsigstore\b", re.IGNORECASE),
    re.compile(r"\bcosign\b", re.IGNORECASE),
    re.compile(r"\bverifySignature\b"),
    re.compile(r"\bverifyChecksum\b"),
    re.compile(r"\bcomputeHash\b"),
]

_JS_SOURCE_EXTENSIONS = (".ts", ".js", ".mjs", ".cjs")
_JS_SCAN_DIR_PREFIXES = ("src/", "lib/", "source/", "sources/", "scripts/")
_JS_EXCLUDE_DIR_PREFIXES = (
    "dist/", "build/", "out/", "node_modules/", "coverage/",
    "__tests__/", "test/", "tests/", "examples/", "example/",
    "docs/", ".github/",
)


def _line_is_pkg_manager(line: str) -> bool:
    lower = line.lower()
    return any(marker in lower for marker in _PKG_MANAGER_MARKERS)


def _find_binary_downloads_js(content: str) -> list[tuple[int, str]]:
    """Find lines in JS/TS source that fetch remote artifacts.

    Flags calls to ``tc.downloadTool`` / ``downloadTool``, bare ``fetch`` to
    an http(s) URL, node's ``http(s).get`` / ``.request``, ``axios.*``, and
    ``new HttpClient()``. Skips comment-only lines.
    """
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue
        if any(p.search(line) for p in _JS_DOWNLOAD_PATTERNS):
            findings.append((i, stripped[:120]))
    return findings


def _list_repo_files(org: str, repo: str, commit_hash: str) -> list[str]:
    """List every blob path in the repo at ``commit_hash`` via the trees API.

    Returns an empty list on error, auth failure, or truncated results (the
    caller should treat "no files discovered" as best-effort, not canonical).
    """
    url = f"https://api.github.com/repos/{org}/{repo}/git/trees/{commit_hash}?recursive=1"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, timeout=15, headers=headers)
        if not resp.ok:
            return []
        data = resp.json()
        if data.get("truncated"):
            return []
        return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"]
    except requests.RequestException:
        return []


def _discover_js_source_files(
    org: str, repo: str, commit_hash: str, sub_path: str,
) -> list[tuple[str, str]]:
    """Return ``(path, content)`` for JS/TS source files worth scanning.

    Includes files at the repo root and under conventional source dirs
    (``src/``, ``lib/``, …). Excludes compiled output, vendored modules,
    test/example dirs, and generated docs. For monorepo sub-actions the
    ``sub_path`` acts as a prefix filter.
    """
    files: list[tuple[str, str]] = []
    all_paths = _list_repo_files(org, repo, commit_hash)
    if not all_paths:
        return files

    prefix = f"{sub_path.rstrip('/')}/" if sub_path else ""
    for path in all_paths:
        if prefix and not path.startswith(prefix):
            continue
        rel = path[len(prefix):] if prefix else path
        if not rel.endswith(_JS_SOURCE_EXTENSIONS):
            continue
        if any(rel.startswith(d) for d in _JS_EXCLUDE_DIR_PREFIXES):
            continue
        if "/" in rel and not any(rel.startswith(d) for d in _JS_SCAN_DIR_PREFIXES):
            continue
        content = fetch_file_from_github(org, repo, commit_hash, path)
        if content is not None:
            files.append((rel, content))
    return files


def _find_binary_downloads(content: str) -> list[tuple[int, str]]:
    """Find lines that download binaries or scripts over HTTP(S).

    Returns a list of ``(line_num, snippet)`` tuples. Lines that are part of a
    package-manager invocation are skipped.
    """
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _line_is_pkg_manager(stripped):
            continue

        if _PIPE_TO_SHELL.search(line):
            findings.append((i, stripped[:120]))
            continue

        if not any(p.search(stripped) for p in _DOWNLOAD_LINE_PATTERNS):
            continue

        url_match = re.search(r"https?://\S+", stripped)
        if not url_match:
            continue
        url = url_match.group(0).rstrip(",;'\")}\\")

        if url.lower().endswith(_BINARY_EXTS):
            findings.append((i, stripped[:120]))
            continue
        if stripped.upper().startswith("ADD "):
            findings.append((i, stripped[:120]))
            continue
        if any(m in url for m in ("/releases/download/", "/bin/", "/binaries/", "/dist/")):
            findings.append((i, stripped[:120]))
            continue
    return findings


def _has_verification(content: str) -> bool:
    return any(p.search(content) for p in _VERIFICATION_PATTERNS)


def _extract_run_blocks(action_yml: str) -> list[str]:
    """Return the textual contents of every ``run:`` block in an action.yml."""
    blocks: list[str] = []
    lines = action_yml.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"(\s*)run:\s*[|>][+-]?\s*$", line)
        if m:
            indent = len(m.group(1))
            i += 1
            block_lines: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    block_lines.append("")
                    i += 1
                    continue
                leading = len(nxt) - len(nxt.lstrip())
                if leading <= indent:
                    break
                block_lines.append(nxt)
                i += 1
            blocks.append("\n".join(block_lines))
            continue
        m2 = re.match(r"\s*run:\s+(\S.*)$", line)
        if m2:
            blocks.append(m2.group(1))
        i += 1
    return blocks


def analyze_binary_downloads(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> tuple[list[str], list[str]]:
    """Scan Dockerfile, ``action.yml`` run blocks and referenced scripts for
    binary/script downloads that lack a detectable verification step.

    Returns ``(warnings, failures)``:
      * ``failures`` — downloads in a file with no verification patterns at all.
        These cause the check to fail.
      * ``warnings`` — downloads in a file that does contain verification
        (informational; reviewer should confirm coverage).
    """
    warnings: list[str] = []
    failures: list[str] = []

    files_to_scan: list[tuple[str, str]] = []

    df_candidates = [f"{sub_path}/Dockerfile", "Dockerfile"] if sub_path else ["Dockerfile"]
    for df_path in df_candidates:
        content = fetch_file_from_github(org, repo, commit_hash, df_path)
        if content:
            files_to_scan.append((df_path, content))
            break

    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if action_yml:
        for idx, block in enumerate(_extract_run_blocks(action_yml), start=1):
            if block.strip():
                files_to_scan.append((f"action.yml [run block #{idx}]", block))

    script_files: set[str] = set()
    if action_yml:
        for line in action_yml.splitlines():
            stripped = line.strip()
            if "${{" in stripped and "}}" in stripped:
                continue
            for ext in (".sh", ".bash", ".py", ".ps1"):
                for m in re.findall(r"(?<![.\w])[\w][\w./-]*" + re.escape(ext) + r"\b", stripped):
                    clean = m.lstrip("./").strip("'\"")
                    if any(p in clean for p in ("steps.", "outputs.", "inputs.")):
                        continue
                    if re.search(r"https?://.*" + re.escape(m), stripped):
                        continue
                    if clean and clean.count("/") <= 3:
                        script_files.add(clean)

    for script_path in sorted(script_files):
        base_path = f"{sub_path}/{script_path}" if sub_path else script_path
        content = fetch_file_from_github(org, repo, commit_hash, base_path)
        if content is None:
            content = fetch_file_from_github(org, repo, commit_hash, script_path)
        if content is not None:
            files_to_scan.append((script_path, content))

    # JS/TS source files: shell-pattern downloads (curl/wget) are rare here
    # but JS actions commonly fetch binaries via @actions/tool-cache etc.,
    # so discover those separately and scan with JS-specific patterns.
    js_files_to_scan = _discover_js_source_files(org, repo, commit_hash, sub_path)

    if not files_to_scan and not js_files_to_scan:
        return warnings, failures

    console.print()
    console.rule("[bold]Binary Download Verification[/bold]")

    any_downloads = False
    for path, content in files_to_scan:
        downloads = _find_binary_downloads(content)
        if not downloads:
            continue
        any_downloads = True
        if _has_verification(content):
            console.print(
                f"  [green]✓[/green] {path}: {len(downloads)} download(s), "
                f"verification present in file"
            )
            for line_num, snippet in downloads[:3]:
                console.print(f"    [dim]line {line_num}:[/dim] [dim]{snippet}[/dim]")
            if len(downloads) > 3:
                console.print(f"    [dim]... and {len(downloads) - 3} more[/dim]")
            for line_num, snippet in downloads:
                warnings.append(
                    f"{path} line {line_num}: download present (review coverage): {snippet[:80]}"
                )
        else:
            console.print(
                f"  [red]✗[/red] {path}: {len(downloads)} unverified download(s) "
                f"[red bold](no checksum/signature check in file)[/red bold]"
            )
            for line_num, snippet in downloads[:5]:
                console.print(f"    [dim]line {line_num}:[/dim] [red]{snippet}[/red]")
            if len(downloads) > 5:
                console.print(f"    [dim]... and {len(downloads) - 5} more[/dim]")
            for line_num, snippet in downloads:
                failures.append(
                    f"{path} line {line_num}: unverified download: {snippet[:80]}"
                )

    for path, content in js_files_to_scan:
        downloads = _find_binary_downloads_js(content)
        if not downloads:
            continue
        any_downloads = True
        has_verify = any(p.search(content) for p in _JS_VERIFICATION_PATTERNS)
        if has_verify:
            console.print(
                f"  [green]✓[/green] {path}: {len(downloads)} JS download(s), "
                f"verification present in file"
            )
            for line_num, snippet in downloads[:3]:
                console.print(f"    [dim]line {line_num}:[/dim] [dim]{snippet}[/dim]")
            if len(downloads) > 3:
                console.print(f"    [dim]... and {len(downloads) - 3} more[/dim]")
            for line_num, snippet in downloads:
                warnings.append(
                    f"{path} line {line_num}: JS download present (review coverage): {snippet[:80]}"
                )
        else:
            console.print(
                f"  [red]✗[/red] {path}: {len(downloads)} unverified JS download(s) "
                f"[red bold](no checksum/signature check in file)[/red bold]"
            )
            for line_num, snippet in downloads[:5]:
                console.print(f"    [dim]line {line_num}:[/dim] [red]{snippet}[/red]")
            if len(downloads) > 5:
                console.print(f"    [dim]... and {len(downloads) - 5} more[/dim]")
            for line_num, snippet in downloads:
                failures.append(
                    f"{path} line {line_num}: unverified JS download: {snippet[:80]}"
                )

    if not any_downloads:
        console.print("  [green]✓[/green] No binary downloads detected")

    return warnings, failures


def analyze_binary_downloads_recursive(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> tuple[list[str], list[str]]:
    """Run :func:`analyze_binary_downloads` against every action reached by
    :func:`walk_actions` starting from the given root.

    Skips the terminal stub visits (``local``/``docker-ref``/``trusted``)
    since they have no ``action.yml`` to scan. Descent into composite,
    nested-node, and non-trusted sub-actions is handled by the walker,
    so this function no longer tracks ``_depth`` or ``_visited``.
    """
    warnings: list[str] = []
    failures: list[str] = []
    for v in walk_actions(org, repo, commit_hash, sub_path):
        if v.action_type in ("local", "docker-ref", "trusted"):
            continue
        w, f = analyze_binary_downloads(v.org, v.repo, v.commit_hash, v.sub_path)
        warnings.extend(w)
        failures.extend(f)
    return warnings, failures


def analyze_repo_metadata(
    org: str, repo: str, commit_hash: str,
) -> list[str]:
    """Check repo-level signals: license, recent commits, contributor count."""
    warnings: list[str] = []

    console.print()
    console.rule("[bold]Repository Metadata[/bold]")

    for license_name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        content = fetch_file_from_github(org, repo, commit_hash, license_name)
        if content is not None:
            first_lines = content[:500].lower()
            license_type = "unknown"
            for name, pattern in [
                ("MIT", "mit license"),
                ("Apache 2.0", "apache license"),
                ("BSD", "bsd"),
                ("GPL", "gnu general public"),
                ("ISC", "isc license"),
                ("MPL", "mozilla public"),
            ]:
                if pattern in first_lines:
                    license_type = name
                    break
            console.print(f"  [green]✓[/green] License: {license_name} ({license_type})")
            break
    else:
        console.print(f"  [yellow]![/yellow] No LICENSE file found")
        warnings.append("No LICENSE file found in repository")

    for sec_name in ("SECURITY.md", ".github/SECURITY.md"):
        content = fetch_file_from_github(org, repo, commit_hash, sec_name)
        if content is not None:
            console.print(f"  [green]✓[/green] Security policy: {sec_name}")
            break
    else:
        console.print(f"  [dim]ℹ[/dim] No SECURITY.md found")

    well_known_orgs = {
        "actions", "github", "google-github-actions", "aws-actions",
        "azure", "docker", "hashicorp", "pypa", "gradle",
    }
    if org in well_known_orgs:
        console.print(f"  [green]✓[/green] Well-known org: [bold]{org}[/bold]")
    else:
        console.print(f"  [dim]ℹ[/dim] Org: {org} (not in well-known list)")

    return warnings
