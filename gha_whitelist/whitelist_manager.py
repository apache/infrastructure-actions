#!/usr/bin/python3 -B
import argparse
import requests
import yaml
import json
import sys
import time

github_timewait = 60
url = "https://api.github.com"
FILE = "approved_patterns.yml"

def ghapi_call(args, s):
    if args.fetch:
        r = s.get("%s/%s" % (url, args.uri))
    else:
        data = {
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": args.whitelist,
        }
        r = s.put("%s/%s" % (url, args.uri), data=json.dumps(data))
    return r


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--token", help="Token")
    parser.add_argument("-f", "--fetch", action="store_true")

    args = parser.parse_args()
    setattr(args, "uri", "orgs/asf-transfer/actions/permissions/selected-actions")
    # Don't try to read the file, we're going to overwrite it.
    if not args.fetch:
        setattr(
            args, "whitelist", yaml.safe_load(open(FILE, "r"))
        )
    # Check token stuffs
    if not args.token:
        print("No token provided")
        sys.exit(1)
    s = requests.Session()
    s.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {args.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    results = ghapi_call(args, s)
    if args.fetch:
        with open(FILE, "w+") as f:
            yaml.dump(results.json()['patterns_allowed'], f, default_flow_style=False)
        print("New file written")
    if results.status_code == 204:
        print("Updated.")


if __name__ == "__main__":
    main()
