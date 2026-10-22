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
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "rerun_allowlist_approval", Path(__file__).with_name("rerun_allowlist_approval.py")
)
raa = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["rerun_allowlist_approval"] = raa
_spec.loader.exec_module(raa)


def _run(run_id, event, conclusion, created_at, status="completed"):
    return {
        "id": run_id,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
    }


def test_failed_pull_request_run_is_rerun():
    runs = [
        _run(1, "pull_request", "failure", "2026-10-21T19:22:00Z"),
        _run(2, "pull_request_review", "success", "2026-10-22T15:00:00Z"),
    ]
    assert raa.run_to_rerun(runs) == 1


def test_only_the_latest_pull_request_run_counts():
    runs = [
        _run(1, "pull_request", "failure", "2026-10-21T19:22:00Z"),
        _run(3, "pull_request", "success", "2026-10-22T16:00:00Z"),
    ]
    assert raa.run_to_rerun(runs) is None


def test_running_pull_request_run_is_left_alone():
    runs = [_run(1, "pull_request", None, "2026-10-21T19:22:00Z", status="in_progress")]
    assert raa.run_to_rerun(runs) is None


def test_cancelled_pull_request_run_is_rerun():
    assert raa.run_to_rerun([_run(4, "pull_request", "cancelled", "2026-10-21T19:22:00Z")]) == 4


def test_review_runs_alone_need_nothing():
    assert raa.run_to_rerun([_run(2, "pull_request_review", "failure", "2026-10-22T15:00:00Z")]) is None


def test_main_rejects_a_non_sha(capsys):
    assert raa.main(["--repo", "apache/infrastructure-actions", "--head-sha", "main; rm -rf /"]) == 1
    assert "Not a commit SHA" in capsys.readouterr().out
