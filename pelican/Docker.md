<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->
Docker execution instructions
=============================

Prerequisites
-------------

You will need Docker and git.

Building the image
------------------

$ git clone https://github.com/apache/infrastructure-pelican PELICANDIR

$ cd PELICANDIR

$ docker build -t IMAGENAME pelican

This will build the image as IMAGENAME (choose a better name!)

Running the image
-----------------

$ cd <website checkout containing pelicanconf.py>

$ docker run --rm -it -p8000:8000 -v $PWD:/site IMAGENAME

This will start the Docker container.
The website files are mapped to /site.

Browse to http://127.0.0.1:8000/

Changes to the website files should be reflected in the website display

Running the image interactively
-------------------------------

$ cd WEBSITE # directory must contain pelicanconf.py

$ docker run --rm -it -p8000:8000 -v $PWD:/site --entrypoint bash IMAGENAME

This will start a shell in the container.
Use the `pelicanasf` wrapper command to run Pelican as it automatically adds
the location of the plugins to the Pelican configuration.

For example, to build once:

$ pelicanasf content

To build again to a different directory:

$ pelicanasf content -t output2

Testing changes to plugins
--------------------------

For testing changes to plugins, one can remap the plugins directory to
the host directory by adding a option of the form:

$ docker ... -v <hostpath>:/opt/pelican-asf/plugins ...
where <hostpath> is the path to pelican/plugins

The next run of pelicanasf will pick up any changes
