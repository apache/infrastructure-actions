#!/usr/bin/env python3
# -*- coding: utf-8 -*- #
# vim: encoding=utf-8
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

"""
Append a path to PLUGIN_PATHS and generate the appropriate JSON output
for use with the -e/--extra-settings pelican CLI override flag

USAGE:
  $ plugin_paths.py path-to-add [PELICAN-CONFIGURATION-FILE]

  If the file is not specified, "pelicanconf.py" is used (read from
  the current working directory.

"""

import sys
import json

DEFAULT_PELCONF = 'pelicanconf.py'  # in current dir

def main():
    """
        Parse the file and generate the JSON output
    """
    path = sys.argv[1] # path to append
    if len(sys.argv) > 2:
        file = sys.argv[2] # the file to parse
    else:
        file = DEFAULT_PELCONF

    with open(file, 'r', encoding='utf-8') as infile:
        contents = infile.read()

    # This will contain the "globals" after executing the peliconconf.py.
    # Note: allow all builtins (by virtue of NOT inserting a __builtins__
    #   value into this dictionary. We have no concerns about builtin usage.
    values = { }

    # Run the pelicanconf.py code into VALUES. We do not want to import
    # this as a module, as we're only going for the variable values.
    exec(contents, values) # pylint: disable=exec-used

    plugdir = values.get('PLUGIN_PATHS', [])
    plugdir.append(path)
    print(f"PLUGIN_PATHS={json.dumps(plugdir)}")

if __name__ == '__main__':
    main()
