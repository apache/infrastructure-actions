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
#
# Run: BUNDLE_GEMFILE=website-comment/adapters/jekyll/test/Gemfile bundle exec ruby website-comment/adapters/jekyll/test/preview_src_test.rb
# Needs the jekyll gem (4.x) and minitest.

require "minitest/autorun"
require "tmpdir"
require "fileutils"
require_relative "../preview_src"

FIXTURE = File.expand_path("fixture", __dir__)
PREPARE = File.expand_path("../prepare.sh", __dir__)

class PreviewSrcBuildTest < Minitest::Test
  def self.built
    @built ||= begin
      dest = Dir.mktmpdir("preview-src-site")
      ENV["PREVIEW_SRC_ROOT"] = FIXTURE
      config = Jekyll.configuration(
        "source" => FIXTURE, "destination" => dest, "quiet" => true,
        "disable_disk_cache" => true,
      )
      Jekyll::Site.new(config).process
      File.read(File.join(dest, "index.html"))
    ensure
      ENV.delete("PREVIEW_SRC_ROOT")
    end
  end

  def html = self.class.built

  def test_markdown_blocks_carry_their_file_line
    assert_match(/<h1[^>]*data-preview-src="index\.md:5"/, html)
    assert_match(/<p data-preview-src="index\.md:7">A paragraph\.<\/p>/, html)
    assert_match(/<li data-preview-src="index\.md:9">one/, html)
    assert_match(/<li data-preview-src="index\.md:10">two/, html)
    assert_match(/<blockquote data-preview-src="index\.md:12">/, html)
  end

  def test_layout_tags_carry_their_file_line
    assert_includes html, %(<header data-preview-src="_layouts/default.html:7" class="site">)
    assert_includes html, %(<main data-preview-src="_layouts/default.html:8">)
  end

  def test_include_tags_carry_their_file_line
    assert_includes html, %(<nav data-preview-src="_includes/nav.html:1">)
    assert_includes html, %(<a data-preview-src="_includes/nav.html:2" href="/">)
  end

  def test_liquid_output_is_not_stamped
    assert_includes html, "<b>Home"
  end

  def test_markdownify_in_a_layout_is_not_given_the_page_path
    assert_includes html, "<p><strong>from markdownify</strong></p>"
  end

  def test_opaque_regions_and_document_chrome_are_untouched
    assert_includes html, %(var x = "<div>";)
    assert_includes html, "<p>literal</p>"
    assert_includes html, "<!-- <section>commented</section> -->"
    assert_includes html, "<body>\n"
    assert_includes html, "<html>\n"
    assert_includes html, "<head><title>"
  end
end

class PreviewSrcUnitTest < Minitest::Test
  def test_stamp_template_tracks_lines_across_an_opaque_region
    src = "<div>\n{% comment %}\n<p>x</p>\n{% endcomment %}\n<span>y</span>\n"
    out = PreviewSrc.stamp_template(src, "f.html", 10)
    assert_includes out, %(<div data-preview-src="f.html:10">)
    assert_includes out, "<p>x</p>"
    assert_includes out, %(<span data-preview-src="f.html:14">)
  end

  def test_stamp_template_leaves_liquid_spans_alone
    out = PreviewSrc.stamp_template(%(<a href="{{ '<i>' }}">{% if x %}<em>{% endif %}), "f.html", 1)
    assert_includes out, %(<a data-preview-src="f.html:1" href="{{ '<i>' }}">)
    assert_includes out, %({% if x %}<em data-preview-src="f.html:1">{% endif %})
  end

  def test_front_matter_lines
    Dir.mktmpdir do |dir|
      path = File.join(dir, "p.md")
      File.write(path, "---\na: 1\n---\nbody\n")
      assert_equal 3, PreviewSrc.front_matter_lines(path)
      File.write(path, "no front matter\n")
      assert_equal 0, PreviewSrc.front_matter_lines(path)
    end
  end

  def test_repo_root_walks_up_to_git_and_skips_files_outside_it
    Dir.mktmpdir do |dir|
      FileUtils.mkdir_p(File.join(dir, ".git"))
      site = File.join(dir, "site", "src")
      FileUtils.mkdir_p(site)
      root = PreviewSrc.repo_root(site)
      assert_equal File.realpath(dir), File.realpath(root.to_s)
      assert_equal "site/src/a.md", PreviewSrc.relative(root, File.join(site, "a.md"))
      assert_nil PreviewSrc.relative(root, "/usr/lib/gems/theme/_layouts/x.html")
    end
  end

  def test_prepare_installs_the_plugin_and_refuses_safe_mode
    Dir.mktmpdir do |dir|
      File.write(File.join(dir, "_config.yml"), "plugins_dir: my_plugins\n")
      assert system(PREPARE, dir, out: File::NULL)
      assert File.file?(File.join(dir, "my_plugins", "preview_src.rb"))

      File.write(File.join(dir, "_config.yml"), "safe: true\n")
      refute system(PREPARE, dir, out: File::NULL, err: File::NULL)
    end
  end
end
