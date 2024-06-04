# ASF Infrastructure Pelican Action

## Inputs
destination 	Pelican Output branch (required) 	 	default: asf-site
gfm 	 	Uses GitHub Flavored Markdown (optional) 	default: false
output 	 	Pelican generated output directory (optional) 	default: output
tempdir 	Temporary Directory name (optional) 	 	default: .output.tmp
debug 	 	Pelican Debug mode (optional) 	 		default: false
version 	Pelican Version (default 4.5.4) (optional) 	default: 4.5.4

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
