#!/usr/bin/python3 -B
import argparse
import requests
from requests import exceptions
import json
import sys
import colorama
import os

ORG = "apache"


def getOpts():
    parser = argparse.ArgumentParser(description="GitHub Actions Enabler / Disabler")
    parser.add_argument("-t", "--token", help="github API token", required=True)
    parser.add_argument("-p", "--project", help="Apache Project to modify")
    parser.add_argument(
        "-q", "--quiet", action="store_true", default=False, help="Silence output"
    )
    status_opts = parser.add_mutually_exclusive_group(required=True)
    status_opts.add_argument("-E", "--enable", help="Enable", action="store_true")
    status_opts.add_argument("-D", "--disable", help="Disable", action="store_true")
    status_opts.add_argument("-I", "--info", help="info only", action="store_true")
    args = parser.parse_args()

    return args


class GitHubber:
    def __init__(self, args):
        self.args = args
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {self.args.token}",
            }
        )
        self.url = "https://api.github.com"

    def get(self, action, repo=None):
        uris = {
            "perms": f"{self.url}/repos/{ORG}/{repo}/actions/permissions",
            "all": f"{self.url}/search/repositories?q=org:{ORG} {self.args.project} in:name",
            "repo": f"{self.url}/repos/{ORG}/{repo}",
        }

        try:
            result = self.s.get(f"{uris[action]}")
            if result.status_code in [200, 204]:
                return result.json()
            else:
                result.raise_for_status()

        except ConnectionError as e:
            print(e)
            return False

        except requests.exceptions.HTTPError as e:
            print(e)
            return False

    def put(self, action, repo=None):
        uris = {
            "enable": f"{self.url}/repositories/{repo}/actions/permissions",
            "disable": f"{self.url}/repositories/{repo}/actions/permissions",
        }
        result = self.s.put(f"{uris[action]}")
        data = {}
        if action == "enable":
            data["enabled"] = True
            data["allowed_actions"] = "selected"
            data[
                "selected_actions_url"
            ] = f"https://api.github.com/repositories/{repo}/actions/permissions/selected-actions"
        elif action == "disable":
            data["enabled"] = False
        else:
            return False
        d = json.dumps(data)
        try:
            result = self.s.put(uris[action], data=d)
        except ConnectionError as e:
            print(e)
            return False
        return True

    def update_selection(self, repos):
        # Write to a tempfile and then read in the results.
        # Set this as the new value of repos
        tempfile_name = "/tmp/GHATEMPFILE"
        tf = open(tempfile_name, "w+")
        tf.write("\n".join(repos))
        tf.close()
        os.system(f"editor {tempfile_name}")
        with open(tempfile_name, "r") as f:
            r = [line.strip() for line in f.readlines()]
        return r

    def safety(self, msg=None, goto=None):
        if msg:
            self.pprint(msg)
        s = input("Y/N: ")
        if s.lower() == "y":
            return True
        elif s.lower() == "n":
            return False
        else:
            self.pprint("Please indicate Y(es) or N(o).", "cyan")
            self.safety()

    def pprint(self, message, color=None, terminator="\n"):
        if self.args.quiet:
            return
        """Use colorama to print the messages"""
        if not color:
            color = "reset"
        c = {
            "cyan": colorama.Fore.CYAN,
            "red": colorama.Fore.RED,
            "green": colorama.Fore.GREEN,
            "reset": colorama.Style.RESET_ALL,
        }
        print(f"{c[color]}{message}{c['reset']}", end=terminator)


def main():
    msgs = {
        "info": "be queried for their GitHub Actions status",
        "enable": "have GitHub Actions Enabled",
        "disable": "have GitHub Actions Disabled",
    }

    args = getOpts()
    g = GitHubber(args)
    g.pprint("Starting...", "cyan")
    repos = g.get("all", args.project)
    if repos:
        rnames = [repo["name"] for repo in repos["items"]]
    else:
        sys.exit(1)
    if not args.quiet:
        action = [
            val
            for val in vars(args).keys()
            if val in ["disable", "enable", "info"] and vars(args)[val]
        ]
        q = False
        while not q:
            q = g.safety(
                "The following repositories will %s. Continue?:\n  - %s"
                % (msgs[action[0]], "\n  - ".join(rnames))
            )
            if q:
                continue
            r = g.update_selection(rnames)
            rnames = r
    rdata = [
        (item["name"], item["id"]) for item in repos["items"] if item["name"] in rnames
    ]
    for repo, idnum in rdata:
        if args.disable:
            g.pprint(f"{repo} GitHub Actions -- ", None, "")
            r = g.put("disable", idnum)
            g.pprint("Disabled", "cyan")
        elif args.enable:
            g.pprint(f"{repo} GitHub Actions -- ", None, "")
            r = g.put("enable", idnum)
            g.pprint("Enabled", "cyan")
        elif args.info:
            g.pprint(f"{repo} GitHub Actions...", None, "")
            r = g.get("perms", repo)
            if r:
                if r["enabled"]:
                    g.pprint("Enabled", "green")
                else:
                    g.pprint("Disabled", "red")
            else:
                g.pprint("FAILED", "red")


if __name__ == "__main__":
    main()
