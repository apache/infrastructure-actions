# ASF Infrastructure Pelican Action

## Inputs
* destination 	Pelican Output branch (required) 	 	default: asf-site
* gfm 	 	Uses GitHub Flavored Markdown (optional) 	default: true
* output 	 	Pelican generated output directory (optional) 	default: output
* tempdir 	Temporary Directory name (optional) 	 	default: ../output
* debug 	 	Pelican Debug mode (optional) 	 		default: false
* version 	Pelican Version (default 4.5.4) (optional) 	default: 4.5.4

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

# Pelican Migration Scripts

The generate_settings.py script is designed to facilitate migrating away from the
infra built pelican site generator via .asf.yaml to GitHub Actions.

The script itself takes one argument: the path to the pelicanconf.yaml file.
Additionally, the script will look for an .asf.yaml file in the same directory.
If an .asf.yaml file is found, the script will generate a GitHub Action workflow file.

## generate_settings.py usage
* [Infrastructure-pelican to GitHub Actions documentation](https://cwiki.apache.org/confluence/display/INFRA/Moving+from+Infrastructure-pelican+to+GitHub+Actions)
