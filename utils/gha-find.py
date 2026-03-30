#!/usr/bin/env python3
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
# Converted this:
# $(which gh) search code --owner apache --language yaml --json repository,path --jq '.[] | .repository.nameWithOwner + " -- " + .path' $1
# To the below:
#
# Run with -t $githubtoken -s $searchterm
# e.g. ./gha-find.py -t <mytoken> -s aquasecurity/trivy-action
#
# This script will also honor envvar named GH_TOKEN if found, thus making token optional.

import sys
import shutil
import subprocess
import os
import argparse
import json
from collections import Counter

# ANSI colors
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def link(url: str, text: str | None = None) -> str:
    """Return an OSC 8 clickable hyperlink for supported terminals."""
    text = text or url
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def get_gh_token(args) -> str:
    """Resolve GitHub token: -t flag > GH_TOKEN env > GITHUB_TOKEN env > `gh auth token`."""
    if args.token:
        return args.token
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(var)
        if val:
            return val
    gh = shutil.which("gh")
    if gh:
        try:
            result = subprocess.run(
                [gh, "auth", "token"], capture_output=True, text=True, check=True
            )
            token = result.stdout.strip()
            if token:
                return token
        except subprocess.CalledProcessError:
            pass
    print("No GitHub token found! Provide via -t, GH_TOKEN/GITHUB_TOKEN env, or `gh auth login`.")
    sys.exit(1)


parser = argparse.ArgumentParser()
parser.add_argument("-t", "--token", help="GitHub Token")
parser.add_argument("-s", "--search", help="Action search string", required=True)
args = parser.parse_args()

os.environ["GH_TOKEN"] = get_gh_token(args)
GH = shutil.which("gh")

command = [
    GH,
    "search",
    "code",
    "--owner",
    "apache",
    "--language",
    "yaml",
    "--json",
    "repository,path,textMatches",
    args.search,
]

try:
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
except subprocess.CalledProcessError as e:
    print(f"Error executing gh: {e.stderr}")
    sys.exit(1)
except FileNotFoundError:
    print("The 'gh' CLI tool is not installed or not in your PATH.")
    sys.exit(1)

print(f"\n{BOLD}Usage of {CYAN}{args.search}{RESET}{BOLD}:{RESET}\n")

uses: Counter[str] = Counter()

for item in data:
    repo = item["repository"]["nameWithOwner"]
    flow = item["path"]
    file_url = f"https://github.com/{repo}/blob/HEAD/{flow}"
    for action in item["textMatches"]:
        matches = [a for a in action["fragment"].split() if args.search in a]
        if not matches:
            continue
        term = matches[0]
        uses[term] += 1
        print(f"  {GREEN}{repo}{RESET} -- {link(file_url, flow)} -- {YELLOW}{term}{RESET}")

print(f"\n{BOLD}Summary (de-duplicated install URLs):{RESET}\n")
for term, count in uses.most_common():
    action_name, _, ref = term.partition("@")
    # Use only owner/repo for the GitHub URL (ignore subpaths in composite actions)
    parts = action_name.split("/")
    action_repo = "/".join(parts[:2]) if len(parts) >= 2 else action_name
    if ref:
        action_url = f"https://github.com/{action_repo}/commit/{ref}"
    else:
        action_url = f"https://github.com/{action_repo}"
    print(f"  {YELLOW}{link(action_url, term)}{RESET}  ({count}x)")
