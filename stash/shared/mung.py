# Copyright (c) The stash contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re


def normalize(s: str) -> str:
    """
    Replaces all characters in the input string `s` that are not
    alphanumeric, underscores, hyphens, or periods with underscores.
    """
    return re.sub(r"[^_\-.\w]", "_", s)


def mung(key: str, ref: str) -> str:
    """Combine `key` and `ref` into a single string separated by a hyphen."""
    key = normalize(key)
    ref = normalize(ref)
    return f"{key}-{ref}"


def output_munged_name(ref="ref_name", key="stash_key", output="stash_name"):
    """
    Reads the stash key and ref name from the matching environment variables,
    combines them and saves the result in a GHA output variable.

    Args:
    ref (str, optional): The name of the environment variable containing
                         the reference string. Defaults to "ref_name".
    key (str, optional): The name of the environment variable containing
                         the key string. Defaults to "stash_key".
    output (str, optional): The output variable name to be used in the
                            GitHub Actions output file. Defaults to "stash_name".

    Returns:
    None
    """
    ref = os.environ[ref]

    key = os.environ[key]
    name = mung(key, ref)

    print(f"::debug::Creating output {output}={name} ")
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"{output}={name}" + "\n")
