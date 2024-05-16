# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
The age_in_days plugin adds a Jinja test, age_days_lt.

It is intended to be used in Pelican templates like this to select articles newer than 90 days:

    {% for article in (articles | selectattr("date", "age_days_lt", 90) ) %}
        ...
    {% endif %}
"""
from pelican import signals
from . import agedayslt

def add_test(pelican):
    """Add age_days_lt test to Pelican."""
    pelican.env.tests.update({'age_days_lt': agedayslt.age_days_lt})


def register():
    """Plugin registration."""
    signals.generator_init.connect(add_test)
