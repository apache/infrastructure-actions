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
"""Unit tests for the pure logic in ``release_actions.py``."""

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "release_actions", Path(__file__).with_name("release_actions.py")
)
ra = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
# Register before exec so dataclass string-annotation resolution can find the
# module in sys.modules (needed under `from __future__ import annotations`).
sys.modules["release_actions"] = ra
_spec.loader.exec_module(ra)


# --------------------------------------------------------------------------- #
# affected_actions
# --------------------------------------------------------------------------- #
def _prefixes(actions):
    return sorted(a.tag_prefix for a in actions)


def test_affected_single_action():
    changed = ["allowlist-check/check_asf_allowlist.py"]
    assert _prefixes(ra.affected_actions(changed)) == ["allowlist-check"]


def test_affected_ignores_unrelated_paths():
    changed = ["README.md", "actions.yml", ".github/workflows/pytest.yml"]
    assert ra.affected_actions(changed) == []


def test_shared_stash_change_bumps_both_stash_actions():
    changed = ["stash/shared/mung.py"]
    assert _prefixes(ra.affected_actions(changed)) == ["restore", "save"]


def test_save_only_change_bumps_only_save():
    changed = ["stash/save/action.yml"]
    assert _prefixes(ra.affected_actions(changed)) == ["save"]


def test_multiple_actions_changed_at_once():
    changed = ["pelican/entrypoint.sh", "allowlist-check/action.yml"]
    assert _prefixes(ra.affected_actions(changed)) == ["allowlist-check", "pelican"]


def test_stash_root_dep_change_does_not_falsely_match_leaf_prefixes():
    # A change to stash/pyproject.toml is not in any watch_path -> no release
    # (deliberately conservative; deps bumps ride along with code changes).
    assert ra.affected_actions(["stash/pyproject.toml"]) == []


# --------------------------------------------------------------------------- #
# parse_version / latest_version
# --------------------------------------------------------------------------- #
def test_parse_version_matches_full_semver():
    assert ra.parse_version("restore/v1.4.2", "restore") == (1, 4, 2)


def test_parse_version_ignores_major_only_tag():
    assert ra.parse_version("restore/v1", "restore") is None


def test_parse_version_rejects_other_prefix():
    assert ra.parse_version("save/v1.0.0", "restore") is None


def test_parse_version_rejects_leaf_collision_lookalike():
    # "check/v1.0.0" must not be parsed as the "allowlist-check" prefix.
    assert ra.parse_version("check/v1.0.0", "allowlist-check") is None


def test_latest_version_picks_highest():
    tags = ["save/v1.0.0", "save/v1.2.0", "save/v1.1.5", "restore/v9.9.9", "save/v1"]
    assert ra.latest_version(tags, "save") == (1, 2, 0)


def test_latest_version_none_when_no_tags():
    assert ra.latest_version(["restore/v1.0.0"], "save") is None


# --------------------------------------------------------------------------- #
# bump_version
# --------------------------------------------------------------------------- #
def test_first_release_seeds_v1_0_0():
    assert ra.bump_version(None, "patch") == (1, 0, 0)
    assert ra.bump_version(None, "minor") == (1, 0, 0)
    assert ra.bump_version(None, "major") == (1, 0, 0)


def test_bump_patch():
    assert ra.bump_version((1, 2, 3), "patch") == (1, 2, 4)


def test_bump_minor_resets_patch():
    assert ra.bump_version((1, 2, 3), "minor") == (1, 3, 0)


def test_bump_major_resets_minor_and_patch():
    assert ra.bump_version((1, 2, 3), "major") == (2, 0, 0)


def test_bump_invalid_raises():
    with pytest.raises(ValueError):
        ra.bump_version((1, 0, 0), "nope")


# --------------------------------------------------------------------------- #
# format helpers
# --------------------------------------------------------------------------- #
def test_format_tag_and_major_tag():
    assert ra.format_tag("restore", (1, 4, 2)) == "restore/v1.4.2"
    assert ra.format_major_tag("restore", (1, 4, 2)) == "restore/v1"


# --------------------------------------------------------------------------- #
# determine_bump
# --------------------------------------------------------------------------- #
def test_default_bump_is_patch():
    assert ra.determine_bump([], "Fix a typo") == "patch"


def test_label_minor():
    assert ra.determine_bump(["release:minor"], "add feature") == "minor"


def test_label_major_wins_over_minor():
    assert ra.determine_bump(["release:minor", "release:major"], "") == "major"


def test_skip_label_returns_none():
    assert ra.determine_bump(["release:skip", "release:major"], "") is None


def test_commit_token_minor():
    assert ra.determine_bump([], "Add input [minor]") == "minor"


def test_commit_token_skip():
    assert ra.determine_bump([], "docs only [skip release]") is None


def test_labels_are_case_insensitive():
    assert ra.determine_bump(["Release:Major"], "") == "major"
