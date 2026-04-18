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
# Dockerfile for rebuilding a GitHub Action's compiled JavaScript
# in an isolated container.  Used by verify-action-build to compare
# published dist/ output against a from-scratch rebuild.

ARG NODE_VERSION=20
FROM node:${NODE_VERSION}-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN corepack enable

WORKDIR /action

ARG REPO_URL
ARG COMMIT_HASH

RUN git clone "$REPO_URL" . && git checkout "$COMMIT_HASH"

# Detect action type from action.yml or action.yaml.
# For monorepo sub-actions (SUB_PATH set), check <sub_path>/action.yml first,
# falling back to the root action.yml.
ARG SUB_PATH=""
RUN if [ -n "$SUB_PATH" ] && [ -f "$SUB_PATH/action.yml" ]; then \
      ACTION_FILE="$SUB_PATH/action.yml"; \
    elif [ -n "$SUB_PATH" ] && [ -f "$SUB_PATH/action.yaml" ]; then \
      ACTION_FILE="$SUB_PATH/action.yaml"; \
    else \
      ACTION_FILE=$(ls action.yml action.yaml 2>/dev/null | head -1); \
    fi; \
    if [ -n "$ACTION_FILE" ]; then \
      grep -E '^\s+using:' "$ACTION_FILE" | head -1 | sed 's/.*using:\s*//' | tr -d "'\"" > /action-type.txt; \
      MAIN_PATH=$(grep -E '^\s+main:' "$ACTION_FILE" | head -1 | sed 's/.*main:\s*//' | tr -d "'\" "); \
      echo "$MAIN_PATH" > /main-path.txt; \
    else \
      echo "unknown" > /action-type.txt; \
      echo "" > /main-path.txt; \
    fi

# Detect the output directory from the main: path.
# For monorepo actions the main: field may use relative paths like ../dist/sub/main/index.js
# Resolve relative to the sub-action directory to get the actual repo-root-relative path.
RUN MAIN_PATH=$(cat /main-path.txt); \
    OUT_DIR="dist"; \
    if [ -n "$MAIN_PATH" ] && [ -n "$SUB_PATH" ]; then \
      RESOLVED=$(cd "$SUB_PATH" 2>/dev/null && realpath --relative-to=/action "$MAIN_PATH" 2>/dev/null || echo ""); \
      if [ -n "$RESOLVED" ]; then \
        OUT_DIR=$(echo "$RESOLVED" | cut -d'/' -f1); \
      fi; \
    elif [ -n "$MAIN_PATH" ]; then \
      DIR_PART=$(echo "$MAIN_PATH" | sed 's|/[^/]*$||'); \
      if [ "$DIR_PART" != "$MAIN_PATH" ] && [ -n "$DIR_PART" ]; then \
        OUT_DIR=$(echo "$DIR_PART" | cut -d'/' -f1); \
      fi; \
    fi; \
    echo "$OUT_DIR" > /out-dir.txt

# Save original output files before rebuild
RUN OUT_DIR=$(cat /out-dir.txt); \
    if [ -d "$OUT_DIR" ]; then cp -r "$OUT_DIR" /original-dist; else mkdir /original-dist; fi

# Detect if node_modules/ is committed (vendored dependencies pattern)
RUN if [ -d "node_modules" ]; then \
      echo "true" > /has-node-modules.txt; \
      cp -r node_modules /original-node-modules; \
    else \
      echo "false" > /has-node-modules.txt; \
      mkdir /original-node-modules; \
    fi

# Delete compiled JS from output dir before rebuild to ensure a clean build.
# Covers .js, .cjs and .mjs — actions bundled with esbuild/rollup may emit
# dist/index.cjs (e.g. JustinBeckwith/linkinator-action) or dist/index.mjs.
RUN OUT_DIR=$(cat /out-dir.txt); \
    if [ -d "$OUT_DIR" ]; then \
      find "$OUT_DIR" \( -name '*.js' -o -name '*.cjs' -o -name '*.mjs' \) -print -delete > /deleted-js.log 2>&1; \
    else \
      echo "no $OUT_DIR/ directory" > /deleted-js.log; \
    fi

# If an approved (previous) commit hash is provided, restore the dev-dependency
# lock files from that commit so the rebuild uses the same toolchain (e.g. same
# rollup/ncc/webpack version) that produced the original dist/.
# This avoids false positives when a version bump updates devDependencies but the
# committed dist/ was built with the old toolchain.
ARG APPROVED_HASH=""
RUN if [ -n "$APPROVED_HASH" ]; then \
      echo "approved-hash: $APPROVED_HASH" >> /build-info.log; \
      for f in package.json package-lock.json yarn.lock pnpm-lock.yaml; do \
        if [ -f "$f" ]; then \
          if git show "$APPROVED_HASH:$f" > "/tmp/approved-$f" 2>/dev/null; then \
            cp "/tmp/approved-$f" "$f"; \
            echo "restored: $f from approved $APPROVED_HASH" >> /build-info.log; \
          fi; \
        fi; \
      done; \
    fi

# Detect the build directory — where package.json lives.
# Some repos (e.g. gradle/actions) keep sources in a subdirectory with its own package.json.
# Also check for a root-level build script (e.g. a 'build' shell script).
RUN BUILD_DIR="."; \
    if [ ! -f package.json ]; then \
      for candidate in sources src; do \
        if [ -f "$candidate/package.json" ]; then \
          BUILD_DIR="$candidate"; \
          break; \
        fi; \
      done; \
    fi; \
    echo "$BUILD_DIR" > /build-dir.txt

# For actions with vendored node_modules, delete and reinstall with --production
# before the normal build step (which will also install devDeps for building).
RUN if [ "$(cat /has-node-modules.txt)" = "true" ]; then \
      rm -rf node_modules && \
      BUILD_DIR=$(cat /build-dir.txt) && \
      cd "$BUILD_DIR" && \
      if [ -f yarn.lock ]; then \
        corepack prepare --activate 2>/dev/null; \
        yarn install --production 2>/dev/null || yarn install 2>/dev/null || true; \
        echo "node_modules-reinstall: yarn --production (in $BUILD_DIR)" >> /build-info.log; \
      elif [ -f pnpm-lock.yaml ]; then \
        corepack prepare --activate 2>/dev/null; \
        pnpm install --prod 2>/dev/null || pnpm install 2>/dev/null || true; \
        echo "node_modules-reinstall: pnpm --prod (in $BUILD_DIR)" >> /build-info.log; \
      else \
        npm ci --production 2>/dev/null || npm install --production 2>/dev/null || true; \
        echo "node_modules-reinstall: npm --production (in $BUILD_DIR)" >> /build-info.log; \
      fi && \
      cd /action && \
      cp -r node_modules /rebuilt-node-modules; \
    else \
      mkdir /rebuilt-node-modules; \
    fi

# Detect and install with the correct package manager (in the build directory)
RUN BUILD_DIR=$(cat /build-dir.txt); \
    cd "$BUILD_DIR" && \
    if [ -f yarn.lock ]; then \
      corepack prepare --activate 2>/dev/null; \
      yarn install 2>/dev/null || true; \
      echo "pkg-manager: yarn (in $BUILD_DIR)" >> /build-info.log; \
    elif [ -f pnpm-lock.yaml ]; then \
      corepack prepare --activate 2>/dev/null; \
      pnpm install 2>/dev/null || true; \
      echo "pkg-manager: pnpm (in $BUILD_DIR)" >> /build-info.log; \
    else \
      npm ci 2>/dev/null || npm install 2>/dev/null || true; \
      echo "pkg-manager: npm (in $BUILD_DIR)" >> /build-info.log; \
    fi

# Detect which run command to use (in the build directory)
RUN BUILD_DIR=$(cat /build-dir.txt); \
    cd "$BUILD_DIR" && \
    if [ -f yarn.lock ]; then \
      echo "yarn" > /run-cmd; \
    elif [ -f pnpm-lock.yaml ]; then \
      echo "pnpm" > /run-cmd; \
    else \
      echo "npm" > /run-cmd; \
    fi

# Build: first try a root-level build script (some repos like gradle/actions use one),
# then try npm/yarn/pnpm build/package/start in the build directory, then ncc fallback.
# After each step, check whether the output directory has JS files; if so, stop.
# Some actions need multiple steps (e.g. "build" compiles TS to lib/, then "package"
# bundles to dist/), so we continue trying subsequent steps until output appears.
# If the build directory is a subdirectory, copy its output dir to root afterwards.
RUN OUT_DIR=$(cat /out-dir.txt); \
    BUILD_DIR=$(cat /build-dir.txt); \
    RUN_CMD=$(cat /run-cmd); \
    has_output() { [ -d "$OUT_DIR" ] && find "$OUT_DIR" \( -name '*.js' -o -name '*.cjs' -o -name '*.mjs' \) -print -quit | grep -q .; }; \
    BUILD_DONE=false; \
    if [ -x build ] && ./build dist 2>/dev/null; then \
      echo "build-step: ./build dist" >> /build-info.log; \
      if has_output; then BUILD_DONE=true; fi; \
    fi && \
    if [ "$BUILD_DONE" = "false" ]; then \
      cd "$BUILD_DIR" && \
      for step in build package start; do \
        if $RUN_CMD run "$step" 2>/dev/null; then \
          echo "build-step: $RUN_CMD run $step (in $BUILD_DIR)" >> /build-info.log; \
          cd /action && \
          if [ "$BUILD_DIR" != "." ] && [ -d "$BUILD_DIR/$OUT_DIR" ] && [ ! -d "$OUT_DIR" ]; then \
            cp -r "$BUILD_DIR/$OUT_DIR" "$OUT_DIR"; \
            echo "copied $BUILD_DIR/$OUT_DIR -> $OUT_DIR" >> /build-info.log; \
          fi; \
          if has_output; then BUILD_DONE=true; break; fi; \
          cd "$BUILD_DIR"; \
        fi; \
      done && \
      if [ "$BUILD_DONE" = "false" ]; then \
        cd "$BUILD_DIR" && \
        if npx ncc build --source-map 2>/dev/null; then \
          echo "build-step: npx ncc build --source-map (in $BUILD_DIR)" >> /build-info.log; \
        fi && \
        cd /action && \
        if [ "$BUILD_DIR" != "." ] && [ -d "$BUILD_DIR/$OUT_DIR" ] && [ ! -d "$OUT_DIR" ]; then \
          cp -r "$BUILD_DIR/$OUT_DIR" "$OUT_DIR"; \
          echo "copied $BUILD_DIR/$OUT_DIR -> $OUT_DIR" >> /build-info.log; \
        fi; \
      fi; \
    fi

# Save rebuilt output files
RUN OUT_DIR=$(cat /out-dir.txt); \
    if [ -d "$OUT_DIR" ]; then cp -r "$OUT_DIR" /rebuilt-dist; else mkdir /rebuilt-dist; fi
