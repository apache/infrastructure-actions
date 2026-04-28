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
"""Docker-based action building and artifact extraction."""

import re
import subprocess
from pathlib import Path

import requests

from rich.panel import Panel
from rich.table import Table

from .console import console, link, run
from .github_client import GitHubClient

# Path to the Dockerfile template shipped with this package
_DOCKERFILE_PATH = Path(__file__).resolve().parent / "dockerfiles" / "build_action.Dockerfile"


def _read_dockerfile_template() -> str:
    """Read the Dockerfile template from the package's dockerfiles directory."""
    return _DOCKERFILE_PATH.read_text()


def detect_node_version(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
    gh: GitHubClient | None = None,
) -> str:
    """Detect the Node.js major version from the action's using: field.

    Fetches action.yml from GitHub at the given commit and extracts the
    node version (e.g. 'node20' -> '20').  Falls back to '20' if detection fails.
    """
    candidates = []
    if sub_path:
        candidates.extend([f"{sub_path}/action.yml", f"{sub_path}/action.yaml"])
    candidates.extend(["action.yml", "action.yaml"])

    for path in candidates:
        url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
        try:
            resp = requests.get(url, timeout=10)
            if not resp.ok:
                continue
            for line in resp.text.splitlines():
                match = re.match(r"\s+using:\s*['\"]?(node\d+)['\"]?", line)
                if match:
                    version = match.group(1).replace("node", "")
                    return version
        except requests.RequestException:
            continue

    return "20"


def _print_docker_build_steps(build_result: subprocess.CompletedProcess[str]) -> None:
    """Parse and display Docker build step summaries from --progress=plain output."""
    build_output = build_result.stderr + build_result.stdout
    step_names: dict[str, str] = {}
    step_status: dict[str, str] = {}
    for line in build_output.splitlines():
        m = re.match(r"^#(\d+)\s+(\[.+)", line)
        if m:
            step_names[m.group(1)] = m.group(2)
            continue
        m = re.match(r"^#(\d+)\s+(DONE\s+[\d.]+s|CACHED)", line)
        if m:
            step_status[m.group(1)] = m.group(2)

    if step_names:
        console.print()
        console.rule("[bold blue]Docker build steps[/bold blue]")
        for sid in sorted(step_names, key=lambda x: int(x)):
            name = step_names[sid]
            status_str = step_status.get(sid, "")
            if "CACHED" in status_str:
                console.print(f"  [dim]✓ {name} (cached)[/dim]")
            else:
                console.print(f"  [green]✓[/green] {name} [dim]{status_str}[/dim]")
        console.print()


def build_in_docker(
    org: str, repo: str, commit_hash: str, work_dir: Path,
    sub_path: str = "",
    gh: GitHubClient | None = None,
    cache: bool = True,
    show_build_steps: bool = False,
    approved_hash: str = "",
    source_commit_hash: str = "",
) -> tuple[Path, Path, str, str, bool, Path, Path, list[str]]:
    """Build the action in a Docker container and extract original + rebuilt dist.

    When *approved_hash* is supplied the Docker build restores package lock files
    from that commit so the rebuild uses the same dev-dependency versions that
    produced the original dist/.

    When *source_commit_hash* is supplied the Docker build captures the original
    dist/ from *commit_hash* (a source-detached release tag) and then switches
    the tree to *source_commit_hash* before building.  Used for actions whose
    tagged commit is an orphan tree without buildable source.

    Returns (original_dir, rebuilt_dir, action_type, out_dir_name,
             has_node_modules, original_node_modules, rebuilt_node_modules,
             kept_js_files).
    *kept_js_files* contains repo-root-relative paths (e.g. ``dist/post.js``)
    of non-minified compiled JS files that were preserved during the
    pre-rebuild deletion step — these are diffed against the previously
    approved version instead of the rebuild.
    """
    repo_url = f"https://github.com/{org}/{repo}.git"
    container_name = f"verify-action-{org}-{repo}-{commit_hash[:12]}"

    dockerfile_path = work_dir / "Dockerfile"
    dockerfile_path.write_text(_read_dockerfile_template())

    original_dir = work_dir / "original-dist"
    rebuilt_dir = work_dir / "rebuilt-dist"
    original_dir.mkdir(exist_ok=True)
    rebuilt_dir.mkdir(exist_ok=True)

    image_tag = f"verify-action:{org}-{repo}-{commit_hash[:12]}"

    action_display = f"{org}/{repo}"
    if sub_path:
        action_display += f"/{sub_path}"

    repo_link = link(f"https://github.com/{org}/{repo}", action_display)
    commit_link = link(f"https://github.com/{org}/{repo}/commit/{commit_hash}", commit_hash)

    info_table = Table(show_header=False, box=None, padding=(0, 1))
    info_table.add_column(style="bold")
    info_table.add_column()
    info_table.add_row("Action", repo_link)
    info_table.add_row("Commit", commit_link)
    if source_commit_hash:
        source_link = link(
            f"https://github.com/{org}/{repo}/commit/{source_commit_hash}",
            source_commit_hash,
        )
        info_table.add_row("Source commit", source_link)
    console.print()
    console.print(Panel(info_table, title="Action Build Verification", border_style="blue"))

    node_version = detect_node_version(org, repo, commit_hash, sub_path, gh=gh)
    if node_version != "20":
        console.print(f"  [green]✓[/green] Detected Node.js version: [bold]node{node_version}[/bold]")

    docker_build_cmd = [
        "docker",
        "build",
        "--progress=plain",
        "--build-arg",
        f"NODE_VERSION={node_version}",
        "--build-arg",
        f"REPO_URL={repo_url}",
        "--build-arg",
        f"COMMIT_HASH={commit_hash}",
        "--build-arg",
        f"SUB_PATH={sub_path}",
        "--build-arg",
        f"APPROVED_HASH={approved_hash}",
        "--build-arg",
        f"SOURCE_COMMIT_HASH={source_commit_hash}",
        "-t",
        image_tag,
        "-f",
        str(dockerfile_path),
        str(work_dir),
    ]
    if not cache:
        docker_build_cmd.insert(3, "--no-cache")

    with console.status("[bold blue]Building Docker image...[/bold blue]"):
        build_result = subprocess.run(
            docker_build_cmd, capture_output=True, text=True,
        )
        if build_result.returncode != 0:
            console.print("[red]Docker build failed. Output:[/red]")
            console.print(build_result.stdout)
            console.print(build_result.stderr)
            _print_docker_build_steps(build_result)
            raise subprocess.CalledProcessError(build_result.returncode, docker_build_cmd)

    if show_build_steps:
        _print_docker_build_steps(build_result)

    with console.status("[bold blue]Extracting build artifacts...[/bold blue]") as status:

        try:
            run(
                ["docker", "create", "--name", container_name, image_tag],
                capture_output=True,
            )

            run(
                [
                    "docker",
                    "cp",
                    f"{container_name}:/original-dist/.",
                    str(original_dir),
                ],
                capture_output=True,
            )

            run(
                [
                    "docker",
                    "cp",
                    f"{container_name}:/rebuilt-dist/.",
                    str(rebuilt_dir),
                ],
                capture_output=True,
            )
            console.print("  [green]✓[/green] Artifacts extracted")

            out_dir_result = subprocess.run(
                ["docker", "cp", f"{container_name}:/out-dir.txt", str(work_dir / "out-dir.txt")],
                capture_output=True,
            )
            out_dir_name = "dist"
            if out_dir_result.returncode == 0:
                out_dir_name = (work_dir / "out-dir.txt").read_text().strip() or "dist"
                if out_dir_name != "dist":
                    console.print(f"  [green]✓[/green] Detected output directory: [bold]{out_dir_name}/[/bold]")

            deleted_log = subprocess.run(
                ["docker", "cp", f"{container_name}:/deleted-js.log", str(work_dir / "deleted-js.log")],
                capture_output=True,
            )
            if deleted_log.returncode == 0:
                log_content = (work_dir / "deleted-js.log").read_text().strip()
                if log_content.startswith("no ") and log_content.endswith(" directory"):
                    console.print(f"  [yellow]![/yellow] No {out_dir_name}/ directory found before rebuild")
                else:
                    deleted_files = [l for l in log_content.splitlines() if l.strip()]
                    if deleted_files:
                        console.print(f"  [green]✓[/green] Deleted {len(deleted_files)} compiled JS file(s) before rebuild:")
                        for f in deleted_files:
                            console.print(f"    [dim]- {f}[/dim]")

            kept_js_files: list[str] = []
            kept_log = subprocess.run(
                ["docker", "cp", f"{container_name}:/kept-js.log", str(work_dir / "kept-js.log")],
                capture_output=True,
            )
            if kept_log.returncode == 0:
                kept_js_files = [
                    l for l in (work_dir / "kept-js.log").read_text().strip().splitlines()
                    if l.strip()
                ]
                if kept_js_files:
                    console.print(
                        f"  [green]✓[/green] Kept {len(kept_js_files)} non-minified JS file(s) "
                        f"(diffed against previously-approved version, not rebuild):"
                    )
                    for f in kept_js_files:
                        console.print(f"    [dim]- {f}[/dim]")

            action_type_result = subprocess.run(
                ["docker", "cp", f"{container_name}:/action-type.txt", str(work_dir / "action-type.txt")],
                capture_output=True,
            )
            action_type = "unknown"
            if action_type_result.returncode == 0:
                action_type = (work_dir / "action-type.txt").read_text().strip()
                console.print(f"  [green]✓[/green] Action type: [bold]{action_type}[/bold]")

            original_node_modules = work_dir / "original-node-modules"
            rebuilt_node_modules = work_dir / "rebuilt-node-modules"
            original_node_modules.mkdir(exist_ok=True)
            rebuilt_node_modules.mkdir(exist_ok=True)
            has_node_modules = False

            has_nm_result = subprocess.run(
                ["docker", "cp", f"{container_name}:/has-node-modules.txt",
                 str(work_dir / "has-node-modules.txt")],
                capture_output=True,
            )
            if has_nm_result.returncode == 0:
                has_node_modules = (work_dir / "has-node-modules.txt").read_text().strip() == "true"

            if has_node_modules:
                status.update("[bold blue]Extracting node_modules artifacts...[/bold blue]")
                run(
                    ["docker", "cp", f"{container_name}:/original-node-modules/.",
                     str(original_node_modules)],
                    capture_output=True,
                )
                run(
                    ["docker", "cp", f"{container_name}:/rebuilt-node-modules/.",
                     str(rebuilt_node_modules)],
                    capture_output=True,
                )
                console.print("  [green]✓[/green] Vendored node_modules detected and extracted")
        finally:
            status.update("[bold blue]Cleaning up Docker resources...[/bold blue]")
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
            )
            subprocess.run(
                ["docker", "rmi", "-f", image_tag],
                capture_output=True,
            )
            console.print("  [green]✓[/green] Cleanup complete")

    return (original_dir, rebuilt_dir, action_type, out_dir_name,
            has_node_modules, original_node_modules, rebuilt_node_modules,
            kept_js_files)
