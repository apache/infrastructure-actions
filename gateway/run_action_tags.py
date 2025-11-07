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

# Helper script to run the action tag verification.
#
# You must have a GH_TOKEN environment variable set to a GitHub PAT w/ read accessto public repos.
#
# From a local environment use:
#       uvx --with ruyaml python gateway/run_action_tags.py
# Or if you're using a virtualenv:
#       python gateway/run_action_tags.py

import os
from pathlib import Path

from action_tags import verify_actions
from gateway import update_actions, update_patterns


def run_main():
    if not 'GH_TOKEN' in os.environ:
        raise Exception("GH_TOKEN environment variable should be must.")

    cwd = Path(os.getcwd())
    dummy_workflow = cwd / ".github/actions/for-dependabot-triggered-reviews/action.yml"
    actions_yaml = cwd / "actions.yml"
    approved_patterns_yaml = cwd / "approved_patterns.yml"

    if not dummy_workflow.exists() or not actions_yaml.exists() or not approved_patterns_yaml.exists():
        raise Exception(f"Missing required files: {dummy_workflow.absolute()}, {actions_yaml.absolute()}, {approved_patterns_yaml.absolute()}")

    update_actions(dummy_workflow, actions_yaml)
    update_patterns(approved_patterns_yaml, actions_yaml)

    result = verify_actions(actions_yaml)
    if result.has_failures():
        raise Exception(f"Verify actions result summary:\n{result}")


if __name__ == "__main__":
    run_main()
