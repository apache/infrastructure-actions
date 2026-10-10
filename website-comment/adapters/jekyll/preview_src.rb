# frozen_string_literal: true

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

# Stamps rendered elements with the source file and line they came from, as
# data-preview-src="<repo-relative path>:<line>", so a preview's review overlay
# can resolve a marked region back to a diff line. The Jekyll counterpart of
# adapters/astro/babel-plugin-preview-src.mjs.
#
# Preview builds only: prepare.sh copies this file into the site's _plugins/
# for the preview build, and nothing else loads it. A production build must
# carry none of these attributes.
#
# Two sources are stamped:
#   - Markdown pages and collection documents, from the line kramdown records
#     for each block, shifted past the front matter.
#   - Layouts and includes, by stamping each opening HTML tag of the raw
#     template before Liquid runs.
#
# Known limit: a Liquid tag inside Markdown that expands to several lines
# shifts the line of every block after it. The file is still right.

require "jekyll"
require "kramdown"
require "pathname"

module PreviewSrc
  ATTR = "data-preview-src"

  # Blocks stamped in Markdown. Inline elements are left alone: the overlay
  # walks up to the nearest stamped ancestor, and a block is the unit a
  # reviewer comments on.
  MARKDOWN_BLOCKS = %i[
    p header ul ol li blockquote table tr codeblock dl dt dd hr html_element math
  ].freeze

  # Never stamped in templates: they render nothing a reviewer can mark, or
  # sit above <body>, where the overlay's walk stops.
  SKIP_TAGS = %w[html head body meta link script style title base noscript template].freeze

  # Template regions whose contents are not markup to stamp.
  OPAQUE = [
    [/\{%-?\s*raw\s*-?%\}/, /\{%-?\s*endraw\s*-?%\}/],
    [/\{%-?\s*comment\s*-?%\}/, /\{%-?\s*endcomment\s*-?%\}/],
    [/\{%-?\s*highlight\b/, /\{%-?\s*endhighlight\s*-?%\}/],
    [/<script\b/i, %r{</script\s*>}i],
    [/<style\b/i, %r{</style\s*>}i],
    [/<!--/, /-->/],
  ].freeze

  LIQUID = /\{\{.*?\}\}|\{%.*?%\}/m
  OPEN_TAG = /<([a-zA-Z][a-zA-Z0-9-]*)(?=[\s>\/])/

  module_function

  # The directory paths are reported relative to: PREVIEW_SRC_ROOT when set,
  # else the nearest ancestor of the site source holding .git (a directory, or
  # a file in a worktree), else the site source itself.
  def repo_root(source)
    return Pathname.new(File.expand_path(ENV["PREVIEW_SRC_ROOT"])) if ENV["PREVIEW_SRC_ROOT"].to_s != ""

    dir = Pathname.new(File.expand_path(source))
    dir.ascend { |d| return d if (d + ".git").exist? }
    Pathname.new(File.expand_path(source))
  end

  # The repo-relative path, or nil for a file outside the repository — a
  # theme gem's layout or include, which no pull request can change.
  def relative(root, path)
    rel = Pathname.new(File.expand_path(path)).relative_path_from(root).to_s
    rel.start_with?("..") ? nil : rel
  rescue ArgumentError
    nil
  end

  # The 1-based line of the front matter's closing delimiter, or 0 when the
  # file has none, so body line N is file line N + this. Mirrors the pattern
  # Jekyll itself uses to split front matter from content.
  def front_matter_lines(path)
    raw = File.read(path, encoding: "UTF-8")
    match = /\A(---\s*\n.*?\n?)^((---|\.\.\.)\s*$\n?)/m.match(raw)
    match ? match[0].count("\n") : 0
  rescue SystemCallError
    0
  end

  # Stamp every opening tag of a raw template with its file line. Liquid spans
  # and opaque regions are copied through untouched.
  def stamp_template(content, rel, first_line)
    closer = nil
    content.each_line.with_index.map do |line, i|
      where = "#{rel}:#{first_line + i}"
      out = +""
      rest = line
      until rest.empty?
        if closer
          at = closer.match(rest)
          if at.nil?
            out << rest
            break
          end
          out << rest[0...at.end(0)]
          rest = rest[at.end(0)..]
          closer = nil
          next
        end

        opener, close = OPAQUE.map { |o, c| [o.match(rest), c] }
                              .reject { |m, _| m.nil? }
                              .min_by { |m, _| m.begin(0) }
        stop = opener ? opener.begin(0) : rest.length
        out << stamp_segment(rest[0...stop], where)
        break unless opener

        out << rest[stop...opener.end(0)]
        rest = rest[opener.end(0)..]
        closer = close
      end
      out
    end.join
  end

  # Stamp the tags in a run of template text that lies outside Liquid spans.
  def stamp_segment(text, where)
    pieces = text.split(/(#{LIQUID})/)
    pieces.each_with_index.map do |piece, i|
      next piece if i.odd?

      piece.gsub(OPEN_TAG) do |tag|
        name = Regexp.last_match(1)
        SKIP_TAGS.include?(name.downcase) ? tag : %(#{tag} #{ATTR}="#{where}")
      end
    end.join
  end

  # Set while Jekyll converts one document's own content, and read by the
  # kramdown converter below. Unset for markdownify in a layout, which would
  # otherwise be stamped with the page's path.
  def current
    Thread.current[:preview_src]
  end

  def with_document(document)
    site = document.site
    # Page#path is source-relative and Document#path absolute; relative_path
    # is source-relative on both.
    path = site.in_source_dir(document.relative_path.to_s)
    rel = File.file?(path) ? relative(repo_root(site.source), path) : nil
    return yield unless rel

    Thread.current[:preview_src] = { rel: rel, offset: front_matter_lines(path) }
    yield
  ensure
    Thread.current[:preview_src] = nil
  end

  module Renderer
    def convert(content)
      PreviewSrc.with_document(document) { super }
    end
  end

  def stamp_tree(el, ctx)
    line = el.options[:location]
    if line && MARKDOWN_BLOCKS.include?(el.type) && !el.attr.key?(ATTR)
      el.attr[ATTR] = "#{ctx[:rel]}:#{line + ctx[:offset]}"
    end
    el.children.each { |child| stamp_tree(child, ctx) }
  end

  def stamp_include(site, path, content)
    rel = relative(repo_root(site.source), path)
    rel ? stamp_template(content, rel, 1) : content
  end

  # kramdown calls #convert on the root only; every child is dispatched
  # straight to its convert_<type>. So the whole tree is stamped from the root.
  module KramdownHtml
    def convert(el, *args)
      ctx = PreviewSrc.current
      PreviewSrc.stamp_tree(el, ctx) if ctx && el.type == :root
      super
    end
  end

  # `include` is OptimizedIncludeTag, which renders a Jekyll::Inclusion and
  # reads the file through Inclusion#content; `include_relative` still goes
  # through IncludeTag#read_file. Both are read once per build and cached.
  module Inclusion
    def content
      @content ||= PreviewSrc.stamp_include(site, path, super)
    end
  end

  module IncludeTag
    def read_file(file, context)
      PreviewSrc.stamp_include(context.registers[:site], file, super)
    end
  end
end

Jekyll::Renderer.prepend(PreviewSrc::Renderer)
Kramdown::Converter::Html.prepend(PreviewSrc::KramdownHtml)
Jekyll::Inclusion.prepend(PreviewSrc::Inclusion)
Jekyll::Tags::IncludeTag.prepend(PreviewSrc::IncludeTag)

# Layouts are read once per build, after which their content is only parsed.
Jekyll::Hooks.register :site, :post_read do |site|
  root = PreviewSrc.repo_root(site.source)
  site.layouts.each_value do |layout|
    next unless layout.path && File.file?(layout.path)

    rel = PreviewSrc.relative(root, layout.path)
    next unless rel

    layout.content = PreviewSrc.stamp_template(
      layout.content, rel, PreviewSrc.front_matter_lines(layout.path) + 1,
    )
  end
end
