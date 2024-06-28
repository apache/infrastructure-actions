
import datetime
# Basic information about the site.
SITENAME = 'Apache Infra Actions'
SITEDESC = 'Test site for Infra actions'
SITEDOMAIN = 'infra.apache.org'
SITEURL = 'https://infra.apache.org'
SITELOGO = 'https://infra.apache.org/images/AOO4_website_logo.png'
SITEREPOSITORY = 'https://github.com/apache/infrastructure-actions/testsite/'
CURRENTYEAR = datetime.date.today().year
NOW = datetime.datetime.now()
TRADEMARKS = ''
TIMEZONE = 'UTC'
# Theme includes templates and possibly static files
THEME = 'simple' # a built-in theme
# Specify location of plugins, and which to use
PLUGIN_PATHS = [ 'plugins' ] # For local plugins
# If the website uses any *.ezmd files, include the 'asfreader' plugin
PLUGINS = [ 'test', 'asfgenid', 'asfrun' ] # asfsignals
ASF_RUN = [ '/bin/bash show_environ.sh start' ]
ASF_POSTRUN = [ '/bin/bash show_environ.sh end' ]

# All content is located at '.' (aka content/ )
PAGE_PATHS = [ 'pages' ]
STATIC_PATHS = [ '.',  ]
# Where to place/link generated pages

PATH_METADATA = 'pages/(?P<path_no_ext>.*)\\..*'

PAGE_SAVE_AS = '{path_no_ext}.html'
# Don't try to translate
PAGE_TRANSLATION_ID = None
# Disable unused Pelican features
# N.B. These features are currently unsupported, see https://github.com/apache/infrastructure-pelican/issues/49
FEED_ALL_ATOM = None
INDEX_SAVE_AS = ''
TAGS_SAVE_AS = ''
CATEGORIES_SAVE_AS = ''
AUTHORS_SAVE_AS = ''
ARCHIVES_SAVE_AS = ''
# Disable articles by pointing to a (should-be-absent) subdir
ARTICLE_PATHS = [ 'blog' ]
# needed to create blogs page
ARTICLE_URL = 'blog/{slug}.html'
ARTICLE_SAVE_AS = 'blog/{slug}.html'
# Disable all processing of .html files
READERS = { 'html': None, }

# Configure the asfgenid plugin
ASF_GENID = {
 'unsafe_tags': True,
 'metadata': True, # to pick up {{metadata}} references in MD files
 'elements': True,
 'permalinks': True,
 'tables': True,

 'headings': True,
 'headings_re': '^h[1-4]',


 'toc': True,
 'toc_headers': '^h[1-6]',

 'debug': False,
}
