#!/usr/bin/python -B
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
#
# Test plugin: sets up metadata from environment variables
# N.B. Must be loaded ahead of asfgenid
#

import os
import sys
import traceback

import pelican.plugins.signals
# import pelican.utils

def process(content_object):
    """ Print any exception, before Pelican chews it into nothingness."""
    try:
        for k, v in os.environ.items():
            if k.startswith('UNIT_TEST_'):
                content_object.metadata[k] = v
    except Exception:
        print('-----', file=sys.stderr)
        traceback.print_exc()
        # exceptions here stop the build
        raise

def register():
    pelican.plugins.signals.content_object_init.connect(process)
