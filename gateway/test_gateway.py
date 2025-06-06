import datetime

import filecmp
from gateway import *


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
            "v5": {"expires_at": datetime.date(2100, 1, 1)},
            "v4": {"expires_at": datetime.date(2100, 1, 1), "keep": True},
        },
        "hashicorp/setup-terraform": {"v2": {"expires_at": datetime.date(2100, 1, 1)}},
        "opentofu/setup-opentofu": {"v1": {"expires_at": datetime.date(2100, 1, 1)}},
        "helm/chart-testing-action": {
            "v2.5.0": {"expires_at": datetime.date(2100, 1, 1)}
        },
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {
                "expires_at": datetime.date(2100, 1, 1)
            }
        },
    }

    expected_refs: ActionsYAML = {
        "actions/setup-go": {
            "v5": {"expires_at": datetime.date(2100, 1, 1)},
            "v4": {"expires_at": datetime.date(2100, 1, 1), "keep": True},
        },
        "hashicorp/setup-terraform": {"v2": {"expires_at": datetime.date(2100, 1, 1)}},
        "opentofu/setup-opentofu": {"v1": {"expires_at": datetime.date(2100, 1, 1)}},
        "helm/chart-testing-action": {
            "v2.5.0": {"expires_at": datetime.date(2100, 1, 1)}
        },
        "dorny/paths-filter": {
            "0bc4621a3135347011ad047f9ecf449bf72ce2bd": {
                "expires_at": calculate_expiry(12)
            },
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {
                "expires_at": datetime.date(2100, 1, 1),
                "keep": False,
            },
        },
    }

    update_refs(steps, refs)
    print(refs["dorny/paths-filter"])
    assert refs == expected_refs


def test_create_pattern():
    actions = {
        "actions/setup-go": {
            "v5": {"expires_at": datetime.date(2100, 1, 1)},
            "v4": {"expires_at": datetime.date(2100, 1, 1), "keep": True},
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
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {
                "expires_at": datetime.date(2100, 1, 1),
                "keep": False,
            },
        },
    }

    expected_refs: ActionsYAML = {
        "actions/setup-go": {
            "v5": {"expires_at": calculate_expiry() + timedelta(days=2)},
            "v4": {"expires_at": datetime.date(1900, 1, 1), "keep": True},
        },
        "dorny/paths-filter": {
            "de90cc6fb38fc0963ad72b210f1f284cd68cea36": {
                "expires_at": datetime.date(2100, 1, 1),
                "keep": False,
            }
        },
    }

    remove_expired_refs(refs)
    assert refs == expected_refs
