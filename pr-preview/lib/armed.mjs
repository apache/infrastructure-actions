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

import { isShowPreviewComment } from "./comments.mjs";

/**
 * Armed means a maintainer said so, in a comment.
 *
 * Write access is resolved per author rather than read from
 * author_association, which is a weaker signal. Results are memoised so a PR
 * spammed with the command costs one permission lookup per distinct author.
 */
export async function resolveArmed({ comments, hasWriteAccess }) {
  const seen = new Map();

  for (const c of comments) {
    if (!isShowPreviewComment(c?.body)) continue;
    const login = c?.user?.login;
    if (!login) continue;

    if (!seen.has(login)) seen.set(login, await hasWriteAccess(login));
    if (seen.get(login)) return { armed: true, by: login };
  }

  return { armed: false, by: null };
}
