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
"""JavaScript beautification and compiled JS comparison."""

from pathlib import Path

import jsbeautifier

from .console import console, link
from .diff_display import show_colored_diff

# Extensions emitted by action build toolchains. `.js` is the common case
# (webpack/ncc/tsc); `.cjs` is what esbuild/rollup write when the source
# package is ESM (type: module) and the action needs a CommonJS bundle for
# the node runner (e.g. JustinBeckwith/linkinator-action);
# `.mjs` is the inverse — an ESM bundle for node20+ runners.
COMPILED_JS_EXTENSIONS = ("*.js", "*.cjs", "*.mjs")


def _collect_compiled_js(base: Path) -> set[Path]:
    """Return relative paths of compiled JS files under base."""
    found: set[Path] = set()
    for pattern in COMPILED_JS_EXTENSIONS:
        for f in base.rglob(pattern):
            found.add(f.relative_to(base))
    return found


def beautify_js(content: str) -> str:
    """Reformat JavaScript for readable diffing."""
    opts = jsbeautifier.default_options()
    opts.indent_size = 2
    opts.wrap_line_length = 120
    result = jsbeautifier.beautify(content, opts)
    lines = [line.rstrip() for line in result.splitlines()]
    return "\n".join(lines) + "\n"


def diff_js_files(
    original_dir: Path, rebuilt_dir: Path, org: str, repo: str, commit_hash: str,
    out_dir_name: str = "dist",
    kept_files: set[Path] | None = None,
) -> bool:
    """Diff JS files between original and rebuilt, return True if identical.

    Files in *kept_files* (paths relative to *out_dir_name*) are skipped here:
    they're non-minified bundles where a clean rebuild produces toolchain-
    version noise rather than actionable diffs, and are verified by the
    approved-vs-new source diff section instead.

    Files present *only in the rebuild* are reported but do not fail the
    check: a file the action does not publish never reaches a consumer's
    runner, so it cannot be a supply-chain vector.  They are usually
    intermediate build output that upstream deliberately does not commit —
    e.g. JetBrains/qodana-action declares ``main: scan/dist/index.js``, so
    OUT_DIR resolves to the whole ``scan/`` sub-project and the rebuild's
    gitignored ``scan/lib/*.js`` (stage one of its ``tsc`` → ``esbuild``
    build) lands in the compared tree.  Files only in the *original* remain
    a hard failure, as does an original tree with no compiled JS at all
    while the rebuild produced some — there is nothing published to
    reconcile the rebuild against.
    """
    blob_url = f"https://github.com/{org}/{repo}/blob/{commit_hash}"
    kept_files = kept_files or set()

    # Files vendored by @vercel/ncc that are not built from the action's source.
    ignored_files = {"sourcemap-register.js"}

    original_files = _collect_compiled_js(original_dir)
    rebuilt_files = _collect_compiled_js(rebuilt_dir)

    all_files = sorted(original_files | rebuilt_files)

    if not all_files:
        console.print(
            f"\n[yellow]No compiled JavaScript found in {out_dir_name}/ — "
            "this action may ship source JS directly (e.g. with node_modules/)[/yellow]"
        )
        return True

    console.print()
    console.rule(f"[bold]Comparing {len(all_files)} JavaScript file(s)[/bold]")

    all_match = True

    # The rebuild produced compiled JS but the published tree has none, so
    # every file below is "only in rebuilt" and nothing can be reconciled
    # against what actually ships.  Without this guard the per-file
    # informational handling would let such an action pass unverified.
    if not original_files and rebuilt_files:
        console.print(
            f"  [red]✗[/red] [red bold]No compiled JavaScript in the published "
            f"{out_dir_name}/ to compare against[/red bold] — the rebuild produced "
            f"{len(rebuilt_files)} file(s) but the action publishes none"
        )
        all_match = False

    def is_minified(content: str) -> bool:
        """Check if JS content appears to be minified."""
        lines = content.splitlines()
        if not lines:
            return False
        avg_len = sum(len(l) for l in lines) / len(lines)
        return avg_len > 500 or len(lines) < 10

    # Check which ignored files are actually referenced by other JS files
    all_js_contents: dict[Path, str] = {}
    for rel_path in all_files:
        for base_dir in (original_dir, rebuilt_dir):
            full_path = base_dir / rel_path
            if full_path.exists() and rel_path not in all_js_contents:
                all_js_contents[rel_path] = full_path.read_text(errors="replace")

    for rel_path in all_files:
        if rel_path.name in ignored_files:
            referenced_by = [
                other
                for other, content in all_js_contents.items()
                if other != rel_path and rel_path.name in content
            ]
            if referenced_by:
                console.print(
                    f"  [yellow]![/yellow] {rel_path} is in the ignore list but is "
                    f"referenced by: {', '.join(str(r) for r in referenced_by)} "
                    f"— [bold]comparing anyway[/bold]"
                )
            else:
                console.print(
                    f"  [dim]⊘ {rel_path} (skipped: vendored @vercel/ncc runtime helper, "
                    f"not referenced by other JS files)[/dim]"
                )
                continue

        orig_file = original_dir / rel_path
        built_file = rebuilt_dir / rel_path

        file_link = link(f"{blob_url}/{out_dir_name}/{rel_path}", str(rel_path))

        if rel_path in kept_files:
            console.print(
                f"  [yellow]~[/yellow] {file_link} [yellow](non-minified — "
                f"kept; diffed against previously-approved version below)[/yellow]"
            )
            continue

        if rel_path not in original_files:
            # Not published, so it never runs on a consumer's runner and
            # cannot be a supply-chain vector.  Report it and move on — the
            # content diff would just be a full dump of a file nobody ships.
            console.print(
                f"  [green]+[/green] {file_link} [dim](only in rebuilt — not published "
                f"by the action; intermediate build output, informational)[/dim]"
            )
            continue

        if rel_path not in rebuilt_files:
            console.print(f"  [red]-[/red] {file_link} [dim](only in original)[/dim]")
            with console.status(f"[dim]Beautifying {rel_path}...[/dim]"):
                orig_content = beautify_js(orig_file.read_text(errors="replace"))
            show_colored_diff(rel_path, orig_content, "")
            all_match = False
            continue

        orig_raw = orig_file.read_text(errors="replace")
        built_raw = built_file.read_text(errors="replace")

        with console.status(f"[dim]Beautifying {rel_path}...[/dim]"):
            orig_content = beautify_js(orig_raw)
            built_content = beautify_js(built_raw)

        if orig_content == built_content:
            console.print(f"  [green]✓[/green] {file_link} [green](identical)[/green]")
        elif not is_minified(orig_raw):
            console.print(
                f"  [yellow]~[/yellow] {file_link} [yellow](non-minified JS — "
                f"rebuild differs, likely due to ncc/toolchain version differences)[/yellow]"
            )
            console.print(
                f"    [dim]The dist/ JS is human-readable and not minified. Small differences "
                f"in the webpack boilerplate are expected across ncc versions.\n"
                f"    Review the source changes via the approved version diff below instead.[/dim]"
            )
        else:
            all_match = False
            console.print(f"  [red]✗[/red] {file_link} [red bold](DIFFERS)[/red bold]")
            show_colored_diff(rel_path, orig_content, built_content)

    return all_match
