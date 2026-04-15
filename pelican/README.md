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
# ASF Infrastructure Pelican Action

This Action simplifies managing a project website. More information is available at
[infra.apache.org/asf-pelican.html](https://infra.apache.org/asf-pelican.html).

## Inputs

| Name           | Description                                                                 | Default           |
| -------------- | --------------------------------------------------------------------------- | ----------------- |
| `destination`  | Pelican output branch                                                       | `asf-site`        |
| `publish`      | Publish the site to the destination branch (set `false` to build only)     | `true`            |
| `gfm`          | Use GitHub Flavored Markdown                                                | `true`            |
| `output`       | Pelican generated output directory                                          | `output`          |
| `tempdir`      | Temporary directory name                                                    | `../output.tmp`   |
| `debug`        | Pelican debug mode                                                          | `false`           |
| `version`      | Pelican version to install                                                  | `4.11.0.post0`    |
| `requirements` | Extra Python requirements file to install on top of the action              | _(none)_          |
| `fatal`        | Value for `--fatal` option (`errors` or `warnings`)                         | `errors`          |

## Example workflows

Build and publish a site on every push:

```yaml
jobs:
  build-pelican:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: apache/infrastructure-actions/pelican@93dfe4693bc118397840e7e4ae447e57a3eea7ee # main
        with:
          destination: master
          gfm: 'true'
```

Build only (useful for pull request checks):

```yaml
jobs:
  build-pelican:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: apache/infrastructure-actions/pelican@93dfe4693bc118397840e7e4ae447e57a3eea7ee # main
        with:
          publish: 'false'
```

## Project layout

| Path              | Purpose                                                                      |
| ----------------- | ---------------------------------------------------------------------------- |
| `action.yml`      | Composite GitHub Action entrypoint.                                          |
| `Dockerfile`      | Docker-based runtime used by Apache CI pipelines. See [Docker.md](Docker.md).|
| `pyproject.toml`  | PEP 621 project metadata and dependencies (single source of truth).          |
| `plugin_paths.py` | Helper that injects plugin directories into the Pelican configuration.       |
| `plugins/`        | Bundled ASF Pelican plugins (`asfdata`, `asfgenid`, `gfm`, ...).             |
| `migration/`      | Scripts for migrating legacy infrastructure-pelican sites to this action.    |
| `build-cmark.sh`  | Builder script for `cmark-gfm` used by GFM support.                          |

## Working with the project

The `pelican` directory is a Python project defined by `pyproject.toml`
([PEP 517](https://peps.python.org/pep-0517/),
[PEP 518](https://peps.python.org/pep-0518/),
[PEP 621](https://peps.python.org/pep-0621/),
[PEP 639](https://peps.python.org/pep-0639/)).
Dependencies, metadata and lint configuration all live there — there is no
separate `requirements.txt` to keep in sync.

### Local development

This project uses [`uv`](https://docs.astral.sh/uv/) as its package manager.
For day-to-day development (editing plugins, running tests, linting) you
have three options — pick whichever matches your workflow.

**Tool version requirements.** Both `uv` and `hatch` need PEP 735
dependency-group support:

| Tool  | Minimum version | Why                                                                   |
| ----- | --------------- | --------------------------------------------------------------------- |
| uv    | `0.5.0`         | `[tool.uv].required-version` enforces this; PEP 735 landed in 0.4.27. |
| hatch | `1.16.0`        | First release with `dependency-groups` support in env configs.        |

The uv floor is machine-enforced via `[tool.uv].required-version` in
`pyproject.toml`; the hatch floor is currently only documented here because
Hatch has no equivalent pyproject.toml field.

**Option 1 — `uv sync` (recommended).** Let uv manage the virtual environment
as a project. This creates (or updates) `.venv/`, resolves every runtime dep
and the PEP 735 `dev` group in one pass, and writes a `uv.lock` for
reproducible installs:

```shell
uv sync
```

`uv sync` includes the `dev` group by default. Any `uv run <cmd>` invocation
from the project root will automatically use this environment, so you can
skip manual activation:

```shell
uv run ruff check .
uv run pytest
uv run pelican --version
```

**Option 2 — manual venv.** If you prefer to manage the environment yourself
(e.g. you already have one, or you want to combine it with other projects),
create it, install the project in editable mode, and add the `dev` group:

```shell
uv venv
source .venv/bin/activate
uv pip install -e .
uv pip install --group dev
```

**Option 3 — [Hatch](https://hatch.pypa.io/).** The repo ships a
`[tool.hatch.envs.default]` section that pulls the same PEP 735 `dev` group
via `dependency-groups = ["dev"]`, with `uv` wired in as the installer:

```shell
hatch env create
hatch shell
```

Common tasks are exposed as named scripts, so you don't need to activate a
shell if you just want to run one command:

```shell
hatch run lint   # ruff check .
hatch run fmt    # ruff format .
hatch run test   # pytest
```

All three options resolve the same `dev` dependency group, so you get
Pelican alongside the project's runtime dependencies and the lint/test
tooling no matter which workflow you choose.

Pelican is intentionally **not** listed in `[project].dependencies`. The
composite action installs `pelican[markdown]` itself via `uv tool install`,
with the version controlled by the action's `version` input. Keeping Pelican
in the `dev` group means there is a single authoritative version source for
the composite action's runtime and a separate one for local development and
the Docker image, with no risk of the two fighting during dependency
resolution.

To reproduce the exact install the **composite action** performs (Pelican
inside an isolated uv tool venv, with this project's runtime dependencies
injected), run:

```shell
uv tool install 'pelican[markdown]' --with .
pelican --version
```

To reproduce the **Docker image's** install instead (project + locked dev
group, including Pelican, into `.venv/`), run:

```shell
uv sync --frozen
.venv/bin/pelican --version
```

### Linting and formatting

Lint rules and formatter config live under `[tool.ruff]` in `pyproject.toml`:

```shell
ruff check .
ruff format .
```

### Updating dependencies

`pyproject.toml` is the source of truth for declared dependency ranges.
`uv.lock` (committed alongside it) pins every transitive package to a
specific version + hash so the Docker image and `uv sync` workflows are
fully reproducible. The composite action (`action.yml`) deliberately does
**not** consume the lockfile — it installs `pelican[markdown]==<version>`
fresh on every run so the action's `version` input stays authoritative.

**Adding or changing a dependency.** Edit the `dependencies` list (or the
`[dependency-groups].dev` list) in `pyproject.toml`, then refresh the
lockfile:

```shell
uv lock           # re-resolves only what your edit changed
uv sync           # apply the new lockfile to your local .venv
```

Commit `pyproject.toml` and `uv.lock` together. Any drift between the two
will cause the Docker build to fail (it runs `uv sync --frozen`), so the
two files must always move as a pair.

**Bumping pinned versions without changing constraints.** To pull in newer
patch/minor releases that already satisfy the existing constraints in
`pyproject.toml`, upgrade the lockfile in place:

```shell
uv lock --upgrade               # bump every package to its latest allowed
uv lock --upgrade-package foo   # bump just one package
uv sync                         # apply the refreshed lockfile
```

**Automated updates via Dependabot.** The repo's
[`.github/dependabot.yml`](../.github/dependabot.yml) registers
`/pelican/` under the `uv` package ecosystem, so Dependabot reads
`pelican/uv.lock` and opens PRs that bump pinned versions on a **weekly**
schedule with a **4-day cooldown** (`cooldown.default-days: 4`) — newly
released versions have to age four days before Dependabot will propose
them, which avoids picking up brand-new releases that get yanked shortly
after publication. Each Dependabot PR updates `uv.lock` only; if a bump
needs a wider constraint in `pyproject.toml`, do that change manually
following the "Adding or changing a dependency" flow above.

### Building the Docker image

See [Docker.md](Docker.md) for the full workflow. The short version:

```shell
docker build -t pelican-asf .
docker run --rm -it -p 8000:8000 -v "$PWD":/site pelican-asf
```

### How the action runs

There are two execution paths and they install Pelican differently. In both
cases the `pelican` CLI ends up on `PATH` alongside this project's runtime
dependencies, and `plugin_paths` is importable from the same Python — so any
dependency that `pelicanconf.py` needs at runtime lives in the same place as
Pelican itself.

1. **Composite action** (`action.yml`) — runs on a GitHub-hosted runner.
   It provisions a hermetic Python via [`actions/setup-python`][setup-python],
   bootstraps `uv` into that interpreter, runs
   `uv tool install 'pelican[markdown]==<version>' --with <action-path>`
   (optionally layering `--with-requirements <user-file>` when the
   `requirements` input is set), optionally builds `cmark-gfm` if `gfm: true`,
   and invokes `pelican content ...` directly. This path **does not** use
   `uv.lock` — Pelican's version comes from the `version` input and the
   project's runtime deps re-resolve on each run, so users can pin Pelican
   from their workflow without editing this repo. The tool venv's Python is
   published to later steps via `$PELICAN_TOOL_PY` so `plugin_paths` runs
   inside the same environment as Pelican.

[setup-python]: https://github.com/actions/setup-python
2. **Docker image** (`Dockerfile`) — a long-lived image used by Apache CI.
   It bootstraps `uv`, copies `pyproject.toml` + `uv.lock` into the image,
   then runs [`uv sync --frozen`](https://docs.astral.sh/uv/reference/cli/#uv-sync)
   to install the project together with the locked `dev` group (which
   includes Pelican) into `/opt/pelican-asf/.venv`. `--frozen` makes the
   build fail if `uv.lock` is out of sync with `pyproject.toml`, so the
   image is always a faithful materialisation of the committed lockfile.
   The image bakes in the plugins and exposes a `pelicanasf` wrapper (in
   `/usr/local/bin`) that calls `pelican` and uses the venv's Python to run
   `python -m plugin_paths`.

The composite action path is intentionally re-resolved on every run so the
`version` input stays authoritative. The Docker image is intentionally
locked so production rebuilds are byte-reproducible. The two paths therefore
have different update workflows (see [Updating dependencies](#updating-dependencies)).

# Pelican Migration Scripts

The generate_settings.py script is designed to facilitate migrating away from the
infra built pelican site generator via .asf.yaml to GitHub Actions.

The script itself takes one argument: the path to the pelicanconf.yaml file.
Additionally, the script will look for an .asf.yaml file in the same directory.
If an .asf.yaml file is found, the script will generate a GitHub Action workflow file.

## generate_settings.py usage
* [Infrastructure-pelican to GitHub Actions documentation](https://cwiki.apache.org/confluence/display/INFRA/Moving+from+Infrastructure-pelican+to+GitHub+Actions)
