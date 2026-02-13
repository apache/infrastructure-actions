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
# Standard plugins for ASF websites

All of these plugins are ALv2 except for **asfgenid** and **toc**, which may be AGPL or permissive. Under investigation.

## asfcopy

Copies a directory tree to output outside of the Pelican processing of content and static files.

## asfdata

During initiation of Pelican, reads in data models to global metadata.

## asfgenid

Generates HeadingIDs, ElementIDs, and PermaLinks. This also generates ToC in a different style from **toc**.

## asfreader

Pelican plugin that processes ezt template Markdown through ezt and then GitHub Flavored Markdown.
Used to create views of data models initiated by **asfdata**.

## asfrun

During initiation, runs scripts that can be used to create content and static files.

## gfm

Pelican plugin that processes Github Flavored Markdown (**GFM**) using the cmark library.

## toc

Generates Table of Contents for markdown.
Only generates a ToC for the headers FOLLOWING the [TOC] tag,
so you can insert it after a specific section that need not be
included in the ToC.
