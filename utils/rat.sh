#!/usr/bin/env bash
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

set -eu

RAT_VERSION='0.18'
RAT_ARCHIVE_NAME="apache-rat-${RAT_VERSION}-bin.tar.gz"
RAT_ARCHIVE_URL="https://dlcdn.apache.org/creadur/apache-rat-${RAT_VERSION}/${RAT_ARCHIVE_NAME}"
RAT_ARCHIVE_SHA512='315b16536526838237c42b5e6b613d29adc77e25a6e44a866b2b7f8b162e03d3629d49c9faea86ceb864a36b2c42838b8ce43d6f2db544e961f2259e242748f4'
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPO_ROOT=$TOOL_DIR
RAT_WORK_HIDDEN_DIR=$SCRIPT_DIR/.rat
ARCHIVE_PATH=$RAT_WORK_HIDDEN_DIR/$RAT_ARCHIVE_NAME

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "rat-check: required command '$1' is not available on PATH." >&2
    exit 1
  }
}

sha512_file() {
  if command -v sha512sum >/dev/null 2>&1; then
    sha512sum "$1" | cut -d' ' -f1
  else
    shasum -a 512 "$1" | cut -d' ' -f1
  fi
}

java_major() {
  java -version 2>&1 | sed -n 's/.*version "\([0-9][0-9]*\)\(\.[^"]*\)\?".*/\1/p' | sed -n '1p'
}

require_command curl
require_command java
require_command tar
command -v sha512sum >/dev/null 2>&1 || require_command shasum

java_version=$(java_major)
[ -n "$java_version" ] || {
  echo 'rat-check: unable to determine the active Java version.' >&2
  exit 1
}
[ "$java_version" -ge 21 ] || {
  echo "rat-check: Apache RAT requires Java 21 or newer, but found Java $java_version." >&2
  exit 1
}

mkdir -p "$RAT_WORK_HIDDEN_DIR"
if [ -f "$ARCHIVE_PATH" ] && [ "$(sha512_file "$ARCHIVE_PATH")" != "$RAT_ARCHIVE_SHA512" ]; then
  rm -f "$ARCHIVE_PATH"
fi
if [ ! -f "$ARCHIVE_PATH" ]; then
  temp_archive="$ARCHIVE_PATH.$$.download"
  trap 'rm -f "$temp_archive"' EXIT HUP INT TERM
  curl --fail --location --silent --show-error --output "$temp_archive" "$RAT_ARCHIVE_URL"
  [ "$(sha512_file "$temp_archive")" = "$RAT_ARCHIVE_SHA512" ] || {
    echo 'rat-check: downloaded Apache RAT archive checksum mismatch.' >&2
    exit 1
  }
  mv -f "$temp_archive" "$ARCHIVE_PATH"
  trap - EXIT HUP INT TERM
fi

work_dir=$(mktemp -d "$RAT_WORK_HIDDEN_DIR/work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
tar -xzf "$ARCHIVE_PATH" -C "$work_dir"
rat_jar="$work_dir/apache-rat-${RAT_VERSION}/apache-rat-${RAT_VERSION}.jar"
exclude_file="$TOOL_DIR/.rat-excludes"

# IMPORTANT: RAT 0.17 does not scan files like shell scripts or the executable python files,
# even if explicitly included.

java -jar "${rat_jar}" \
  --input-exclude-std GIT IDEA ECLIPSE \
  --input-exclude-file "$exclude_file" \
  --input-exclude-parsed-scm GIT \
  --license-families-approved AL \
  --input-include \
    .github/ \
    "**/.gitignore" \
  -- \
  . \
