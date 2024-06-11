#!/bin/bash
#
# Build the cmark-gfm library and extensions within CURRENT DIRECTORY.
# 
# The binary output will be under: cmark-gfm-$VERSION/lib
# 
# USAGE:
#   $ build-cmark.sh [ VERSION [ TARDIR ] ]
#
#   VERSION: defaults to 0.28.3.gfm.12
#   TARDIR: where to find a downloaded/cached tarball of the cmark
#           code, or where to place a tarball
#

# Echo all of our steps if DEBUG_STEPS is set
test -n "$DEBUG_STEPS" && set -x

set -e # early exit if any step fails

#VERSION=0.28.3.gfm.20  ### not yet
VERSION=0.28.3.gfm.12
if [ "$1" != "" ]; then VERSION="$1"; fi

# The tarball exists here, or will be downloaded here.
TARDIR="."
if [ "$2" != "" ]; then TARDIR="$2"; fi

ARCHIVES="https://github.com/github/cmark-gfm/archive/refs/tags"
LOCAL="${TARDIR}/cmark-gfm.$VERSION.orig.tar.gz"

# WARNING: this must agree with the parent directory in the tar file or the build will fail
EXTRACTED_AS="cmark-gfm-$VERSION"

# Follow redirects, and place the result into known name $LOCAL
if [ -f "$LOCAL" ]; then
    echo "Using cached tarball: ${LOCAL}" >&2
else
    echo "Fetching $VERSION from cmark archives" >&2
    curl -sSL --fail -o "$LOCAL" "$ARCHIVES/$VERSION.tar.gz"
fi

# Clean anything old, then extract and build.
### somebody smart could peek into the .tgz. ... MEH
if [ -d "$EXTRACTED_AS" ]; then rm -r "$EXTRACTED_AS"; fi
tar xzf "$LOCAL"
pushd "$EXTRACTED_AS"
  mkdir build
  pushd build
    cmake --version >&2
    cmake -DCMARK_TESTS=OFF -DCMARK_STATIC=OFF ..
    make
  popd

  mkdir lib
  cp -Pp build/src/lib* lib/
  cp -Pp build/extensions/lib* lib/
popd

# These files/dir may need a reference with LD_LIBRARY_PATH.
# gfm.py wants this lib/ in LIBCMARKDIR.
ls -laF "$EXTRACTED_AS/lib/"

# Provide a handy line for copy/paste.
echo "export LIBCMARKDIR='$(pwd)/$EXTRACTED_AS/lib'"
