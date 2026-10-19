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
"""Unit tests for the pure logic in ``check_allowlist_approval.py``."""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_allowlist_approval", Path(__file__).with_name("check_allowlist_approval.py")
)
caa = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_allowlist_approval"] = caa
_spec.loader.exec_module(caa)


def _review(login, state):
    return {"login": login, "state": state}


def test_allowlist_pr_detected_for_each_allowlist_file():
    for path in caa.ALLOWLIST_PATHS:
        assert caa.is_allowlist_pr(["README.md", path])


def test_non_allowlist_pr():
    assert not caa.is_allowlist_pr(["README.md", "utils/actions.yml"])
    assert not caa.is_allowlist_pr([])


def test_approvers_uses_latest_decisive_review():
    reviews = [
        _review("alice", "APPROVED"),
        _review("bob", "APPROVED"),
        _review("bob", "CHANGES_REQUESTED"),
        _review("carol", "CHANGES_REQUESTED"),
        _review("carol", "APPROVED"),
    ]
    assert caa.approvers(reviews) == ["alice", "carol"]


def test_comment_does_not_override_approval():
    reviews = [_review("alice", "APPROVED"), _review("alice", "COMMENTED")]
    assert caa.approvers(reviews) == ["alice"]


def test_dismissed_approval_does_not_count():
    reviews = [_review("alice", "APPROVED"), _review("alice", "DISMISSED")]
    assert caa.approvers(reviews) == []


def test_maintainer_approvers_filters_by_permission():
    permissions = {"alice": "admin", "bob": "write", "carol": "read", "dave": "none"}
    reviews = [_review(login, "APPROVED") for login in permissions]
    assert caa.maintainer_approvers(reviews, permissions.__getitem__) == [
        "alice",
        "bob",
    ]


def test_no_maintainer_approval():
    reviews = [_review("carol", "APPROVED"), _review("alice", "COMMENTED")]
    permissions = {"alice": "admin", "carol": "read"}
    assert caa.maintainer_approvers(reviews, permissions.__getitem__) == []


def _labeled(login, label=None, event="labeled"):
    return {"event": event, "label": label or caa.OVERRIDE_LABEL, "login": login}


def _permission(**perms):
    return lambda login: perms.get(login, "read")


def test_override_by_a_maintainer_waives_approval():
    perms = _permission(alice="write")
    assert caa.override_by([caa.OVERRIDE_LABEL], [_labeled("alice")], perms) == "alice"


def test_override_needs_the_label_on_the_pr():
    perms = _permission(alice="write")
    events = [_labeled("alice"), _labeled("alice", event="unlabeled")]
    assert caa.override_by([], events, perms) is None


def test_override_by_a_triager_does_not_count():
    perms = _permission(alice="write", tri="triage")
    # A maintainer added it once, but whoever added it last is a triager.
    events = [_labeled("alice"), _labeled("alice", event="unlabeled"), _labeled("tri")]
    assert caa.override_by([caa.OVERRIDE_LABEL], events, perms) is None


def test_override_with_no_labeled_event_does_not_count():
    assert caa.override_by([caa.OVERRIDE_LABEL], [], _permission()) is None


def test_other_labels_are_not_an_override():
    perms = _permission(alice="write")
    assert caa.override_by(["preview"], [_labeled("alice", label="preview")], perms) is None

