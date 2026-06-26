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
"""Tests for the vendored-node_modules npm registry verification."""

import base64
import hashlib
import io
import json
import tarfile
from unittest import mock

from verify_action_build import npm_registry_verify as nrv
from verify_action_build.npm_registry_verify import (
    _git_blob_sha1,
    _integrity_matches,
    _tarball_files,
    verify_vendored_node_modules,
)


def _make_tgz(files: dict[str, bytes]) -> bytes:
    """Build an npm-style ``.tgz`` (everything under ``package/``)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, content in files.items():
            info = tarfile.TarInfo(name=f"package/{rel}")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


# A single-package fixture mirroring the real shape (action-send-mail vendors
# small packages exactly like this).
PKG_FILES = {
    "index.js": b"module.exports = 1;\n",
    "package.json": b'{"name":"foo","version":"1.0.0"}\n',
}
PKG_TGZ = _make_tgz(PKG_FILES)
PKG_URL = "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz"


def _tree_for(files: dict[str, bytes], extra: dict[str, str] | None = None) -> dict[str, str]:
    tree = {"node_modules/.package-lock.json": "abc123"}
    for rel, content in files.items():
        tree[f"node_modules/foo/{rel}"] = _git_blob_sha1(content)
    if extra:
        tree.update(extra)
    return tree


def _lock(resolved: str = PKG_URL, integrity: str | None = None, extra_pkgs=None) -> bytes:
    packages = {
        "": {"name": "root"},
        "node_modules/foo": {
            "version": "1.0.0",
            "resolved": resolved,
            "integrity": integrity if integrity is not None else _integrity(PKG_TGZ),
        },
    }
    if extra_pkgs:
        packages.update(extra_pkgs)
    return json.dumps({"lockfileVersion": 3, "packages": packages}).encode()


def _run(tree, lockfile_bytes, tarballs=None, truncated=False):
    """Invoke the verifier with the three network seams mocked."""
    tarballs = tarballs or {PKG_URL: PKG_TGZ}
    with mock.patch.object(nrv, "_fetch_tree_with_sha", return_value=(tree, truncated)), \
         mock.patch.object(nrv, "_fetch_lockfile", return_value=lockfile_bytes), \
         mock.patch.object(nrv, "_download_tarball", side_effect=lambda url: tarballs.get(url)):
        return verify_vendored_node_modules("org", "repo", "deadbeef")


class TestHelpers:
    def test_git_blob_sha1_known_value(self):
        # git hash-object of an empty blob is well-known.
        assert _git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

    def test_integrity_matches_true_and_false(self):
        data = b"hello"
        assert _integrity_matches(data, _integrity(data)) is True
        assert _integrity_matches(data, _integrity(b"tampered")) is False

    def test_integrity_matches_sha256_token(self):
        data = b"hello"
        b64 = base64.b64encode(hashlib.sha256(data).digest()).decode()
        assert _integrity_matches(data, f"sha256-{b64}") is True

    def test_tarball_files_strips_package_prefix(self):
        out = _tarball_files(PKG_TGZ)
        assert out == PKG_FILES


class TestVerify:
    def test_no_vendored_lockfile_returns_none(self):
        # Tree without node_modules/.package-lock.json → not applicable.
        assert _run({"action.yml": "x", "index.js": "y"}, _lock()) is None

    def test_clean_match_passes(self):
        result = _run(_tree_for(PKG_FILES), _lock())
        assert result is not None
        assert result.ok is True
        assert result.verified == ["foo"]
        assert not result.mismatched and not result.extra and not result.errors

    def test_content_mismatch_fails(self):
        tree = _tree_for(PKG_FILES)
        tree["node_modules/foo/index.js"] = _git_blob_sha1(b"EVIL();\n")  # tampered
        result = _run(tree, _lock())
        assert result.ok is False
        assert "node_modules/foo/index.js" in result.mismatched
        assert "foo" not in result.verified

    def test_integrity_mismatch_fails(self):
        # Lockfile claims a digest the tarball doesn't have → reject.
        result = _run(_tree_for(PKG_FILES), _lock(integrity=_integrity(b"other")))
        assert result.ok is False
        assert any("integrity" in e for e in result.errors)

    def test_extra_file_in_verified_package_fails(self):
        tree = _tree_for(PKG_FILES, extra={"node_modules/foo/sneaky.js": "deadbeef00"})
        result = _run(tree, _lock())
        assert result.ok is False
        assert "node_modules/foo/sneaky.js" in result.extra

    def test_noisy_bin_files_not_flagged_as_extra(self):
        tree = _tree_for(PKG_FILES, extra={"node_modules/.bin/foo": "shimsha00"})
        result = _run(tree, _lock())
        assert result.ok is True
        assert not result.extra

    def test_git_dependency_is_skipped_not_failed(self):
        # A git dep has no integrity / non-registry resolved → skipped, not a
        # hard failure, but surfaced (no silent pass).
        lock = _lock(resolved="git+ssh://git@github.com/foo/foo.git#abc", integrity="")
        result = _run(_tree_for(PKG_FILES), lock)
        assert result.ok is True
        assert "foo" in result.skipped
        assert "foo" not in result.verified

    def test_foreign_registry_recorded(self):
        url = "https://npm.example.com/foo/-/foo-1.0.0.tgz"
        result = _run(_tree_for(PKG_FILES), _lock(resolved=url), tarballs={url: PKG_TGZ})
        assert result.ok is True
        assert any("npm.example.com" in f for f in result.foreign)
        assert "foo" in result.verified

    def test_truncated_tree_cannot_pass(self):
        result = _run(_tree_for(PKG_FILES), _lock(), truncated=True)
        assert result is not None
        assert result.truncated is True
        assert result.ok is False
