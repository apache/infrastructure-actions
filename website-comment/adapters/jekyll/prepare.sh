#!/bin/sh
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
# Install the source-annotation plugin into a Jekyll site for a PREVIEW build.
# Run it in the preview build only, never before the production build: the
# plugin stamps every page with data-preview-src, which must not ship.
#
# Usage: prepare.sh <jekyll site source directory>
set -eu

site="${1:?usage: prepare.sh <jekyll site source directory>}"

config=""
for c in "$site/_config.yml" "$site/_config.yaml"; do
  if [ -f "$c" ]; then config="$c"; break; fi
done
if [ -z "$config" ]; then
  echo "prepare.sh: no _config.yml in $site" >&2
  exit 1
fi

# Safe mode, which the github-pages gem forces, ignores _plugins entirely. The
# build would succeed with nothing stamped, so refuse rather than pretend.
if grep -Eq '^[[:space:]]*safe:[[:space:]]*true' "$config"; then
  echo "prepare.sh: $config sets safe: true, which ignores _plugins" >&2
  exit 1
fi

plugins_dir="$(sed -n 's/^plugins_dir:[[:space:]]*["'\'']*\([^"'\'' ]*\).*/\1/p' "$config" | head -n 1)"
plugins_dir="${plugins_dir:-_plugins}"

mkdir -p "$site/$plugins_dir"
cp "$(dirname "$0")/preview_src.rb" "$site/$plugins_dir/preview_src.rb"
echo "prepare.sh: installed preview_src.rb into $site/$plugins_dir"
