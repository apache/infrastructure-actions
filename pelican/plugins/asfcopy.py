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
# asfcopy.py -- Pelican plugin that copies trees during finalization
#

import sys
import shutil
import os
import traceback

import pelican.plugins.signals
import pelican.settings


# copy trees from PATH to OUTPUT_PATH
def copy_trees(pel_ob):
    print('-----\nasfcopy')

    output_path = pel_ob.settings.get('OUTPUT_PATH')
    path = pel_ob.settings.get('PATH')
    asf_copy = pel_ob.settings.get('ASF_COPY')
    if asf_copy:
        for tree in asf_copy:
            src = os.path.join(path, tree)
            dst = os.path.join(output_path, tree)
            print(f'{src} --> {dst}')
            shutil.copytree(src, dst)
    else:
        print("Nothing to copy")


def tb_finalized(pel_ob):
    """ Print any exception, before Pelican chews it into nothingness."""
    try:
        copy_trees(pel_ob)
    except Exception:
        print('-----', file=sys.stderr)
        traceback.print_exc()
        # exceptions here stop the build
        raise


def register():
    pelican.plugins.signals.finalized.connect(tb_finalized)
