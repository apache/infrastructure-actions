#!/usr/bin/env python3
#
# Test script for using GitHub's REST endpoint for rendering
# GFM (on stdin) into HTML (to stdout).
#
# USAGE:
#
#   A Personal Access Token (PAT) is needed. Generate one at
#   https://github.com/settings/tokens?type=beta ... set an
#   expiration and read-only for all public repositories. No
#   fine-grained permissions are needed.
#
#   Save the PAT somewhere safe.
#
#   Put the PAT into the environment variable GITHUB_TOKEN:
#
#   $ read GItHUB_TOKEN
#   <paste>
#
#   Using read will ensure the PAT does not appear in your
#   shell history.
#
#   $ ./render.py < /some/file.md > /some/where.html
#

import os
import sys
import json

import requests

ENDPOINT = 'https://api.github.com/markdown'
API_VERSION = '2022-11-28'


def main():
    token = os.environ['GITHUB_TOKEN']  # Fail if missing
    markdown = sys.stdin.read()
    sys.stdout.write(render(token, markdown))


def render(token, markdown):
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': API_VERSION,
        }
    params = {
        'text': markdown,
        'mode': 'gfm',
        #'context': None,
        }
    r = requests.post(ENDPOINT, headers=headers, json=params)
    return r.text


if __name__ == '__main__':
    main()
