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
from datetime import datetime, timezone
from unittest import mock

from verify_action_build.release_lookup import (
    format_release_time,
    get_release_or_commit_time,
)


class TestFormatReleaseTime:
    def test_includes_full_weekday_name(self):
        # 2026-04-23 was a Thursday.
        ts = datetime(2026, 4, 23, 14, 32, 0, tzinfo=timezone.utc)
        assert format_release_time(ts) == "Thursday 2026-04-23 14:32 UTC"

    def test_each_weekday(self):
        # Spot-check every weekday in a single calendar week.
        # 2026-04-20 was a Monday.
        for offset, name in enumerate(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        ):
            ts = datetime(2026, 4, 20 + offset, 9, 0, tzinfo=timezone.utc)
            assert format_release_time(ts).startswith(name)

    def test_no_relative_phrase_in_output(self):
        # The whole point: never embed "X days ago" since it'd rot if the
        # output is re-read later.
        ts = datetime(2026, 4, 23, 14, 32, 0, tzinfo=timezone.utc)
        out = format_release_time(ts)
        assert "ago" not in out
        assert "in the future" not in out

    def test_naive_timestamp_treated_as_utc(self):
        # A datetime without tzinfo is assumed UTC rather than crashing.
        ts = datetime(2026, 4, 23, 14, 32, 0)
        out = format_release_time(ts)
        assert "2026-04-23 14:32 UTC" in out
        assert out.startswith("Thursday")

    def test_non_utc_timezone_converted(self):
        # +02:00 input should be displayed as the equivalent UTC.
        from datetime import timedelta as _td
        tz = timezone(_td(hours=2))
        ts = datetime(2026, 4, 23, 16, 32, 0, tzinfo=tz)  # 14:32 UTC
        out = format_release_time(ts)
        assert out == "Thursday 2026-04-23 14:32 UTC"


class TestGetReleaseOrCommitTime:
    def test_returns_release_published_at_when_tag_has_release(self):
        with mock.patch(
            "verify_action_build.release_lookup._find_tags_for_commit",
            return_value=["v1.2.3", "v1"],
        ), mock.patch(
            "verify_action_build.release_lookup._release_published_at",
            side_effect=lambda o, r, t: (
                datetime(2026, 4, 23, 14, 32, tzinfo=timezone.utc)
                if t == "v1.2.3" else None
            ),
        ):
            result = get_release_or_commit_time("org", "repo", "a" * 40)
        assert result is not None
        ts, tag, source = result
        assert tag == "v1.2.3"
        assert source == "release"
        assert ts.year == 2026 and ts.month == 4 and ts.day == 23

    def test_falls_back_to_second_tag_when_first_has_no_release(self):
        with mock.patch(
            "verify_action_build.release_lookup._find_tags_for_commit",
            return_value=["v1-rolling", "v1.0.0"],
        ), mock.patch(
            "verify_action_build.release_lookup._release_published_at",
            side_effect=lambda o, r, t: (
                datetime(2026, 1, 1, tzinfo=timezone.utc) if t == "v1.0.0" else None
            ),
        ):
            result = get_release_or_commit_time("org", "repo", "a" * 40)
        assert result is not None
        ts, tag, source = result
        assert tag == "v1.0.0"
        assert source == "release"

    def test_falls_back_to_commit_date_when_no_release(self):
        with mock.patch(
            "verify_action_build.release_lookup._find_tags_for_commit",
            return_value=["v9.9.9"],
        ), mock.patch(
            "verify_action_build.release_lookup._release_published_at",
            return_value=None,
        ), mock.patch(
            "verify_action_build.release_lookup._commit_committer_date",
            return_value=datetime(2026, 3, 1, tzinfo=timezone.utc),
        ):
            result = get_release_or_commit_time("org", "repo", "a" * 40)
        assert result is not None
        ts, tag, source = result
        assert tag == "v9.9.9"
        assert source == "commit"

    def test_falls_back_to_commit_date_when_no_tags(self):
        with mock.patch(
            "verify_action_build.release_lookup._find_tags_for_commit",
            return_value=[],
        ), mock.patch(
            "verify_action_build.release_lookup._commit_committer_date",
            return_value=datetime(2026, 2, 14, tzinfo=timezone.utc),
        ):
            result = get_release_or_commit_time("org", "repo", "a" * 40)
        assert result is not None
        ts, tag, source = result
        assert tag is None
        assert source == "commit"

    def test_returns_none_when_everything_fails(self):
        with mock.patch(
            "verify_action_build.release_lookup._find_tags_for_commit",
            return_value=[],
        ), mock.patch(
            "verify_action_build.release_lookup._commit_committer_date",
            return_value=None,
        ):
            result = get_release_or_commit_time("org", "repo", "a" * 40)
        assert result is None
