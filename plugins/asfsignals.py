#!/usr/bin/python -B
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
#
# asfsignals.py - optional Pelican plugin to show when signals are triggered and what args are passed
#

import pprint
import pelican.plugins.signals

def _pretty(obj):
    pp = pprint.PrettyPrinter(indent=2)
    pp.pprint(",".join(sorted(obj.__dict__.keys())))
    if 'metadata' in obj.__dict__.keys():
        pp.pprint(obj.metadata)
    print("========================")

def initialized(pelican_object):
    print(f"******initialized: (pelican_object: {pelican_object.__class__})")
    _pretty(pelican_object)

def finalized(pelican_object):
    print(f"******finalized: (pelican_object: {pelican_object.__class__})")
    _pretty(pelican_object)

def generator_init(generator):
    print(f"******generator_init: (generator: {generator.__class__})")
    _pretty(generator)

def all_generators_finalized(generators):
    print(f"******all_generators_finalized: (generators: {generators.__class__})")

def readers_init(readers):
    print(f"******readers_init: (readers: {readers.__class__})")
    _pretty(readers)

def article_generator_context(article_generator, metadata):
    print(f"******article_generator_context: (article_generator: {article_generator.__class__}, {metadata.__class__})")

def article_generator_preread(article_generator):
    print(f"******article_generator_preread: (article_generator: {article_generator.__class__})")

def article_generator_init(article_generator):
    print(f"******article_generator_init: (article_generator: {article_generator.__class__})")

def article_generator_pretaxonomy(article_generator):
    print(f"******article_generator_pretaxonomy: (article_generator: {article_generator.__class__})")

def article_generator_finalized(article_generator):
    print(f"******article_generator_finalized: (article_generator: {article_generator.__class__})")

def article_generator_write_article(article_generator, content):
    print(f"******article_generator_write_article: (article_generator: {article_generator.__class__}, content: {content.__class__})")

def article_writer_finalized(article_generator, writer):
    print(f"******article_writer_finalized: (article_generator: {article_generator.__class__}, writer: {writer.__class__})")

def get_generators(pelican_object):
    print(f"******get_generators: (pelican_object: {pelican_object.__class__})")
    return [] # must return an empty list

def get_writer(pelican_object):
    print(f"******get_writer: (pelican_object: {pelican_object.__class__})")
    return None

def page_generator_context(page_generator, metadata):
    print(f"******page_generator_context: (page_generator: {page_generator.__class__}, metadata: {metadata.__class__})")

def page_generator_preread(page_generator):
    print(f"******page_generator_preread: (page_generator: {page_generator.__class__})")

def page_generator_init(page_generator):
    print(f"******page_generator_init: (page_generator: {page_generator.__class__})")

def page_generator_finalized(page_generator):
    print(f"******page_generator_finalized: (page_generator: {page_generator.__class__})")

def page_generator_write_page(page_generator, content):
    print(f"******page_generator_write_page: (page_generator: {page_generator.__class__}, content: {content.__class__})")

def static_generator_context(static_generator, metadata):
    print(f"******static_generator_context: (static_generator: {static_generator.__class__}, metadata: {metadata.__class__})")

def static_generator_preread(static_generator):
    print(f"******static_generator_preread: (static_generator: {static_generator.__class__})")

def static_generator_init(static_generator):
    print(f"******static_generator_init: (static_generator: {static_generator.__class__})")

def static_generator_finalized(static_generator):
    print(f"******static_generator_finalized: (static_generator: {static_generator.__class__})")

def content_object_init(content_object):
    print(f"******content_object_init: (content: {content_object.__class__})")
    _pretty(content_object)

def content_written(path, context):
    print(f"******content_written: (path: {path.__class__}, context: {context.__class__})")

def feed_generated(context, feed):
    print(f"******feed_generated: (context: {context.__class__}, feed: {feed.__class__})")

def feed_written(path, context, feed):
    print(f"******feed_written: (path: {path.__class__}, context: {context.__class__}, feed: {feed.__class__})")

def register():
    pelican.plugins.signals.initialized.connect(initialized)
    pelican.plugins.signals.finalized.connect(finalized)
    pelican.plugins.signals.generator_init.connect(generator_init)
    pelican.plugins.signals.all_generators_finalized.connect(all_generators_finalized)
    pelican.plugins.signals.readers_init.connect(readers_init)
    pelican.plugins.signals.article_generator_context.connect(article_generator_context)
    pelican.plugins.signals.article_generator_preread.connect(article_generator_preread)
    pelican.plugins.signals.article_generator_init.connect(article_generator_init)
    pelican.plugins.signals.article_generator_pretaxonomy.connect(article_generator_pretaxonomy)
    pelican.plugins.signals.article_generator_finalized.connect(article_generator_finalized)
    pelican.plugins.signals.article_generator_write_article.connect(article_generator_write_article)
    pelican.plugins.signals.article_writer_finalized.connect(article_writer_finalized)
    pelican.plugins.signals.get_generators.connect(get_generators)
    pelican.plugins.signals.get_writer.connect(get_writer)
    pelican.plugins.signals.page_generator_context.connect(page_generator_context)
    pelican.plugins.signals.page_generator_preread.connect(page_generator_preread)
    pelican.plugins.signals.page_generator_init.connect(page_generator_init)
    pelican.plugins.signals.page_generator_finalized.connect(page_generator_finalized)
    pelican.plugins.signals.page_generator_write_page.connect(page_generator_write_page)
    pelican.plugins.signals.static_generator_context.connect(static_generator_context)
    pelican.plugins.signals.static_generator_preread.connect(static_generator_preread)
    pelican.plugins.signals.static_generator_init.connect(static_generator_init)
    pelican.plugins.signals.static_generator_finalized.connect(static_generator_finalized)
    pelican.plugins.signals.content_object_init.connect(content_object_init)
    pelican.plugins.signals.content_written.connect(content_written)
    pelican.plugins.signals.feed_generated.connect(feed_generated)
    pelican.plugins.signals.feed_written.connect(feed_written)

# From https://docs.getpelican.com/en/latest/plugins.html#list-of-signals
# 
# =================================   ============================   ===========================================================================
# Signal                              Arguments                       Description
# =================================   ============================   ===========================================================================
# initialized                         pelican object
# finalized                           pelican object                 invoked after all the generators are executed and just before pelican exits
#                                                                    useful for custom post processing actions, such as:
#                                                                    - minifying js/css assets.
#                                                                    - notify/ping search engines with an updated sitemap.
# generator_init                      generator                      invoked in the Generator.__init__
# all_generators_finalized            generators                     invoked after all the generators are executed and before writing output
# readers_init                        readers                        invoked in the Readers.__init__
# article_generator_context           article_generator, metadata
# article_generator_preread           article_generator              invoked before a article is read in ArticlesGenerator.generate_context;
#                                                                    use if code needs to do something before every article is parsed
# article_generator_init              article_generator              invoked in the ArticlesGenerator.__init__
# article_generator_pretaxonomy       article_generator              invoked before categories and tags lists are created
#                                                                    useful when e.g. modifying the list of articles to be generated
#                                                                    so that removed articles are not leaked in categories or tags
# article_generator_finalized         article_generator              invoked at the end of ArticlesGenerator.generate_context
# article_generator_write_article     article_generator, content     invoked before writing each article, the article is passed as content
# article_writer_finalized            article_generator, writer      invoked after all articles and related pages have been written, but before
#                                                                    the article generator is closed.
# get_generators                      pelican object                 invoked in Pelican.get_generator_classes,
#                                                                    can return a Generator, or several
#                                                                    generators in a tuple or in a list.
# get_writer                          pelican object                 invoked in Pelican.get_writer,
#                                                                    can return a custom Writer.
# page_generator_context              page_generator, metadata
# page_generator_preread              page_generator                 invoked before a page is read in PageGenerator.generate_context;
#                                                                    use if code needs to do something before every page is parsed.
# page_generator_init                 page_generator                 invoked in the PagesGenerator.__init__
# page_generator_finalized            page_generator                 invoked at the end of PagesGenerator.generate_context
# page_generator_write_page           page_generator, content        invoked before writing each page, the page is passed as content
# page_writer_finalized               page_generator, writer         invoked after all pages have been written, but before the page generator
#                                                                    is closed.
# static_generator_context            static_generator, metadata
# static_generator_preread            static_generator               invoked before a static file is read in StaticGenerator.generate_context;
#                                                                    use if code needs to do something before every static file is added to the
#                                                                    staticfiles list.
# static_generator_init               static_generator               invoked in the StaticGenerator.__init__
# static_generator_finalized          static_generator               invoked at the end of StaticGenerator.generate_context
# content_object_init                 content_object                 invoked at the end of Content.__init__
# content_written                     path, context                  invoked each time a content file is written.
# feed_generated                      context, feed                  invoked each time a feed gets generated. Can be used to modify a feed
#                                                                    object before it gets written.
# feed_written                        path, context, feed            invoked each time a feed file is written.
# =================================   ============================   ===========================================================================
