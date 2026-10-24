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

/**
 * The patterns of preview-artifact's `paths` input: one per line, blank lines
 * and `#` comments ignored. An empty list means every change is a site change.
 */
export function parsePatterns(text) {
  return String(text ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
}

/**
 * A pattern as a regular expression over a repository-relative path.
 *
 * `**` matches across directories, `*` and `?` within one path segment, and a
 * trailing `/` matches everything under that directory. Nothing else is
 * special, so a plain path matches only itself.
 */
export function patternRegExp(pattern) {
  const glob = pattern.endsWith("/") ? `${pattern}**` : pattern;
  let re = "";
  for (let i = 0; i < glob.length; i += 1) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      // `**/` also matches no directory at all, so `src/**/x` matches `src/x`.
      if (glob[i + 2] === "/") {
        re += "(?:.*/)?";
        i += 2;
      } else {
        re += ".*";
        i += 1;
      }
    } else if (c === "*") {
      re += "[^/]*";
    } else if (c === "?") {
      re += "[^/]";
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${re}$`);
}

/**
 * Whether any of a pull request's changed files matches any pattern.
 *
 * `files` are entries of the pull request's files listing: a rename counts
 * under its old name as well as its new one, since moving a file out of the
 * site changes the site too.
 */
export function touchesPaths(files, patterns) {
  if (!patterns.length) return true;
  const res = patterns.map(patternRegExp);
  return files.some((f) =>
    [f?.filename, f?.previous_filename].some(
      (name) => typeof name === "string" && res.some((re) => re.test(name)),
    ),
  );
}
