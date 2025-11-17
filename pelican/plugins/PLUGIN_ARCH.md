# Plugin Architecture

The plugins used operate at various points in a pelican build.
Pelican uses [signals](https://docs.getpelican.com/en/latest/plugins.html#list-of-signals) at various points.
At a high level consider the following sequence of events:

1. Pelican Settings. Settings for a Pelican Build are in your Pelican Configuration

   These are automatically created according to your `pelicanconf.yaml` into a settings file that would have something like:

   ```python
   PLUGIN_PATHS = ['./theme/plugins']
   PLUGINS = ['asfgenid', 'asfrun', 'asfdata', 'gfm', 'asfreader', 'asfcopy']
   ```

2. Init (initialized). At this point any ASF_DATA is read into a metadata dictionary made available in every page.

   - The [asfdata plugin](./asfdata.py) reads an .asfdata.yaml file and creates the metadata dictionary.

   ```yaml
   setup:
     data: asfdata.yaml
   ```

   ```python
   ASF_DATA_YAML = "asfdata.yaml"
   ASF_DATA = {
        'data': ASF_DATA_YAML,
        'metadata': { },
        'debug': True
   }
   ```

   - The [asfgenid plugin](./asfgenid.py) configures its features.

   ```yaml
   genid:
     unsafe: yes
     metadata: yes
     elements: yes
     headings_depth: 4
     permalinks: yes
     toc_depth: 4
     tables: yes
   ```

   ```python
   ASF_GENID = {
       'unsafe': True,
       'metadata': True,
       'elements': True,
       'headings': True,
       'permalinks': True,
       'toc': True,
       'toc_headers': r"h[1-6]",
       'debug': False
   }
   ```

   - The [asfrun plugin](./asfrun.py) runs shell scripts during initialization

   ```yaml
   setup:
     run:
       - /bin/bash shell.sh
   ```

   ```python
   ASF_RUN = [
       '/bin/bash shell.sh'
   ]
   ```

   - The [asfrun plugin](./asfrun.py) also runs shell scripts during finalization

   ```yaml
   setup:
     postrun:
       - /bin/bash postshell.sh
   ```

   ```python
   ASF_POSTRUN = [
       '/bin/bash postshell.sh'
   ]
   ```

3. Readers (readers_init). Two important readers are set at this point. Readers are responsible for transforming page files to html and
    providing a metadata dictionary
    - GFMReader by the gfm plugin. Transforms GitHub Flavored Markdown(GFM) to HTML.
        * .md
        * .markdown
        * .mkd
        * .mdown
    - ASFReader by the [asfreader plugin](./asfreader.py). Transforms an [ezt template](https://github.com/gstein/ezt) into GFM and then to HTML.
        * .ezmd

4. Content Init (content_object_init). This is signaled after a Reader has processed content.
    At this point plugins can review, record, and transform the html content.
    - The [asfgenid plugin](./asfgenid.py) performs a number of steps. Some of the steps are optional.
        * Metadata transformation by looking up {{ key_expression }} in the page metadata.
        * Inventory of existing id attributes.
        * Set id and class attributes specified by {#id} and {.class} syntax.
        * Assign id to all headings without ids.
        * Insert a Table of Contents if a [TOC] tag is present.

5. Finalization (finalized). This is signalled after all content is generated and static files copied.
   At this point additional content can be copied and the final output analyzed.
   - The [asfcopy plugin](.asfcopy.py) is an example. Here is copies files from the source tree.
     It could be modified to copy information in from external sources too.
