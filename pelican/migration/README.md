# Migrating from Infrastructure Pelican to GitHub Actions

## Migration instructions
https://cwiki.apache.org/confluence/display/INFRA/Moving+from+Infrastructure-pelican+to+GitHub+Actions

## Template and GHA Workflow file
The workflow and accompanying template in this directory is intended to be used by the generate_settings.py
script. The build-pelican.yml workflow may be used directly by projects wishing to use the pelican workflow.

## Updating the workflow

Before using the workflow file, ensure that the branches are correct. otherwise you *could* commit to a production branch
