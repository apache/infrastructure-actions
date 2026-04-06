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
"""Shared console setup, user interaction helpers, and subprocess utilities."""

import os
import subprocess

from rich.console import Console

_is_ci = os.environ.get("CI") is not None
_ci_console_options: dict = {"force_interactive": False, "force_terminal": True, "width": 200} if _is_ci else {}

console = Console(stderr=True, **_ci_console_options)
output = Console(**_ci_console_options)


def link(url: str, text: str) -> str:
    """Return Rich-markup hyperlink, falling back to plain text in CI."""
    if _is_ci:
        return text
    return f"[link={url}]{text}[/link]"


class UserQuit(Exception):
    """Raised when user enters 'q' to quit."""


def ask_confirm(prompt: str, default: bool = True) -> bool:
    """Ask a y/n/q confirmation. Returns True/False, raises UserQuit on 'q'."""
    suffix = " [Y/n/q]" if default else " [y/N/q]"
    try:
        answer = console.input(f"{prompt}{suffix} ").strip().lower()
    except EOFError:
        raise UserQuit
    if answer == "q":
        raise UserQuit
    if not answer:
        return default
    return answer in ("y", "yes")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, failing on error."""
    return subprocess.run(cmd, check=True, **kwargs)
