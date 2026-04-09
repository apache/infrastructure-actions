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
import re

from .console import console, link
from .github_client import GitHubClient
from .action_ref import (
    fetch_action_yml,
    fetch_file_from_github,
    extract_composite_uses,
    detect_action_type_from_yml,
)
from .approved_actions import find_approved_versions


def analyze_nested_actions(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    ci_mode: bool = False, gh: GitHubClient | None = None,
    _depth: int = 0, _visited: set | None = None,
    _checked: list | None = None,
) -> tuple[list[str], list[dict]]:
    """Analyze actions referenced in composite steps, recursing into ALL types.

    Returns (warnings, checked_actions) where checked_actions is a list of dicts
    describing each nested action that was inspected (for the summary).
    """
    MAX_DEPTH = 3
    warnings: list[str] = []

    if _visited is None:
        _visited = set()
    if _checked is None:
        _checked = []

    action_key = f"{org}/{repo}/{sub_path}@{commit_hash}"
    if action_key in _visited:
        return warnings, _checked
    _visited.add(action_key)

    indent = "  " * (_depth + 1)

    action_yml = fetch_action_yml(org, repo, commit_hash, sub_path)
    if not action_yml:
        warnings.append(f"Could not fetch action.yml for {org}/{repo}@{commit_hash[:12]}")
        return warnings, _checked

    uses_refs = extract_composite_uses(action_yml)
    if not uses_refs:
        return warnings, _checked

    if _depth == 0:
        console.print()
        console.rule("[bold]Nested Action Analysis[/bold]")

    for ref_info in uses_refs:
        raw = ref_info["raw"]
        line = ref_info["line_num"]

        if ref_info.get("is_local"):
            console.print(f"{indent}[dim]line {line}:[/dim] [cyan]{raw}[/cyan] [dim](local action)[/dim]")
            _checked.append({
                "action": raw, "type": "local", "pinned": True,
                "approved": True, "status": "ok",
            })
            continue

        if ref_info.get("is_docker"):
            console.print(f"{indent}[dim]line {line}:[/dim] [cyan]{raw}[/cyan] [dim](docker reference)[/dim]")
            _checked.append({
                "action": raw, "type": "docker-ref", "pinned": True,
                "approved": True, "status": "ok",
            })
            continue

        r_org, r_repo, r_sub = ref_info["org"], ref_info["repo"], ref_info["sub_path"]
        ref_str = ref_info["ref"]
        display_name = f"{r_org}/{r_repo}"
        if r_sub:
            display_name += f"/{r_sub}"

        checked_entry: dict = {
            "action": display_name, "ref": ref_str,
            "pinned": ref_info["is_hash_pinned"],
            "approved": False, "type": "unknown", "status": "ok",
            "depth": _depth + 1,
        }

        if ref_info["is_hash_pinned"]:
            approved = find_approved_versions(r_org, r_repo)
            approved_hashes = {v["hash"] for v in approved}
            is_approved = ref_str in approved_hashes
            checked_entry["approved"] = is_approved

            tag_comment = ""
            for yml_line in action_yml.splitlines():
                if ref_str in yml_line and "#" in yml_line:
                    tag_comment = yml_line.split("#", 1)[1].strip()
                    break
            checked_entry["tag"] = tag_comment

            if is_approved:
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
                checked_entry["status"] = "warn"

            TRUSTED_ORGS = {"actions", "github"}
            is_trusted = r_org in TRUSTED_ORGS
            checked_entry["trusted"] = is_trusted

            if _depth < MAX_DEPTH:
                nested_yml = fetch_action_yml(r_org, r_repo, ref_str, r_sub)
                if nested_yml:
                    nested_type = detect_action_type_from_yml(nested_yml)
                    checked_entry["type"] = nested_type

                    if is_trusted:
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action "
                            f"(trusted org '{r_org}' — skipping deep inspection)[/dim]"
                        )
                    elif nested_type == "composite":
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action — analyzing nested steps...[/dim]"
                        )
                        nested_warnings, _ = analyze_nested_actions(
                            r_org, r_repo, ref_str, r_sub,
                            ci_mode=ci_mode, gh=gh,
                            _depth=_depth + 1, _visited=_visited,
                            _checked=_checked,
                        )
                        warnings.extend(nested_warnings)
                    elif nested_type.startswith("node"):
                        node_ver = nested_type.replace("node", "")
                        has_dist = False
                        main_path = ""
                        for yml_line in nested_yml.splitlines():
                            main_m = re.match(r"\s+main:\s*['\"]?(\S+?)['\"]?\s*$", yml_line)
                            if main_m:
                                main_path = main_m.group(1)
                                break
                        if main_path:
                            main_check = fetch_file_from_github(r_org, r_repo, ref_str, main_path)
                            has_dist = main_check is not None
                        else:
                            dist_check = fetch_file_from_github(r_org, r_repo, ref_str, "dist/index.js")
                            has_dist = dist_check is not None
                        if has_dist:
                            dist_status = f"[green]has {main_path or 'dist/'}[/green]"
                        else:
                            dist_status = "[dim]no compiled JS found[/dim]"
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action (Node.js {node_ver}), {dist_status}[/dim]"
                        )
                        nested_uses = extract_composite_uses(nested_yml)
                        if nested_uses:
                            console.print(
                                f"{indent}  [dim]↳ node action also references "
                                f"{len(nested_uses)} other action(s) — inspecting...[/dim]"
                            )
                            nested_warnings, _ = analyze_nested_actions(
                                r_org, r_repo, ref_str, r_sub,
                                ci_mode=ci_mode, gh=gh,
                                _depth=_depth + 1, _visited=_visited,
                                _checked=_checked,
                            )
                            warnings.extend(nested_warnings)
                    elif nested_type == "docker":
                        for yml_line in nested_yml.splitlines():
                            img_m = re.search(r"image:\s*['\"]?(\S+?)['\"]?\s*$", yml_line.strip())
                            if img_m:
                                image = img_m.group(1)
                                if image.startswith("Dockerfile") or image.startswith("./"):
                                    console.print(
                                        f"{indent}  [dim]↳ docker action (local Dockerfile)[/dim]"
                                    )
                                elif "@sha256:" in image:
                                    console.print(
                                        f"{indent}  [dim]↳ docker action, image digest-pinned[/dim]"
                                    )
                                else:
                                    console.print(
                                        f"{indent}  [dim]↳ docker action, image: {image}[/dim]"
                                    )
                                break
                    else:
                        console.print(
                            f"{indent}  [dim]↳ {nested_type} action[/dim]"
                        )
        else:
            console.print(
                f"{indent}[dim]line {line}:[/dim] [red]✗[/red] "
                f"{display_name}@{ref_str} [red bold](NOT hash-pinned — uses tag/branch!)[/red bold]"
            )
            warnings.append(
                f"Nested action {display_name}@{ref_str} is NOT pinned to a commit hash"
            )
            checked_entry["status"] = "fail"

        _checked.append(checked_entry)

    return warnings, _checked


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

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            from_match = re.match(r"FROM\s+(.+?)(?:\s+AS\s+\S+)?$", stripped, re.IGNORECASE)
            if from_match:
                image = from_match.group(1).strip()
                from_lines.append((i, image))
                if "@sha256:" in image:
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
