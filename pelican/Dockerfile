# Settings
# ========
ARG PYTHON_VERSION=3.9.17
ARG GFM_VERSION=0.28.3.gfm.12 # must agree with copy below

# Build cmake-gfm
FROM python:${PYTHON_VERSION}-slim-buster as pelican-asf

RUN apt update && apt upgrade -y
RUN apt install curl cmake build-essential -y

WORKDIR /opt/pelican-asf

# Copy only what we need to build cmark-gfm
COPY build-cmark.sh bin/build-cmark.sh

# build it
RUN bash bin/build-cmark.sh ${GFM_VERSION}

# rebase the image to save on image size
FROM python:${PYTHON_VERSION}-slim-buster

# Use the Pelican version as installed on CI pelican builders (2023-06-02)
ARG PELICAN_VERSION=4.5.4
ARG GFM_VERSION=0.28.3.gfm.12 # must agree with copy above

# Where we put GFM and the plugins
ENV WORKDIR /opt/pelican-asf
ENV LIBCMARKDIR ${WORKDIR}/cmark-gfm-${GFM_VERSION}/lib

RUN apt update && apt upgrade -y

# subversion is used by asfdata.py plugin in template-site to retrieve release information
# wget is used by pagefind.sh (www-site)
RUN apt install subversion wget -y

# Need markdown as fallback for gfm as documented in ASF.YAML
RUN pip install pelican[markdown]==${PELICAN_VERSION}
# [1] https://cwiki.apache.org/confluence/display/INFRA/Git+-+.asf.yaml+features#Git.asf.yamlfeatures-PelicanCMS

# Copy the built cmark and ASF
WORKDIR $LIBCMARKDIR
COPY --from=pelican-asf $LIBCMARKDIR .

WORKDIR $WORKDIR

COPY requirements.txt .
# Don't automatically load dependencies; please add them to requirements.txt instead
RUN pip install -r requirements.txt --no-deps

# Could perhaps be added to requirements.txt but that would affect other uses
RUN pip install 'MarkupSafe<2.1.0' # needed for Pelican 4.5.4

# Now add the local code; do this last to avoid unnecessary rebuilds
COPY plugin_paths.py .
COPY plugins plugins
# Tidy up any old builds
RUN { find plugins -type d -name __pycache__ -exec rm -rf {} \; ; true; }

# If the site needs authtokens to build, copy them into the file .authtokens
# and it will be picked up at build time
# N.B. make sure the .authtokens file is not committed to the repo!
RUN ln -s /site/.authtokens /root/.authtokens

WORKDIR /site

# Create a pelicanasf wrapper to add required arguments
# The initial hashbang is needed, otherwise docker complains: 'exec format error'
RUN { echo '#!/usr/bin/env bash' >/usr/local/bin/pelicanasf; chmod +x /usr/local/bin/pelicanasf;}
# The -b (bind) is needed to allow connections from the host
RUN echo 'SRC=${1:?source}; shift; python3 -B -m pelican $SRC -b 0.0.0.0 -e "$(python3 $WORKDIR/plugin_paths.py $WORKDIR/plugins)" "$@"' >>/usr/local/bin/pelicanasf;

# --autoreload (-r) and --listen (-l)
ENTRYPOINT [ "pelicanasf", "content", "-rl"]
