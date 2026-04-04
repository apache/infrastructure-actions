#!/usr/bin/env python3
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
"""Insert action entries into actions.yml in alphabetical order.

Usage:
    python3 insert_actions.py <actions.yml> <ref> [<ref> ...]

Each ``ref`` is an action reference in ``owner/action@version`` format.
New entries are inserted so that the top-level keys in ``actions.yml``
remain sorted case-insensitively.  If an action already exists in the
file, the entry is skipped (it will *not* be overwritten).
"""

import re
import sys


def insert_actions(actions_yml_path: str, refs: list[str]) -> None:
    """Insert *refs* into *actions_yml_path* in alphabetical order."""
    # Group refs by action name: {name: [version, ...]}
    by_action: dict[str, list[str]] = {}
    for ref in refs:
        name, _, version = ref.partition("@")
        by_action.setdefault(name, []).append(version or "*")

    # Build YAML blocks for the new entries
    new_entries: dict[str, str] = {}
    for name in sorted(by_action):
        lines = [f"{name}:"]
        for version in sorted(by_action[name]):
            lines.append(f"  '{version}':")
            lines.append("    keep: true")
        new_entries[name] = "\n".join(lines)

    # Parse existing top-level blocks
    text = open(actions_yml_path).read()
    blocks = re.split(r"(?m)(?=^\S)", text)
    by_key: dict[str, str] = {}
    for block in blocks:
        if block.strip():
            by_key[block.split(":", 1)[0].strip()] = block.rstrip()

    # Merge — setdefault keeps existing entries untouched
    for key, value in new_entries.items():
        by_key.setdefault(key, value)

    # Write back sorted
    with open(actions_yml_path, "w") as f:
        f.write(
            "\n".join(by_key[k] for k in sorted(by_key, key=str.casefold))
            + "\n"
        )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <actions.yml> <ref> [<ref> ...]", file=sys.stderr)
        sys.exit(2)
    insert_actions(sys.argv[1], sys.argv[2:])
