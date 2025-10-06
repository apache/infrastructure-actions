#!/usr/bin/env bash

if [ "$#" -ne 1 ]; then
    echo "Usage:"
    echo
    echo "  ./utils/usage.sh <action>"
    echo
    echo "e.g.:"
    echo
    echo "  ./utils/usage.sh sbt/setup-sbt"
    echo
    exit -1
fi

echo "Usage of $1:"
echo

echo Repositories:
gh search code --owner apache --language yaml --json repository --jq '.[] | .repository.nameWithOwner' $1 | sort | uniq | sort | grep -v infrastructure-actions
echo

if [ "$(which jq 2>/dev/null)" == "" ]; then
  echo "To generate GitHub search links to find the occurences, install 'jq'."
  exit -2
fi

echo Search links:
URLENCODED=$(echo -n "$1" | jq -sRr @uri)
gh search code --owner apache --language yaml --json repository --jq '.[] | .repository.nameWithOwner' $1 | sort | uniq | sort | grep -v infrastructure-actions | sed -e "s/apache\/\(.*\)/https:\/\/github.com\/search?q=repo%3Aapache%2f\1%20$URLENCODED\&type=code/"
