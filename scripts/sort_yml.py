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
#
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rich",
#     "ruamel.yaml",
# ]
# ///

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
import sys
from pathlib import Path
from rich.console import Console

console = Console(width=400, color_system="standard")


def sort_yaml_file(input_file: str):
    """Sorts the keys of a YAML file in alphabetical order"""

    yaml = YAML()
    yaml.preserve_quotes = True

    input_path = Path(input_file)

    if not input_path.exists():
        raise FileNotFoundError(f"File '{input_file}' not found.")

    with open(input_path, 'r', encoding='utf-8') as f:
        data = yaml.load(f)

    sorted_data = CommentedMap()

    sorted_keys: list[str] = sorted(data.keys(), key=str.lower)

    # Copy data in sorted order
    for key in sorted_keys:
        sorted_data[key] = data[key]

    # Preserve any comment at the beginning of the file
    if hasattr(data, 'ca') and hasattr(data.ca, 'comment'):
        if not hasattr(sorted_data, 'ca'):
            sorted_data.ca = data.ca.__class__()
        sorted_data.ca.comment = data.ca.comment

    with open(input_path, 'w', encoding='utf-8') as f:
        yaml.dump(sorted_data, f)


errors = []
def main():
    files = sys.argv[1:]
    for file in files:
        console.print(f"[blue]Sorting YAML file {file}")
        try:
            sort_yaml_file(file)
            console.print(f"[blue]✅ YAML file sorted successfully {file}!")
        except FileNotFoundError as e:
            errors.append((file, str(e)))
        except Exception as e:
            errors.append((file, str(e)))

    if errors:
        console.print("[red]Errors occurred while sorting YAML files:")
        for file, error in errors:
            console.print(f"[red]File: {file} - Error: {error}")
        sys.exit(1)

if __name__ == "__main__":
    main()