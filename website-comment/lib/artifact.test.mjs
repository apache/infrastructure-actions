// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  unsafeArchiveEntries,
  archiveHasSymlinkMembers,
  uncompressedBytes,
  createArtifactFetcher,
} from "./artifact.mjs";

test("flags an absolute path entry", () => {
  assert.deepEqual(unsafeArchiveEntries("/etc/passwd"), ["/etc/passwd"]);
});

test("flags a traversing path entry", () => {
  assert.deepEqual(unsafeArchiveEntries("../../evil.html"), ["../../evil.html"]);
});

test("flags a traversal nested mid-path", () => {
  assert.deepEqual(unsafeArchiveEntries("assets/../../evil.html"), ["assets/../../evil.html"]);
});

test("flags a backslash path", () => {
  assert.deepEqual(unsafeArchiveEntries("..\\evil"), ["..\\evil"]);
});

test("flags a tilde-rooted path", () => {
  assert.deepEqual(unsafeArchiveEntries("~/evil"), ["~/evil"]);
});

test("allows a clean listing", () => {
  assert.deepEqual(unsafeArchiveEntries("index.html\nassets/app.js"), []);
});

test("returns empty for empty input", () => {
  assert.deepEqual(unsafeArchiveEntries(""), []);
});

test("returns empty for null input", () => {
  assert.deepEqual(unsafeArchiveEntries(null), []);
});

test("returns empty for undefined input", () => {
  assert.deepEqual(unsafeArchiveEntries(undefined), []);
});

test("ignores blank lines between entries", () => {
  assert.deepEqual(unsafeArchiveEntries("index.html\n\nassets/app.js\n"), []);
});

test("collects multiple unsafe entries from a mixed listing", () => {
  const listing = "index.html\n/etc/passwd\nassets/app.js\n../evil.html";
  assert.deepEqual(unsafeArchiveEntries(listing), ["/etc/passwd", "../evil.html"]);
});

test("detects a symlink member from the long listing", () => {
  const listing = [
    "Archive:  artifact.zip",
    "-rw-r--r--  3.0 unx      123 tx defN 26-Sep-16 12:00 index.html",
    "lrwxrwxrwx  3.0 unx        5 bx stor 26-Sep-16 12:00 a -> ../..",
    "2 files, 128 bytes uncompressed, 64 bytes compressed:  50.0%",
  ].join("\n");
  assert.equal(archiveHasSymlinkMembers(listing), true);
});

test("a listing with no symlink members passes", () => {
  const listing = [
    "-rw-r--r--  3.0 unx      123 tx defN 26-Sep-16 12:00 index.html",
    "-rw-r--r--  3.0 unx      456 tx defN 26-Sep-16 12:00 assets/app.js",
    "2 files, 579 bytes uncompressed, 200 bytes compressed:  65.5%",
  ].join("\n");
  assert.equal(archiveHasSymlinkMembers(listing), false);
  assert.equal(archiveHasSymlinkMembers(""), false);
  assert.equal(archiveHasSymlinkMembers(null), false);
});

test("reads the uncompressed total", () => {
  assert.equal(
    uncompressedBytes("2 files, 579 bytes uncompressed, 200 bytes compressed:  65.5%"),
    579,
  );
  assert.equal(uncompressedBytes("no totals here"), null);
  assert.equal(uncompressedBytes(null), null);
});

test("reads the total from the trailer, never from an entry name", () => {
  const listing = [
    "Archive:  artifact.zip",
    "-rw-r--r--  3.0 unx 999999999 tx defN 26-Sep-16 12:00 0 bytes uncompressed",
    "1 file, 999999999 bytes uncompressed, 1000 bytes compressed:  99.9%",
  ].join("\n");
  assert.equal(uncompressedBytes(listing), 999999999);
  // An entry name alone, with no trailer, is not a total.
  assert.equal(uncompressedBytes("-rw-r--r--  3.0 unx 1 tx defN 26-Sep-16 12:00 0 bytes uncompressed"), null);
});

test("lists a site of many files, past execFile's default 1 MiB buffer", async () => {
  const dir = await mkdtemp(join(tmpdir(), "artifact-many-"));
  try {
    const site = join(dir, "site");
    const deep = join(site, "a-fairly-long-directory-name-to-pad-the-listing", "and-another-one");
    await mkdir(deep, { recursive: true });
    await Promise.all(
      Array.from({ length: 12000 }, (_, i) => writeFile(join(deep, `page-${i}.html`), "x")),
    );
    await promisify(execFile)("zip", ["-qr", join(dir, "a.zip"), "."], { cwd: site });
    const zip = await readFile(join(dir, "a.zip"));

    const fetchArtifact = createArtifactFetcher({
      gh: {
        listRunArtifacts: async () => [{ id: 1, name: "preview-site", expired: false }],
        artifactZipUrl: () => "https://example.invalid/a.zip",
      },
      token: "t",
      fetchImpl: async () => ({
        ok: true,
        headers: new Headers({ "content-length": String(zip.length) }),
        arrayBuffer: async () => zip.buffer.slice(zip.byteOffset, zip.byteOffset + zip.length),
      }),
    });
    const got = await fetchArtifact(1);
    assert.ok(got?.dir, "the archive must be accepted");
    await rm(join(got.dir, ".."), { recursive: true, force: true });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
