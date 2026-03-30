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

parser = argparse.ArgumentParser()
parser.add_argument("-t", "--token", help="GitHub Token")
parser.add_argument("-s", "--search", help="Action search string", required=True)
args = parser.parse_args()

if not os.environ["GH_TOKEN"]:
    if not args.token:
        print("No Token found!")
        sys.exit(1)
    else:
        os.environ["GH_TOKEN"] = args.token
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
    # Run the command and capture standard output
    result = subprocess.run(command, capture_output=True, text=True, check=True)

    # Parse the JSON string into a Python list/dictionary
    data = json.loads(result.stdout)

except subprocess.CalledProcessError as e:
    print(f"Error executing gh: {e.stderr}")
    sys.exit(1)
except FileNotFoundError:
    print("The 'gh' CLI tool is not installed or not in your PATH.")
    sys.exit(1)

print(f"Usage of {args.search}:\n")
for item in data:
    repo = item["repository"]["nameWithOwner"]
    flow = item["path"]
    for action in item["textMatches"]:
        term = [a for a in action["fragment"].split() if args.search in a][0]
        print(f"{repo} -- {flow} -- {term}")
