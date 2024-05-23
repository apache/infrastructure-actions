import argparse
import os
import fnmatch
import datetime

import yaml
import ezt


SCRATCH_DIR = "/tmp"
THIS_DIR = os.path.abspath(os.path.dirname(__file__))

# Automatic settings filenames
AUTO_SETTINGS_YAML = "pelicanconf.yaml"
AUTO_SETTINGS_TEMPLATE = "pelican.auto.ezt"
AUTO_SETTINGS = "pelicanconf.py"
BUILD_PELICAN_TEMPLATE = "build-pelican.ezt"

class _helper:
    def __init__(self, **kw):
        vars(self).update(kw)

def find(pattern, path):
    for _root, _dirs, files in os.walk(path):
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                return True
    return False

def generate_settings(source_yaml, settings_path, builtin_p_paths=None, sourcepath="."):
    """Generate the Pelican settings file

    :param source_yaml: the settings in YAML form
    :param settings_path: the path name to generate
    :param builtin_p_paths: list of plugin paths (defaults to [])
    :param sourcepath: path to source (defaults to '.')

    """
    print(f"Reading {source_yaml} in {sourcepath}")
    ydata = yaml.safe_load(open(source_yaml, encoding='utf-8'))

    print(f"Converting to {settings_path}...")
    tdata = ydata["site"]  # Easy to copy these simple values.
    tdata.update(
        {
            "year": datetime.date.today().year,
            "theme": ydata.get("theme", "theme/apache"),
            "debug": str(ydata.get("debug", False)),
        }
    )

    content = ydata.get("content", {})
    tdata["pages"] = content.get("pages")
    tdata["static"] = content.get(
        "static_dirs",
        [
            ".",
        ],
    )

    if builtin_p_paths is None:
        builtin_p_paths = []
    tdata["p_paths"] = builtin_p_paths
    tdata["use"] = ["gfm"]

    tdata["uses_sitemap"] = None
    if "plugins" in ydata:
        if "paths" in ydata["plugins"]:
            for p in ydata["plugins"]["paths"]:
                tdata["p_paths"].append(os.path.join(p))

        if "use" in ydata["plugins"]:
            tdata["use"] = ydata["plugins"]["use"]

        if "sitemap" in ydata["plugins"]:
            sm = ydata["plugins"]["sitemap"]
            sitemap_params = _helper(
                exclude=str(sm["exclude"]),
                format=sm["format"],
                priorities=_helper(
                    articles=sm["priorities"]["articles"],
                    indexes=sm["priorities"]["indexes"],
                    pages=sm["priorities"]["pages"],
                ),
                changefreqs=_helper(
                    articles=sm["changefreqs"]["articles"],
                    indexes=sm["changefreqs"]["indexes"],
                    pages=sm["changefreqs"]["pages"],
                ),
            )

            tdata["uses_sitemap"] = "yes"  # ezt.boolean
            tdata["sitemap"] = sitemap_params
            tdata["use"].append("sitemap")  # add the plugin

    tdata["uses_index"] = None
    if "index" in tdata:
        tdata["uses_index"] = "yes"  # ezt.boolean

    if "genid" in ydata:
        genid = _helper(
            unsafe=str(ydata["genid"].get("unsafe", False)),
            metadata=str(ydata["genid"].get("metadata", False)),
            elements=str(ydata["genid"].get("elements", False)),
            permalinks=str(ydata["genid"].get("permalinks", False)),
            tables=str(ydata["genid"].get("tables", False)),
            headings_depth=ydata["genid"].get("headings_depth"),
            toc_depth=ydata["genid"].get("toc_depth"),
        )

        tdata["uses_genid"] = "yes"  # ezt.boolean()
        tdata["genid"] = genid
        tdata["use"].append("asfgenid")  # add the plugin
    else:
        tdata["uses_genid"] = None

    tdata["uses_data"] = None
    tdata["uses_run"] = None
    tdata["uses_postrun"] = None
    tdata["uses_ignore"] = None
    tdata["uses_copy"] = None
    if "setup" in ydata:
        sdata = ydata["setup"]
        # Load data structures into the pelican METADATA.
        if "data" in sdata:
            tdata["uses_data"] = "yes"  # ezt.boolean()
            tdata["asfdata"] = sdata["data"]
            tdata["use"].append("asfdata")  # add the plugin
        # Run the included scripts with the asfrun plugin during initialize
        if "run" in sdata:
            tdata["uses_run"] = "yes"  # ezt.boolean
            tdata["run"] = sdata["run"]
            tdata["use"].append("asfrun")  # add the plugin
        # Run the included scripts with the asfrun plugin during finalize
        if "postrun" in sdata:
            tdata["uses_postrun"] = "yes"  # ezt.boolean
            tdata["postrun"] = sdata["postrun"]
            if not "run" in sdata:
                tdata["use"].append("asfrun")  # add the plugin (if not already added)
        # Ignore files avoids copying these files to output.
        if "ignore" in sdata:
            tdata["uses_ignore"] = "yes"  # ezt.boolean
            tdata["ignore"] = sdata["ignore"]
            # No plugin needed.
        # Copy directories to output.
        if "copy" in sdata:
            tdata["uses_copy"] = "yes"  # ezt.boolean
            tdata["copy"] = sdata["copy"]
            tdata["use"].append("asfcopy")  # add the plugin

    # if ezmd files are present then use the asfreader plugin
    if find('*.ezmd', sourcepath):
        tdata["use"].append("asfreader")  # add the plugin

    # We assume that pelicanconf.yaml is at the top level of the repo
    # so .asf.yaml amd .github/workflow are located under sourcepath
    workflows = os.path.join(sourcepath, ".github/workflows")
    if not os.path.isdir(workflows):
        print(f"Creating directory {workflows}")
        os.makedirs(workflows)
    workfile = f"{workflows}/build-pelican.yml"
    print(f"Creating workfile {workfile} from .asf.yaml")
    workfiletemplate = ezt.Template(os.path.join(THIS_DIR, BUILD_PELICAN_TEMPLATE), 0)
    asfyaml = os.path.join(sourcepath,'.asf.yaml')
    with open(workfile, "w", encoding='utf-8') as w:
        workfiletemplate.generate(w, {
            'destination': yaml.safe_load(open(asfyaml, encoding='utf-8'))['pelican']['target']
        })

    print(f"Writing converted settings to {settings_path}")
    t = ezt.Template(os.path.join(THIS_DIR, AUTO_SETTINGS_TEMPLATE))
    with open(settings_path, "w", encoding='utf-8') as w:
        t.generate(w, tdata)

def main():
    parser = argparse.ArgumentParser(description="Convert pelicanconf.yaml to pelicanconf.py")
    parser.add_argument('-p', '--project', required=False, help="Owning Project") # ignored,can be deleted
    parser.add_argument('-y', '--yaml', required=True, help="Pelicanconf YAML file")
    args = parser.parse_args()

    pelconf_yaml = args.yaml
    sourcepath = os.path.dirname(pelconf_yaml)
    if sourcepath == '':
        sourcepath = '.' # Needed for find

    if os.path.exists(pelconf_yaml):
        print(f"found {pelconf_yaml}")
        settings_path = os.path.join(sourcepath, AUTO_SETTINGS)
        builtin_plugins = '/tmp/pelican-asf/plugins' # Where the Docker plugins are currently
        generate_settings(pelconf_yaml, settings_path, [builtin_plugins], sourcepath)
    else:
        print(f"Unable to find {pelconf_yaml}")

if __name__ == "__main__":
    main()
