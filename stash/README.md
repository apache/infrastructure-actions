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

`Stash` provides a solution for managing large build caches in your workflows, that doesn't require any secrets and can therefore be used in fork PRs.
It's designed as an alternative to `actions/cache` which struggles with big build caches such as `.ccache` directories due to the repository wide [size limit](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows#usage-limits-and-eviction-policy) of 10GB and the fact that caches are [immutable](https://github.com/actions/toolkit/issues/505).
With workflows running multiple configurations across PRs and merge commits this limit is quickly reached, leading to cache evictions, causing CI times to increase.

This action is split into two distinct operations:
- `infrastructure-actions/stash/restore` for fetching a previously stored stash
- `infrastructure-actions/stash/save` for storing a new stash after a build has been completed.

## Features

- Each stash is uploaded as a workflow artifact. In contrasts to `actions/cache` there is no repository wide size limit for artifacts.
    - There is no cache eviction, stashes will expire after 5 days by default.
- Artifact storage is free for public repositories and much cheaper than CI minutes (~ 1 Cent/1GB/day) for private repositories.
- No secrets required, stash can be used in fork PRs.
- Follows the same search scope as `actions/cache`: will look for the cache in the current workflow, current branch and finally the base branch of a PR.
This prevents untrusted user caches (e.g. from fork PR CI runs) from being used on the default branch (where actions have elevated permissions by default) or other repo or PR branches.

## Usage

> [!IMPORTANT]
> You have to explicitly save your stash by using `infrastructure-actions/stash/save` action,
> it will not be saved automatically by using `infrastructure-actions/stash/restore`.

To restore a stash before your build process, use the `infrastructure-actions/stash/restore` action in your workflow:


```yaml
steps:
- uses: actions/checkout@v2
- uses: apache/infrastructure-actions/stash/restore@main
  with:
    key: 'cache-key'
    path: 'path/to/cache'
```

After your build completes, save the stash using the `infrastructure-actions/stash/save` action:

```yaml
steps:
- uses: apache/infrastructure-actions/stash/save@main
  with:
    key: 'cache-key'
    path: 'path/to/cache'
```
Stashes will expire after 5 days by default.
You can set this from 1-90 days with the `retention-days` input.
Using the `save` action again in the same workflow run will overwrite the existing cache with the same key.
This does apply to each invocation in a matrix job as well!
If you want to keep the old cache, you can use a different key or set `overwrite` to `false`.

### Inputs and Outputs

Each action (restore and save) has specific inputs tailored to its functionality,
they are specifically modeled after `actions/cache` and `actions/upload-artifact` to provide a drop in replacement.
Please refer to the action metadata (`action.yml`) for a comprehensive list of inputs, including descriptions and default values.

The `restore` action has an optional "overwrite" input which defaults to `false` - when set to "true", it
will delete the existing contents of the target directory before restoring the stash.

Additionally, the `restore` action has an output `stash-hit` which is set to `true` if the cache was restored successfully,
`false` if no cache was restored and '' if the action failed (an error will be thrown unless `continue-on-error` is set).
A technical limitation of composite actions like `Stash` is that all outputs are **strings**.
Therefore, an explicit comparison has to be used when using the output:
`if: ${{ steps.restore-stash.outputs.stash-hit == 'true' }}`
