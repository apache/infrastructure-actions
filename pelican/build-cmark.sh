#!/bin/bash
#
# Build the cmark-gfm library and extensions in a temporary directory
# 
# The binary output will be under: LIBCMARKDIR
# 
# USAGE:
#   $ build-cmark.sh VERSION LIBCMARKDIR [TARFILE]
#
#   VERSION: e.g. 0.28.3.gfm.12
#   LIBCMARKDIR: where to put the binary library files
#   TARFILE: local copy of the tarfile; must be for the correct version! (optional)
#

# Echo all of our steps if DEBUG_STEPS is set
test -n "$DEBUG_STEPS" && set -x

set -e # early exit if any step fails

VERSION=${1:?version}
LIBCMARKDIR=${2:?library output}
TARFILE=$3

if [[ -n $3 ]]
then 
  mkdir -p $3
  cd $3
fi

ARCHIVES="https://github.com/github/cmark-gfm/archive/refs/tags"
TARNAME="cmark-gfm.$VERSION.orig.tar.gz"
TARDIR="cmark-gfm-$VERSION"

# Work in a temporary directory
TEMP=$(mktemp -d)

if [[ -f $TARFILE ]]
then
  echo "Found tar!"
  cp $TARFILE $TEMP # do this before cd to allow for relative paths
  cd $TEMP
else
  cd $TEMP
  echo "Fetching $VERSION from cmark archives" >&2
  curl -sSL --fail -o "$TARNAME" "$ARCHIVES/$VERSION.tar.gz"
fi

tar xzf "$TARNAME"
pushd "$TARDIR" >/dev/null
  mkdir build
  pushd build >/dev/null
    cmake --version >&2
    {
      cmake -DCMARK_TESTS=OFF -DCMARK_STATIC=OFF ..
      make
    } > build.log
  popd >/dev/null

  cp -Pp build/src/lib* ${LIBCMARKDIR}/
  cp -Pp build/extensions/lib* ${LIBCMARKDIR}/
popd >/dev/null
