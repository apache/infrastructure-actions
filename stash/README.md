<!--
# Copyright (c) The stash contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
-->

# Stash GitHub Action

`Stash` provides a solution for managing large build caches in your workflows, that doesn't require any secrets and can be used in fork PRs. It's designed as an alternative to `actions/cache` which struggles with big build caches such as `.ccache` directories. This action is split into two distinct operations: `assign/user/restore` for fetching a previously stored stash, and `assignUser/stash/save` for storing a new stash after a build has been completed.

## Features

- No repository wide size limit of 10GB each stash is uploaded as a workflow artifact.
    - This means there will be no cache evicition leading to cache misses and increased build times. Stashes will expire after 5 days by default.
- Artifact storage is free for public repositories and much cheaper than CI minutes (~ 1 Cent/1GB/day) for private repositories.
- No secrets required, stash can be used in fork PRs.
- Follows the same search scope as `actions/cache`: will look for the cache in the current workflow, current branch and finally the base branch of a PR.

## Usage

> [!IMPORTANT]
> You have to explicitly save your stash by using `assignUser/stash/save` action, it will not be saved automatically by using `assignUser/stash/restore`.

To restore a stash before your build process, use the `assignUser/stash/restore` action in your workflow:


```yaml
steps:
- uses: actions/checkout@v2
- uses: assignUser/stash/restore@v1
  with:
    key: 'cache-key'
    path: 'path/to/cache'
```

After your build completes, save the stash using the `assignUser/stash/save` action:

```yaml
steps:
- uses: assignUser/stash/save@v1
  with:
    key: 'cache-key'
    path: 'path/to/cache'
```
Stashes will expire after 5 days by default. You can set this from 1-90 days with the `retention-days` input. Using the `save` action again in the same workflow run will overwrite the existing cache with the same key. If you want to keep the old cache, you can use a different key or set `overwrite` to `false`.

### Inputs and Outputs

Each action (restore and save) has specific inputs tailored to its functionality, they are specifically modeled after `actions/cache` and `actions/upload-artifact` to provide a drop in replacement. Please refer to the action metadata (`action.yml`) for a comprehensive list of inputs, including descriptions and default values.

Additionally the `restore` action has an output `stash-hit` which is set to `true` (as a **string** so use `if: ${{ steps.restore-stash.outputs.stash-hit == 'true' }}`!) if the cache was restored successfully, `false` if no cache was restored and '' if the action failed (an error will be thrown unless `continue-on-error` is set).
