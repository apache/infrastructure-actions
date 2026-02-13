#!/usr/bin/env bash
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

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
