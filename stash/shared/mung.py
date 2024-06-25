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


def normalize(s):
    return re.sub(r"[^_\-.\w]", "_", s)


def mung(key, ref):
    key = normalize(key)
    ref = normalize(ref)
    return f"{key}-{ref}"


def output_munged_name(ref = "ref_name", key = "stash_key", output = "stash_name"):
    ref = os.environ[ref]
    key = os.environ[key]
    name = mung(key, ref)

    print(f"::debug::Creating output {output}={name} ")
    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        f.write(f'{output}={name}' + '\n')
