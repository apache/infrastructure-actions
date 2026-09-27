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

import { createHash } from "node:crypto";

/**
 * GitHub's Files-tab anchor for a file: "diff-" followed by the sha256 of its
 * repo-relative path. Not a documented contract — it has been an MD5 before —
 * so it is computed in this one place. Verified 2026-09-17 against PR 186.
 */
export function diffAnchor(path) {
  return `diff-${createHash("sha256").update(String(path)).digest("hex")}`;
}

/** The new-file line ranges a unified diff adds or changes. */
export function addedLineRanges(patch) {
  const ranges = [];
  let line = 0;
  let run = null;

  for (const text of String(patch ?? "").split("\n")) {
    const header = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (header) {
      if (run) ranges.push(run);
      run = null;
      line = Number(header[1]);
      continue;
    }
    if (text.startsWith("+")) {
      run = run ? [run[0], line] : [line, line];
      line += 1;
    } else if (text.startsWith("-")) {
      // A removed line exists only in the old file, so the new-file cursor
      // stays put. Advancing here shifts every following line number and
      // anchors comments to the wrong line.
      if (run) ranges.push(run);
      run = null;
    } else {
      if (run) ranges.push(run);
      run = null;
      line += 1;
    }
  }
  if (run) ranges.push(run);
  return ranges;
}

/** The manifest the overlay reads: path -> {anchor, ranges}. */
export function buildAnchors(files) {
  const out = {};
  for (const file of files ?? []) {
    out[file.filename] = {
      anchor: diffAnchor(file.filename),
      ranges: addedLineRanges(file.patch),
    };
  }
  return out;
}
