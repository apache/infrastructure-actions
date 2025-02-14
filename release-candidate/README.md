# ASF Infrastructure Release Candidate Action

This is a GitHub Action that can be used to create release candidates. Note
that it is somewhat opinionated on how release candidates are organized. This
is not intended to be used by all projects.

## Prerequisites

* Apache Security Team has approved your project for
  [Automated Release Signing](https://infra.apache.org/release-signing.html#automated-release-signing)
  and INFRA has set secrets for the repository, including a GPG signing key,
  SVN username/password, and nexus username/password.
* The `runs-on` workflow setting should be Linux based (e.g. `ubuntu-latest`)
* The repository must be checked out using `actions/checkout` action prior to
  triggering this action
* The repository must have a `VERSION` file containing the current version of the
  project (e.g. `1.0.0`)
* When triggered from a tag, the tag must follow the pattern `v<VERSION>-*`
  (e.g. `v1.0.0-rc1`)
* When triggered from a tag, the tag must be signed and verified by a key
  listed in `https://downloads.apache.org/<tlp_dir>/KEYS`.

## Setup Operations

Below are the operations this action does to setup the environment for a
release candidate workflow:

* Checkout the project's `dist/dev/` directory and create a directory for
  release artifacts in `https://dist.apache.org/repos/dist/dev/<tlp_dir>/<project_dir>/<version>-rcX`.
  The `artifact_dir` output is set to this directory. Note that `<project_dir>`
  is optional if the artifact directory should be in the root of the
  `<tlp_dir>`
* Delete previous release candidates from `dist/dev/` for the same version
  Useful if the first rc fails the VOTE and more are needed
* Create a zip source artifact using git archive. The artifact is written to
  `src/apache-<project_id>-<version>-src.zip` in the above artifact directory
* Export `SOURCE_DATE_EPOCH` environment variable to match the timestamp of the
  current commit
* Configure global SBT [Simple Build Tool](https://scala-sbt.org) settings to
  enabling publishing signed jars to the ASF nexus staging repository. Workflow
  steps can use `sbt pubilshSigned` without needing any other configuration. If
  publishing is disabled, SBT is configured to publish to a local maven repo on
  the CI system, so `sbt publishSigned` can still be used without actually
  publishing anything.
* TODO: Add configurations for Maven/Gradle/etc to support other build tools or
  staging to non-maven repositories

## Post Operations

If the workflow job does not succeed, none of the following actions are taken.
Files added to `dist/dev/` will not be committed. If the workflow published
files to the ASF staging nexus repository, those files must be manually
dropped.

If the workflow job successfully completes, the following actions are performed
at the end of the workflow:

* Create sha512 checksum files for all artifacts
* Create detached ASCII armored GPG signatures for all artifacts
* Sign all rpm artifacts with the GPG key with rpmsign
* Commit all files added to `dist/dev/` to SVN

Note that committing to SVN is is disabled if any of the following are true:
* The `publish` action setting is not explicitly set to `true`
* The `VERSION` file contains `-SNAPSHOT`
* The workflow is not triggered from the push of a tag
* The repository is not in the `apache` organization

If any of the above are true and publishing is disabled, the artifact directory
is uploaded as a GitHub workflow artifact. It will be retained for one day.
This is useful for testing the workflow using workflow dispatch.

## Inputs
| Input           | Required | Default | Description |
|-----------------|----------|---------|-------------|
| tlp_dir         | yes      |         | Directory of the top level project in dist/dev/ |
| project_name    | yes      |         | Human readable name of the project |
| project_id      | yes      |         | ID of the project, used in source artifact file name |
| project_dir     | no       | ""      | Directory for the project in dev/dist/<tlp_dir>/. Omit if at the root |
| gpg_signing_key | yes      |         | Key used to sign artifacts |
| svn_username    | yes      |         | Username for publishing release artifacts to SVN dev/dist |
| svn_password    | yes      |         | Password for publishing release artifacts to SVN dev/dist |
| nexus_username  | yes      |         | Username for publishing release artifacts to Nexus |
| nexus_password  | yes      |         | Password for publishing release artifacts to Nexus |
| publish         | no       | false   | Enable/disabling publish artifacts. Must be explicitly set to true to enable publishing. Maybe ignored depending on other factors. |

## Outputs

| Output          | Description |
|-----------------|-------------|
| artifact_dir    | Directory where additional release artifacts can be added by the workflow. They are automatically signed, checksumed, and published at the end of the workflow |

## Example Workflow

```yaml
name: Release Candidate

# triggered via release candidate tags or manually via workflow dispatch, note
# that publishing is disabled if not triggered from a tag
on:
  push:
    tags:
      - 'v*-rc*'
  workflow_dispatch:

jobs:

  release-candidate:
    name: Release Candidate ${{ github.ref_name }}
    runs-on: ubuntu-latest

    steps:

      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: ASF Release Candidate
        id: rc
        uses: apache/infrastructure-actions/release-candidate@main
        with:
          tlp_dir: 'daffodil'
          project_name: 'Apache Daffodil'
          project_id: 'daffodil'
          gpg_signing_key: ${{ secrets.GPG_PRIVATE_KEY }}
          svn_username: ${{ secrets.SVN_USERNAME }}
          svn_password: ${{ secrets.SVN_PASSWORD }}
          nexus_username: ${{ secrets.NEXUS_USERNAME }}
          nexus_password: ${{ secrets.NEXUS_PASSWORD }}
          publish: true

      - name: Install Dependencies
        run: |
          sudo apt-get -y install ...
          ...

      - name: Create Binary Artifacts
        run: |
          sbt compile publishSigned ...
          
          ARTIFACT_DIR=${{ steps.rc.outputs.artifact_dir }}
          ARTIFACT_BIN_DIR=$ARTIFACT_DIR/bin

          # copy helper binaries to the artifact bin directory, these will be
          # automatically signed, checksumed, and comitted to dist/dev/
          mkdir -p $ARTIFACT_BIN_DIR
          cp ... $ARTIFACT_BIN_DIR/
```
