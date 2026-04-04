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

import datetime

import filecmp
from gateway import *

def load_yaml_string(yaml_string: str):
    yaml = ruyaml.YAML()
    return yaml.load(yaml_string)

def test_load_yaml():
    this_dir = os.path.dirname(os.path.realpath(__file__))
    parsed = load_yaml(this_dir + "/test_dummy.yml")
    comment = parsed['jobs']['dummy']['steps'][3].ca.items['uses'][2].value
    assert comment == "# v4.6.8\n"

def test_roundtrip_yaml():
    this_dir = os.path.dirname(os.path.realpath(__file__))
    infile = this_dir + "/test_dummy.yml"
    parsed = load_yaml(infile)
    outfile = this_dir + "/test_out_dummy.yml"
    write_yaml(outfile, parsed)
    assert filecmp.cmp(infile, outfile, shallow=False)

def test_update_refs():
    steps = [
        {"uses": "actions/setup-go@v5"},
        {"uses": "dorny/paths-filter@de90cc6fb38fc0963ad72b210f1f284cd68cea36"},
    ]

    refs: ActionsYAML = {
        "actions/setup-go": {
            "v5": {},
            "v4": {"keep": True},
        },
        "hashicorp/setup-terraform": {"v2": {}},
        "opentofu/setup-opentofu": {"v1": {}},
        "helm/chart-testing-action": {
            "v2.5.0": {}
        },
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {}
        },
    }

    expected_refs: ActionsYAML = {
        "actions/setup-go": {
            "v5": {},
            "v4": {"keep": True},
        },
        "hashicorp/setup-terraform": {"v2": {}},
        "opentofu/setup-opentofu": {"v1": {}},
        "helm/chart-testing-action": {
            "v2.5.0": {}
        },
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {
                "expires_at": calculate_expiry(12)
            },
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {},
        },
    }

    update_refs(steps, refs)
    assert refs == expected_refs

def test_update_refs_expiry():
    steps = [
        {"uses": "dorny/paths-filter@de90cc6fb38fc0963ad72b210f1f284cd68cea36"},
    ]

    refs: ActionsYAML = {
        "dorny/paths-filter": {
            "taew9aeJ3thuoteerohpohxei7ahWivuki9eshoh": {
                "expires_at": calculate_expiry(-3)
            },
            "hoo9eethee7ootieY3Ahbie9aen9oopiquaej9do": {
                "expires_at": calculate_expiry(3)
            },
            "AefuzeiLo3shaexieCiewoo3ahmoo7kie3zi9thu": {
                "expires_at": calculate_expiry(16)
            },
            "kee7Kineiy9thu4eikahTeiP9ahch3iey4deepah": {
                "keep": True,
            },
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {},
        },
    }

    expected_refs: ActionsYAML = {
        "dorny/paths-filter": {
            "taew9aeJ3thuoteerohpohxei7ahWivuki9eshoh": {
                "expires_at": calculate_expiry(-3)
            },
            "hoo9eethee7ootieY3Ahbie9aen9oopiquaej9do": {
                "expires_at": calculate_expiry(3)
            },
            "AefuzeiLo3shaexieCiewoo3ahmoo7kie3zi9thu": {
                "expires_at": calculate_expiry(12)
            },
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {
                "expires_at": calculate_expiry(12)
            },
            "kee7Kineiy9thu4eikahTeiP9ahch3iey4deepah": {
                "keep": True,
            },
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {},
        },
    }

    update_refs(steps, refs)
    assert refs == expected_refs

def test_update_tagged_ref():
    steps = load_yaml_string('''
    - uses: dorny/paths-filter@de90cc6fb38fc0963ad72b210f1f284cd68cea36
    - uses: DavidAnson/markdownlint-cli2-action@b4c9feab76d8025d1e83c653fa3990936df0e6c8   # v16
    ''')

    refs: ActionsYAML = {
        "actions/setup-go": {"v4": {"keep": True}},
        "hashicorp/setup-terraform": {"v2": {}},
        "opentofu/setup-opentofu": {"v1": {}},
        "helm/chart-testing-action": {
            "v2.5.0": {}
        },
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {}
        },
    }

    expected_refs: ActionsYAML = {
        "actions/setup-go": {"v4": {"keep": True}},
        "hashicorp/setup-terraform": {"v2": {}},
        "opentofu/setup-opentofu": {"v1": {}},
        "helm/chart-testing-action": {
            "v2.5.0": {}
        },
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {
                "expires_at": calculate_expiry(12)
            },
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {},
        },
        "DavidAnson/markdownlint-cli2-action": {
            "b4c9feab76d8025d1e83c653fa3990936df0e6c8": {
                "tag": "v16",
            }
        },
    }

    update_refs(steps, refs)
    assert refs == expected_refs


def test_create_pattern():
    actions = {
        "actions/setup-go": {
            "v5": {},
            "v4": {"keep": True},
        },
        "hashicorp/setup-terraform": {"v2": {"expires_at": datetime.date(1100, 1, 1)}},
    }
    expected = ["actions/setup-go@v5", "actions/setup-go@v4"]
    pattern = create_pattern(actions)
    assert pattern == expected


def test_clean_actions():

    refs: ActionsYAML = {
        "actions/setup-go": {
            "v5": {"expires_at": calculate_expiry() + timedelta(days=2)},
            "v4": {"expires_at": datetime.date(1900, 1, 1), "keep": True},
        },
        "opentofu/setup-opentofu": {"v1": {"expires_at": datetime.date(1900, 1, 1)}},
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {
                "expires_at": datetime.date(1900, 1, 1)
            },
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {},
        },
    }

    expected_refs: ActionsYAML = {
        "actions/setup-go": {
            "v5": {"expires_at": calculate_expiry() + timedelta(days=2)},
            "v4": {"expires_at": datetime.date(1900, 1, 1), "keep": True},
        },
        "dorny/paths-filter": {
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {}
        },
    }

    remove_expired_refs(refs)
    assert refs == expected_refs
