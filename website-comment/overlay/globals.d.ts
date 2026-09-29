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

// review.js is browser script, not a module: at publish time the publisher
// concatenates logic.mjs ahead of it with the `export ` keywords stripped, so
// these arrive as globals rather than imports. Declaring them here documents
// that contract and lets `astro check` see the file as it actually runs.

declare function clampRegion(
  drag: { x1: number; y1: number; x2: number; y2: number },
  viewport: { w: number; h: number },
): { x: number; y: number; w: number; h: number } | null;

declare function captionFor(input: {
  url: string;
  source: string | null;
  region: { x: number; y: number; w: number; h: number };
  sha: string;
}): string;

declare function targetUrl(input: {
  repo: string;
  pr: number;
  source: string | null;
  anchors: Record<string, { anchor: string; ranges: [number, number][] }> | null;
}): string;

declare function sourceUrl(input: {
  repo: string;
  branch: string;
  source: string | null;
  generated?: string[];
}): string | null;

declare function issueUrl(input: {
  repo: string;
  branch: string;
  pageUrl: string;
  source: string | null;
  generated?: string[];
  sha?: string;
}): string;
