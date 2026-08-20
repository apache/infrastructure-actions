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
    normalize_package_json,
    strip_npm_install_metadata,
    verify_vendored_node_modules,
)


def _make_tgz(files: dict[str, bytes], root: str = "package") -> bytes:
    """Build an npm-style ``.tgz``.

    ``root`` is ``package`` by convention; DefinitelyTyped publishes under
    the bare package name instead, so it is parameterised.  Pass ``""`` to
    emit member names verbatim.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, content in files.items():
            info = tarfile.TarInfo(name=f"{root}/{rel}" if root else rel)
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

# DefinitelyTyped roots its tarballs at the bare package name rather than
# ``package/`` — this is the real @types/estree@1.0.9 member layout.
TYPES_FILES = {
    "LICENSE": b"MIT License\n",
    "README.md": b"# Installation\n",
    "flow.d.ts": b"// flow types\n",
    "index.d.ts": b"export interface Node {}\n",
    "package.json": b'{"name":"@types/estree","version":"1.0.9"}\n',
}
TYPES_TGZ = _make_tgz(TYPES_FILES, root="estree")
TYPES_URL = "https://registry.npmjs.org/@types/estree/-/estree-1.0.9.tgz"


def _types_tree(extra: dict[str, str] | None = None) -> dict[str, str]:
    tree = {"node_modules/.package-lock.json": "abc123"}
    for rel, content in TYPES_FILES.items():
        tree[f"node_modules/@types/estree/{rel}"] = _git_blob_sha1(content)
    if extra:
        tree.update(extra)
    return tree


def _types_lock() -> bytes:
    return json.dumps({
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "root"},
            "node_modules/@types/estree": {
                "version": "1.0.9",
                "resolved": TYPES_URL,
                "integrity": _integrity(TYPES_TGZ),
            },
        },
    }).encode()


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


def _run_with_files(tree, files, tarballs=None):
    """Like :func:`_run`, but serves committed file bytes per path.

    Needed once a check fetches a committed file (not just the lockfile).
    """
    tarballs = tarballs or {PKG_URL: PKG_TGZ}
    with mock.patch.object(nrv, "_fetch_tree_with_sha", return_value=(tree, False)), \
         mock.patch.object(nrv, "_fetch_lockfile", side_effect=lambda o, r, c, p: files.get(p)), \
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

    def test_tarball_files_strips_definitelytyped_bare_name_root(self):
        # @types/* tarballs root at the bare package name; assuming
        # "package/" left every path prefixed with the root, so none of
        # them matched node_modules/@types/<pkg>/<rel>.
        assert _tarball_files(TYPES_TGZ) == TYPES_FILES

    def test_tarball_files_strips_dot_slash_prefixed_root(self):
        assert _tarball_files(_make_tgz(PKG_FILES, root="./package")) == PKG_FILES

    def test_tarball_files_leaves_multi_root_tarball_untouched(self):
        # No single shared root → nothing is safe to strip.
        files = {"a/one.js": b"1\n", "b/two.js": b"2\n"}
        assert _tarball_files(_make_tgz(files, root="")) == files

    def test_tarball_files_leaves_root_level_files_untouched(self):
        files = {"one.js": b"1\n"}
        assert _tarball_files(_make_tgz(files, root="")) == files

    def test_strip_npm_install_metadata_drops_underscore_keys(self):
        # npm's install bookkeeping is _-prefixed by convention; the exact
        # set has varied across npm versions, so match the prefix.
        assert strip_npm_install_metadata({
            "name": "tunnel", "version": "0.0.6",
            "_args": [["tunnel@0.0.6", "."]], "_location": "/tunnel",
            "_resolved": "https://registry.npmjs.org/tunnel/-/tunnel-0.0.6.tgz",
        }) == {"name": "tunnel", "version": "0.0.6"}
        # Nothing else is touched.
        assert strip_npm_install_metadata({"name": "x"}) == {"name": "x"}

    def test_normalize_package_json_shorthand_fields(self):
        # Exact shapes from tunnel@0.0.6 as vendored by
        # reactivecircus/android-emulator-runner@a421e438 vs the published
        # tarball.  npm's normalize-package-data expands author/bugs and
        # prefixes repository.url with "git+" at install time.
        published = {
            "name": "tunnel",
            "version": "0.0.6",
            "author": "Koichi Kobayashi <koichik@improvement.jp>",
            "bugs": "https://github.com/koichik/node-tunnel/issues",
            "repository": {
                "type": "git",
                "url": "https://github.com/koichik/node-tunnel.git",
            },
        }
        installed = {
            "name": "tunnel",
            "version": "0.0.6",
            "author": {"name": "Koichi Kobayashi", "email": "koichik@improvement.jp"},
            "bugs": {"url": "https://github.com/koichik/node-tunnel/issues"},
            "repository": {
                "type": "git",
                "url": "git+https://github.com/koichik/node-tunnel.git",
            },
            "_location": "/tunnel",
        }
        assert normalize_package_json(installed) == normalize_package_json(published)

    def test_normalize_package_json_leaves_runtime_fields_strict(self):
        # Fields that decide what actually runs are compared as-is.
        base = {"name": "foo", "version": "1.0.0"}
        assert normalize_package_json({**base, "main": "./index.js"}) != \
            normalize_package_json({**base, "main": "./evil.js"})
        assert normalize_package_json({**base, "scripts": {"postinstall": "x"}}) != \
            normalize_package_json(base)
        assert normalize_package_json({**base, "dependencies": {"a": "1"}}) != \
            normalize_package_json({**base, "dependencies": {"a": "2"}})

    def test_normalize_person_handles_name_only_and_url(self):
        assert normalize_package_json({"author": "Jane Doe"})["author"] == {"name": "Jane Doe"}
        assert normalize_package_json(
            {"author": "Jane Doe <j@example.com> (https://example.com)"}
        )["author"] == {
            "name": "Jane Doe", "email": "j@example.com", "url": "https://example.com",
        }


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

    def test_package_json_install_metadata_is_not_a_mismatch(self):
        # reactivecircus/android-emulator-runner@a421e438 vendors a
        # node_modules installed with npm v6-era tooling, so every
        # package.json carries _args/_location/... that the registry tarball
        # never had.  Byte comparison alone reported the package modified.
        installed = json.dumps({
            "name": "foo", "version": "1.0.0",
            "_args": [["foo@1.0.0", "."]],
            "_location": "/foo",
            "_resolved": PKG_URL,
            "_integrity": _integrity(PKG_TGZ),
        }).encode()
        tree = _tree_for(PKG_FILES)
        tree["node_modules/foo/package.json"] = _git_blob_sha1(installed)

        result = _run_with_files(tree, {
            "node_modules/.package-lock.json": _lock(),
            "node_modules/foo/package.json": installed,
        })
        assert result.ok is True
        assert result.verified == ["foo"]
        assert not result.mismatched

    def test_package_json_real_change_still_mismatches(self):
        # Precision guard: normalising _-prefixed keys must not hide an edit
        # to a field that changes what actually runs.
        tampered = json.dumps({
            "name": "foo", "version": "1.0.0", "main": "./evil.js",
            "_resolved": PKG_URL,
        }).encode()
        tree = _tree_for(PKG_FILES)
        tree["node_modules/foo/package.json"] = _git_blob_sha1(tampered)

        result = _run_with_files(tree, {
            "node_modules/.package-lock.json": _lock(),
            "node_modules/foo/package.json": tampered,
        })
        assert result.ok is False
        assert "node_modules/foo/package.json" in result.mismatched

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

    def test_definitelytyped_package_verifies_clean(self):
        # apache/infrastructure-actions#1171: github-pages-deploy-action v4.9.0
        # migrated yarn → npm, which added node_modules/.package-lock.json and
        # so switched this check on for the first time.  Every file of every
        # vendored @types package was then reported as injected code, even
        # though each tarball had already passed integrity verification.
        result = _run(_types_tree(), _types_lock(), tarballs={TYPES_URL: TYPES_TGZ})
        assert result.ok is True
        assert result.verified == ["@types/estree"]
        assert not result.extra and not result.mismatched and not result.errors

    def test_injected_file_in_definitelytyped_package_still_flagged(self):
        # Precision guard: detecting the root must not stop real extra files
        # inside a bare-name-rooted package from being caught.
        tree = _types_tree(extra={"node_modules/@types/estree/evil.js": "deadbeef00"})
        result = _run(tree, _types_lock(), tarballs={TYPES_URL: TYPES_TGZ})
        assert result.ok is False
        assert "node_modules/@types/estree/evil.js" in result.extra

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
        # Exact match (not a substring check) — asserts the precise foreign
        # entry and avoids CodeQL's URL-substring-sanitization heuristic.
        assert result.foreign == ["foo (registry: npm.example.com)"]
        assert "foo" in result.verified

    def test_truncated_tree_cannot_pass(self):
        result = _run(_tree_for(PKG_FILES), _lock(), truncated=True)
        assert result is not None
        assert result.truncated is True
        assert result.ok is False
