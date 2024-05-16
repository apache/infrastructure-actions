# Standard plugins for ASF websites

All of these plugins are ALv2 except for **asfgenid** and **toc** which may be AGPL or permissive. This needs investigation.

## asfcopy

Copies a directory trees to output outside of the Pelican processing of content and static files.

## asfdata

During initiation of Pelican reads in data models to global metadata.

## asfgenid

Generates HeadingIDs, ElementID, and PermaLinks. This also generates ToC in a different style from **toc**.

## asfreader

Pelican plugin that processes ezt template Markdown through ezt and then GitHub Flavored Markdown.
Used to create views of data models initiated by **asfdata**.

## asfrun

During initiation runs scripts that can be used to create content and static files.

## gfm

Pelican plugin that processes Github Flavored Markdown(**GFM**) using the cmark library.

## toc

Generates Table of Contents for markdown.
Only generates a ToC for the headers FOLLOWING the [TOC] tag,
so you can insert it after a specific section that need not be
include in the ToC.
