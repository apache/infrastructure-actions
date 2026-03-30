<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->
# Agents Guidelines

## Running tests

Use `uv run` for all commands run during tests. For example:

```bash
uv run pytest
```

## Commit messages

Do not add `Co-Authored-By` headers in commit messages. Instead, use a `Generated-by` trailer
following the guidelines of the ASF:

```
Generated-by: <Agent information>
```

## Pull requests

Always use `--web` when creating PRs (e.g. `gh pr create --web ...`). This opens the PR in the
browser and gives the author a chance to review the title, description, and diff before submitting.
Do not create PRs directly from the CLI without `--web`.
