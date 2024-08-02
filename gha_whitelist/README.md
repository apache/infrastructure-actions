# GitHub Actions alowed patterns list

approved_patterns.yml is a list of all of the allowed GitHub actions for a given org.

see documentation [here](https://docs.github.com/en/rest/actions/permissions?apiVersion=2022-11-28#get-allowed-actions-and-reusable-workflows-for-an-organization) for token details.

This script is intended to generate a current list of allowed GitHub Actions with the use of `--fetch`

When run without `--fetch` the script will update the GitHub org whitelist with the contents of approved_patterns.yml.

`./whitelist_manager.py -t $TOKEN (--fetch|-f)`
