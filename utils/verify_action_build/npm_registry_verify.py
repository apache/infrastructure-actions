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
"""Verify vendored ``node_modules`` against the npm registry.

Many node actions commit their whole ``node_modules`` tree.  The existing
JS-build check rebuilds that tree with ``npm ci`` and diffs it — but npm is
not byte-reproducible across versions, so the rebuild frequently fails to
match and the tool falls back to a weak "diff against the previously
approved version" review prompt.

This module verifies the vendored tree directly against the registry,
which *is* deterministic.  npm records, for every package in the
``lockfileVersion`` 2/3 lockfile, the ``resolved`` tarball URL and an
``integrity`` digest (sha512 of the tarball).  For each package we:

  1. download the tarball from ``resolved``,
  2. confirm its digest matches ``integrity`` — proving it is the
     published, untampered package,
  3. compare every file the tarball contains against the committed
     ``node_modules/<pkg>/...`` blob (by git blob SHA, so committed blobs
     never have to be downloaded — the repo tree already carries them),
  4. flag any committed file inside a verified package directory that the
     tarball does *not* contain (injected code).

Packages that cannot be registry-verified (git/file/link deps, or a
missing ``integrity``) are reported as *skipped* rather than silently
passed.  A pass means: every vendored package matched a registry tarball
whose digest we verified, with no extra files.
"""

import base64
import hashlib
import io
import json
import os
import tarfile

import requests

from .console import console, link

# npm-generated artifacts that live in a vendored ``node_modules`` but do
# not come from any package tarball, so they are not part of verification.
_NOISY_NAMES = {".package-lock.json", ".yarn-integrity"}
_NOISY_DIRS = {".bin", ".cache"}

NPM_REGISTRY_HOST = "registry.npmjs.org"


class NpmRegistryResult:
    """Outcome of verifying a vendored ``node_modules`` tree.

    ``verified`` / ``skipped`` / ``foreign`` hold package names;
    ``mismatched`` / ``extra`` / ``errors`` hold human-readable detail
    strings.  ``ok`` is the hard verdict; ``skipped``/``foreign`` are
    surfaced but do not fail on their own.
    """

    def __init__(self) -> None:
        self.verified: list[str] = []
        self.skipped: list[str] = []
        self.foreign: list[str] = []
        self.mismatched: list[str] = []
        self.extra: list[str] = []
        self.errors: list[str] = []
        self.truncated: bool = False

    @property
    def total(self) -> int:
        return len(self.verified) + len(self.skipped)

    @property
    def ok(self) -> bool:
        return (
            not self.truncated
            and not self.mismatched
            and not self.extra
            and not self.errors
        )


def _git_blob_sha1(data: bytes) -> str:
    """Git's blob object id: sha1 of ``blob <len>\\0<bytes>``.

    Lets us compare a tarball's extracted file against the committed blob
    using only the SHA the repo tree already reports — no blob download.
    """
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode() + b"\0")
    h.update(data)
    return h.hexdigest()


def _integrity_matches(data: bytes, integrity: str) -> bool:
    """Check tarball bytes against a Subresource-Integrity ``algo-b64`` string.

    ``integrity`` may carry several space-separated digests; a match on any
    recognised algorithm (sha512/sha256/sha1) is sufficient.
    """
    for token in integrity.split():
        algo, _, b64 = token.partition("-")
        if not b64 or algo not in ("sha512", "sha256", "sha1"):
            continue
        digest = hashlib.new(algo, data).digest()
        if base64.b64encode(digest).decode() == b64:
            return True
    return False


def _fetch_tree_with_sha(org: str, repo: str, commit_hash: str) -> tuple[dict[str, str], bool]:
    """Map every blob path at ``commit_hash`` to its git blob SHA.

    Returns ``(paths, truncated)``.  ``truncated`` is True when the tree
    exceeded the API's recursive listing limit — the result is then not
    canonical and the caller must not treat absence as proof of anything.
    """
    url = f"https://api.github.com/repos/{org}/{repo}/git/trees/{commit_hash}?recursive=1"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, timeout=15, headers=headers)
        if not resp.ok:
            return {}, False
        data = resp.json()
        paths = {
            t["path"]: t["sha"]
            for t in data.get("tree", [])
            if t.get("type") == "blob"
        }
        return paths, bool(data.get("truncated"))
    except (requests.RequestException, ValueError):
        return {}, False


def _fetch_lockfile(org: str, repo: str, commit_hash: str, path: str) -> bytes | None:
    """Fetch one file's raw bytes at ``commit_hash`` via raw.githubusercontent."""
    url = f"https://raw.githubusercontent.com/{org}/{repo}/{commit_hash}/{path}"
    headers = {}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, timeout=15, headers=headers)
        return resp.content if resp.ok else None
    except requests.RequestException:
        return None


def _download_tarball(url: str) -> bytes | None:
    """Download a package tarball (public npm registry needs no auth)."""
    try:
        resp = requests.get(url, timeout=30)
        return resp.content if resp.ok else None
    except requests.RequestException:
        return None


def _tarball_files(data: bytes) -> dict[str, bytes]:
    """Extract a ``.tgz`` into ``{relative_path: bytes}``.

    npm tarballs root everything under ``package/``; that prefix is
    stripped so paths line up with ``node_modules/<pkg>/<rel>``.
    """
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name
            rel = name[len("package/"):] if name.startswith("package/") else name
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            files[rel] = extracted.read()
    return files


def _is_noisy(rel_path: str) -> bool:
    parts = rel_path.split("/")
    return parts[-1] in _NOISY_NAMES or any(p in _NOISY_DIRS for p in parts)


def verify_vendored_node_modules(
    org: str, repo: str, commit_hash: str, sub_path: str = "",
) -> NpmRegistryResult | None:
    """Verify a committed ``node_modules`` tree against the npm registry.

    Returns ``None`` when the action ships no vendored lockfile (the check
    is not applicable).  Otherwise returns an :class:`NpmRegistryResult`.
    """
    prefix = f"{sub_path.rstrip('/')}/" if sub_path else ""
    nm_prefix = f"{prefix}node_modules/"
    lock_path = f"{nm_prefix}.package-lock.json"

    tree, truncated = _fetch_tree_with_sha(org, repo, commit_hash)
    if not tree:
        return None
    if lock_path not in tree:
        return None

    result = NpmRegistryResult()
    if truncated:
        # The tree was too large to enumerate fully; we cannot reason about
        # extra files, so refuse to claim a pass.
        result.truncated = True
        return result

    raw = _fetch_lockfile(org, repo, commit_hash, lock_path)
    if raw is None:
        result.errors.append(f"could not fetch {lock_path}")
        return result
    try:
        lock = json.loads(raw)
    except ValueError:
        result.errors.append(f"{lock_path} is not valid JSON")
        return result

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        # lockfileVersion 1 (only "dependencies", no per-package integrity
        # under "packages") — out of scope for registry verification.
        result.skipped.append("(lockfileVersion < 2 — no per-package integrity)")
        return result

    console.print()
    console.rule("[bold]Vendored npm Registry Check[/bold]")

    # Every committed file under node_modules/, by repo-relative path → sha.
    committed = {
        path[len(prefix):]: sha
        for path, sha in tree.items()
        if path.startswith(nm_prefix)
    }

    tarball_cache: dict[str, dict[str, bytes] | None] = {}
    # node_modules paths that a verified tarball legitimately accounts for.
    accounted: set[str] = set()
    # node_modules dirs belonging to a package we managed to verify.
    verified_dirs: list[str] = []

    for key, meta in packages.items():
        if not key.startswith("node_modules/"):
            continue  # "" is the root project itself
        if not isinstance(meta, dict):
            continue
        name = key[len("node_modules/"):]
        resolved = meta.get("resolved") or ""
        integrity = meta.get("integrity") or ""

        if not integrity or not resolved.startswith(("http://", "https://")):
            # git/file/link dependency, or a workspace package — no tarball
            # digest to verify against.
            result.skipped.append(name)
            continue

        host = resolved.split("/", 3)[2] if "://" in resolved else ""
        if host != NPM_REGISTRY_HOST:
            result.foreign.append(f"{name} (registry: {host})")

        if integrity not in tarball_cache:
            data = _download_tarball(resolved)
            if data is None:
                tarball_cache[integrity] = None
                result.errors.append(f"{name}: could not download {resolved}")
                continue
            if not _integrity_matches(data, integrity):
                tarball_cache[integrity] = None
                result.errors.append(
                    f"{name}: tarball digest does not match lockfile integrity"
                )
                continue
            tarball_cache[integrity] = _tarball_files(data)

        tar_files = tarball_cache[integrity]
        if tar_files is None:
            continue  # already recorded as error above

        verified_dirs.append(key + "/")
        pkg_ok = True
        for rel, content in tar_files.items():
            committed_path = f"{key}/{rel}"
            accounted.add(committed_path)
            committed_sha = committed.get(committed_path)
            if committed_sha is None:
                continue  # tarball ships a file the repo omits — benign
            if committed_sha != _git_blob_sha1(content):
                result.mismatched.append(committed_path)
                pkg_ok = False
        if pkg_ok:
            result.verified.append(name)

    # Any committed file that sits inside a verified package directory but
    # was not produced by that package's tarball is injected code.
    for path in committed:
        if _is_noisy(path):
            continue
        if path in accounted:
            continue
        if any(path.startswith(d) for d in verified_dirs):
            result.extra.append(path)

    _render(result, org, repo, commit_hash)
    return result


def _render(result: NpmRegistryResult, org: str, repo: str, commit_hash: str) -> None:
    """Print a per-category summary of the registry check."""
    blob = f"https://github.com/{org}/{repo}/tree/{commit_hash}/node_modules"
    if result.verified:
        console.print(
            f"  [green]✓[/green] {len(result.verified)} package(s) match "
            f"registry-published, integrity-verified tarballs"
        )
    for name in result.foreign:
        console.print(f"  [yellow]![/yellow] {name} — non-npmjs registry")
    if result.skipped:
        console.print(
            f"  [yellow]![/yellow] {len(result.skipped)} package(s) not "
            f"registry-verifiable (git/file/link dep or missing integrity): "
            f"{', '.join(result.skipped[:8])}"
            + (" …" if len(result.skipped) > 8 else "")
        )
    for path in result.mismatched:
        console.print(f"  [red]✗[/red] {link(path, f'{blob}')} — content differs from registry tarball")
    for path in result.extra:
        console.print(f"  [red]✗[/red] {path} — present in repo but not in the verified package tarball")
    for err in result.errors:
        console.print(f"  [red]✗[/red] {err}")
    if result.truncated:
        console.print("  [yellow]![/yellow] repo tree too large to enumerate — cannot verify")
