#!/usr/bin/env python3
import re
import sys
import yaml
import argparse
import requests
import logging
import json
import sys
import jinja2
import time

github_timewait = 60
url = "https://api.github.com"
MINIMUMS = {
    "watchers": 5,
    "in_market": True,
    }

def getOpts():
    parser = argparse.ArgumentParser(description="github api caller")
    parser.add_argument("-t", "--token", help="github API token")
    parser.add_argument("-d", "--dump", action='store_true', help="dump response object to file")
    parser.add_argument("-v", action="count", default=0, help="Set log verbosity (1-5)")
    parser.add_argument("-a", "--action", required=True, help="GitHub Action to Verify")
    
    output = parser.add_mutually_exclusive_group()
    output.add_argument("-J", "--j2", help="jinja2 formatting string")
    output.add_argument("-o", "--outfile", help="dump raw data to file")
    args = parser.parse_args()

    return args


class Log:
    def __init__(self, config):
        self.config = config
        self.log = logging.getLogger(__name__)
        self.verbosity = {
            0: logging.INFO,
            1: logging.CRITICAL,
            2: logging.ERROR,
            3: logging.WARNING,
            4: logging.INFO,
            5: logging.DEBUG,
        }

        self.stdout_fmt = logging.Formatter(
            "{asctime} [{levelname}] {funcName}: {message}", style="{"
        )

        if self.config["logfile"] == "stdout":
            self.to_stdout = logging.StreamHandler(sys.stdout)
            self.to_stdout.setLevel(self.verbosity[self.config["verbosity"]])
            self.to_stdout.setFormatter(self.stdout_fmt)
            self.log.setLevel(self.verbosity[self.config["verbosity"]])
            self.log.addHandler(self.to_stdout)
        else:
            self.log.setLevel(self.verbosity[self.config["verbosity"]])
            logging.basicConfig(
                format="%(asctime)s [%(levelname)s] %(funcName)s: %(message)s",
                filename=self.config["logfile"],
            )

class GitHubber:
    def __init__(self, args):
        self.logger = Log({"logfile": "stdout", "verbosity": args.v})
        self.logger.log.debug("Starting up...")
        self.token = args.token
        self.s = requests.Session()
        self.logger.log.debug("Setting Headers...")
        self.s.headers.update({"Accept": "application/vnd.github+json"})
        self.s.headers.update({"Authorization": "Bearer %s" % self.token})
        self.s.headers.update({"X-GitHub-Api-Version": "2022-11-28"})
        self.ghurl = "https://api.github.com"
        self.mpurl = "https://github.com/marketplace"

    def queryGH(self, action):
        u = f"{url}/repos/{action}"
        r = self.s.get(f"{self.ghurl}/repos/{action}")
        res = r.json()

        m_url = f"{self.mpurl}/actions/{action.split('/')[-1]}"
        print(m_url)
        m = self.s.get(m_url)
        print(m.json())

        return(res)

def main():
    args = getOpts()
    gh = GitHubber(args)
    gh.s.headers.update({"content-type": "text"})
    
    results = gh.queryGH(args.action)

    if args.outfile:
        json.dump(results, open(args.outfile, "w+"))
    else:
        if args.j2:
            t = jinja2.Template(args.j2)
            print(t.render(data=results))
        else:
            print(json.dumps(results, indent=4))

if __name__ == "__main__":
    main()
