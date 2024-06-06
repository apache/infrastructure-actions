import argparse
import os
import pathlib
import fnmatch

import yaml
import ezt


SCRATCH_DIR = "/tmp"
THIS_DIR = os.path.abspath(os.path.dirname(__file__))

# Automatic settings filenames
AUTO_SETTINGS_YAML = "pelicanconf.yaml"
AUTO_SETTINGS_TEMPLATE = "pelican.auto.ezt"
AUTO_SETTINGS = "pelicanconf.py"
WORKFLOW_TEMPLATE = "build-pelican.yml.ezt"
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
    print(pathlib.Path(os.path.abspath(source_yaml)).parent.absolute().joinpath(".asf.yaml"))
    asfyaml = pathlib.Path(os.path.abspath(source_yaml)).parent.absolute().joinpath(".asf.yaml")
    if os.path.isfile(asfyaml):
        print(".asf.yaml detected, reading...")
        adata = yaml.safe_load(open(asfyaml))

    print(f"Reading {source_yaml} in {sourcepath}")
    ydata = yaml.safe_load(open(source_yaml))

    print(f"Converting to {settings_path}...")
    tdata = ydata["site"]  # Easy to copy these simple values.
    tdata.update(
        {
            "year": 'datetime.date.today().year', # generate this at run-time
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
            tdata["p_paths"] = []
            for p in ydata["plugins"]["paths"]:
                tdata["p_paths"].append(os.path.join(p))
        else:
            tdata["p_paths"] = ["plugins"]

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

    if not os.path.isdir(".github/workflows"):
        if not os.path.isdir(".github"):
            os.mkdir(".github")
        os.mkdir("./.github/workflows")
    print("Writing GitHub Actions file")
    g = ezt.Template(os.path.join(THIS_DIR, WORKFLOW_TEMPLATE), 0) # don't compress whitespace
    g.generate(open(".github/workflows/build-pelican.yml", "w+"), adata['pelican'])

    print(f"Writing converted settings to {settings_path}")
    t = ezt.Template(os.path.join(THIS_DIR, AUTO_SETTINGS_TEMPLATE))
    t.generate(open(settings_path, "w+"), tdata)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert pelicanconf.yaml to pelicanconf.py")
    parser.add_argument('-y', '--yaml', required=True, help="Pelicanconf YAML file")
    args = parser.parse_args()

    pelconf_yaml = args.yaml
    sourcepath = os.path.dirname(os.path.realpath(pelconf_yaml))
    tool_dir = THIS_DIR

    if os.path.exists(pelconf_yaml):
        print(f"found {pelconf_yaml}")
        settings_path = os.path.join(sourcepath, AUTO_SETTINGS)
        builtin_plugins = os.path.join(tool_dir, os.pardir, "plugins")
        generate_settings(pelconf_yaml, settings_path, [builtin_plugins], sourcepath)
