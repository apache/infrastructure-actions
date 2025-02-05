# ASF Infrastructure Pelican Action

**Note** This Action simplifies managing a project website. More information is available at <a href="https://infra.apache.org/asf-pelican.html" targeet="_blank">infra.apache.org/asf-pelican.html</a>.

## Inputs
* destination 	Pelican Output branch (optional) 	 	default: asf-site
* publish 	Publish to destination branch (optional) 	default: true
* gfm 	 	Uses GitHub Flavored Markdown (optional) 	default: true
* output 	 	Pelican generated output directory (optional) 	default: output
* tempdir 	Temporary Directory name (optional) 	 	default: ../output.tmp
* debug 	 	Pelican Debug mode (optional) 	 		default: false
* version 	Pelican Version (default 4.5.4) (optional) 	default: 4.5.4
* requirements	Python Requirements file (optional) 	default: None
* fatal  Value for --fatal option [errors|warnings] - sets exit code to error (default: errors)

## Example Workflow Usage:

```
...
jobs:
  build-pelican:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
      - uses: apache/infrastructure-actions/pelican@main
        with:
          destination: master
          gfm: 'true'
```

Example workflow for only building the site, not publishing. Useful for PR tests:

```
...
jobs:
  build-pelican:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
      - uses: apache/infrastructure-actions/pelican@main
        with:
          publish: 'false'
```


# Pelican Migration Scripts

The generate_settings.py script is designed to facilitate migrating away from the
infra built pelican site generator via .asf.yaml to GitHub Actions.

The script itself takes one argument: the path to the pelicanconf.yaml file.
Additionally, the script will look for an .asf.yaml file in the same directory.
If an .asf.yaml file is found, the script will generate a GitHub Action workflow file.

## generate_settings.py usage
* [Infrastructure-pelican to GitHub Actions documentation](https://cwiki.apache.org/confluence/display/INFRA/Moving+from+Infrastructure-pelican+to+GitHub+Actions)
