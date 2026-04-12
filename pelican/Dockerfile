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
# Settings
# ========
ARG PYTHON_VERSION=3.10.18-slim-bullseye
ARG GFM_VERSION=0.28.3.gfm.12 # must agree with copy below

# Build cmake-gfm
FROM python:${PYTHON_VERSION} AS pelican-asf

RUN apt update && apt upgrade -y
RUN apt install curl cmake build-essential -y

WORKDIR /opt/pelican-asf

# Copy only what we need to build cmark-gfm
COPY build-cmark.sh bin/build-cmark.sh

# build it
RUN bash bin/build-cmark.sh ${GFM_VERSION}

# rebase the image to save on image size
FROM python:${PYTHON_VERSION}

ARG GFM_VERSION=0.28.3.gfm.12 # must agree with copy above

# Where we put GFM and the plugins
ENV WORKDIR=/opt/pelican-asf
ENV LIBCMARKDIR=${WORKDIR}/cmark-gfm-${GFM_VERSION}/lib
# `uv sync` writes a project venv at $WORKDIR/.venv; put its bin dir on
# PATH so the `pelican` CLI is visible to the pelicanasf wrapper at
# runtime.
ENV PATH="/opt/pelican-asf/.venv/bin:$PATH"

RUN apt update && apt upgrade -y

# subversion is used by asfdata.py plugin in template-site to retrieve release information
# wget is used by pagefind.sh (www-site)
RUN apt install subversion wget -y

# Bootstrap uv — used for every subsequent Python install.
RUN pip install uv

# Copy the built cmark and ASF
WORKDIR $LIBCMARKDIR
COPY --from=pelican-asf $LIBCMARKDIR .

WORKDIR $WORKDIR

# Copy enough of the checkout to let uv build the project. py-modules
# in pyproject.toml requires plugin_paths.py, and readme = "README.md"
# requires README.md to be present when setuptools builds the wheel.
# uv.lock is the source of truth for resolved versions.
COPY pyproject.toml uv.lock plugin_paths.py README.md ./

# Install the project and its dev group (which includes Pelican itself)
# into $WORKDIR/.venv straight from the lockfile. --frozen makes the
# build fail if uv.lock is out of sync with pyproject.toml, so the image
# always reflects the committed lockfile rather than re-resolving at
# build time. The `pelican` CLI ends up at .venv/bin/pelican (already on
# PATH) and the venv's own Python can import `plugin_paths` as a module
# — we use that below so user-supplied pelicanconf.py can import any
# dep that lives in the venv.
# [1] https://github.com/apache/infrastructure-asfyaml/blob/main/README.md#pelican_cms
RUN uv sync --frozen

# Now add the plugins; these are loaded by Pelican via PLUGIN_PATHS at
# runtime and are never imported as a Python package.
COPY plugins plugins
# Tidy up any old builds
RUN { find plugins -type d -name __pycache__ -exec rm -rf {} \; ; true; }

# If the site needs authtokens to build, copy them into the file .authtokens
# and it will be picked up at build time
# N.B. make sure the .authtokens file is not committed to the repo!
RUN ln -s /site/.authtokens /root/.authtokens

WORKDIR /site

# Create a pelicanasf wrapper around the `pelican` CLI (from the project
# venv) that injects the PLUGIN_PATHS settings computed by plugin_paths.
# plugin_paths is invoked as a module via the venv's own Python, so the
# user's pelicanconf.py can import any dep that lives in the venv.
# The initial hashbang is required, otherwise docker complains with
# 'exec format error'. -b (bind) allows connections from the host.
RUN TOOLPY="$WORKDIR/.venv/bin/python" && \
    { echo '#!/usr/bin/env bash'; \
      echo 'set -euo pipefail'; \
      echo "TOOLPY='$TOOLPY'"; \
      echo "WORKDIR='$WORKDIR'"; \
      echo 'SRC=${1:?source}; shift'; \
      echo 'if [[ ! -f pelicanconf.py ]]; then'; \
      echo '  echo "pelicanasf: pelicanconf.py not found in $(pwd)." >&2'; \
      echo '  echo "Mount a Pelican website checkout (one containing pelicanconf.py) at /site, e.g." >&2'; \
      echo '  echo "  docker run --rm -it -p 8000:8000 -v \"\$PWD\":/site <image>" >&2'; \
      echo '  exit 1'; \
      echo 'fi'; \
      echo 'PP=$("$TOOLPY" -m plugin_paths "$WORKDIR/plugins")'; \
      echo 'exec pelican "$SRC" -b 0.0.0.0 -e "$PP" "$@"'; \
    } > /usr/local/bin/pelicanasf && \
    chmod +x /usr/local/bin/pelicanasf

# --autoreload (-r) and --listen (-l)
ENTRYPOINT [ "pelicanasf", "content", "-rl"]
