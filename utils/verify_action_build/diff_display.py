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
"""Colored diff rendering with pagination support."""

import difflib
from pathlib import Path

from rich.panel import Panel
from rich.text import Text

from .console import console


def show_colored_diff(
    filename: Path,
    original: str,
    rebuilt: str,
    context_lines: int = 5,
    from_label: str = "original",
    to_label: str = "rebuilt",
    border: str = "red",
    ci_mode: bool = False,
) -> str:
    """Show a colored unified diff between two strings, paged for large diffs.

    Returns "continue", "skip_file", or "quit" (skip all remaining files).
    """
    orig_lines = original.splitlines(keepends=True)
    built_lines = rebuilt.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            built_lines,
            fromfile=f"{from_label}/{filename}",
            tofile=f"{to_label}/{filename}",
            n=context_lines,
        )
    )

    if not diff_lines:
        return "continue"

    terminal_height = console.size.height - 4
    page_size = max(terminal_height, 20)
    title = f"[bold]{filename}[/bold]"

    if ci_mode or len(diff_lines) <= page_size:
        diff_text = format_diff_text(diff_lines)
        console.print(Panel(diff_text, title=title, border_style=border, padding=(0, 1)))
        return "continue"

    total_pages = (len(diff_lines) + page_size - 1) // page_size
    console.print(
        f"  [dim]Diff has {len(diff_lines)} lines ({total_pages} pages) — "
        f"Enter: next page, n: skip file, q: skip all remaining files[/dim]"
    )

    for page_num in range(total_pages):
        start = page_num * page_size
        end = min(start + page_size, len(diff_lines))
        page_lines = diff_lines[start:end]

        diff_text = format_diff_text(page_lines)
        console.print(Panel(
            diff_text,
            title=title,
            border_style=border,
            padding=(0, 1),
            subtitle=f"[dim]page {page_num + 1}/{total_pages}[/dim]",
        ))

        if page_num < total_pages - 1:
            try:
                key = console.input("[dim]Enter: next page, n: skip file, q: skip all remaining files[/dim] ")
                choice = key.strip().lower()
                if choice == "n":
                    console.print(f"  [dim]Skipped remaining diff for {filename}[/dim]")
                    return "skip_file"
                if choice == "q":
                    console.print(f"  [dim]Skipping all remaining files[/dim]")
                    return "quit"
            except EOFError:
                return "quit"

    return "continue"


def format_diff_text(lines: list[str]) -> Text:
    """Format diff lines with syntax coloring."""
    diff_text = Text()
    for line in lines:
        line_stripped = line.rstrip("\n")
        if line.startswith("---") or line.startswith("+++"):
            diff_text.append(line_stripped + "\n", style="bold")
        elif line.startswith("@@"):
            diff_text.append(line_stripped + "\n", style="cyan")
        elif line.startswith("+"):
            diff_text.append(line_stripped + "\n", style="green")
        elif line.startswith("-"):
            diff_text.append(line_stripped + "\n", style="red")
        else:
            diff_text.append(line_stripped + "\n")
    return diff_text
