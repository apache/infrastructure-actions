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
# ---------------
#
# USAGE:
#   $ fetch_plugins.py [PELICAN-CONFIGURATION-FILE]
#
#   If the file is not specified, "pelicanconf.py" is used (read from
#   the current working directory.
#
# ### THIS IS A DRAFT/EXAMPLE. It merely prints the values of PLUGINS
# ### and the last directory from PLUGIN_PATHS as specified in the
# ### pelicanconf.py. If the values do not exist, then "no plugins"
# ### (the empty set) is given, and "." is used for the plugins.
#

import sys

#import requests  ### future: fetch the plugins

DEFAULT_PELCONF = 'pelicanconf.py'  # in current dir
DEFAULT_PLUGINS = set()
DEFAULT_PLUGDIR = '.'  ### is this correct?


def extract_values(pelconf):
    contents = open(pelconf, 'r').read()

    # This will contain the "globals" after executing the peliconconf.py.
    # Note: allow all builtins (by virtue of NOT inserting a __builtins__
    #   value into this dictionary. We have no concerns about builtin usage.
    values = { }

    # Run the pelicanconf.py code into VALUES. We do not want to import
    # this as a module, as we're only going for the variable values.
    exec(contents, values)

    # Variables of interest, and default values.
    plugins = set(values.get('PLUGINS', DEFAULT_PLUGINS))
    plugdir = (values.get('PLUGIN_PATHS') or [ DEFAULT_PLUGDIR ])[-1]

    return plugins, plugdir


def main(pelconf):
    plugins, plugdir = extract_values(pelconf)
    print('PLUGINS:', plugins)
    print('PLUGDIR:', plugdir)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        main(DEFAULT_PELCONF)
