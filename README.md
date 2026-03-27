# ASF GitHub Actions Repository

This repository hosts GitHub Actions developed by the ASF community and approved for any ASF top level project to use. It also manages the organization wide allow list of GitHub Actions via 'Configuration as Code'.

- [Submitting an Action](#submitting-an-action)
- [Available GitHub Actions](#available-github-actions)
- [Organization-wide GitHub Actions Allow List](#management-of-organization-wide-github-actions-allow-list)
  - [Adding a New Action](#adding-a-new-action-to-the-allow-list)
  - [Reviewing](#reviewing)
  - [Adding a New Version](#adding-a-new-version-to-the-allow-list)
    - [Dependabot Cooldown Period](#dependabot-cooldown-period)
  - [Manual Version Addition](#manual-addition-of-specific-versions)
  - [Removing a Version](#removing-a-version-manually)

## Submitting an Action

To contribute a GitHub Action to this repository:

1. **Fork** this repository
2. **Add your action code**:
   - Create a subdirectory for your proposed GHA at the root level (e.g., `/MyNewAction`)
   - Add all required files for your action in this subdirectory
   - Include a comprehensive README.md that explains:
     - What the action does
     - Required inputs and available outputs
     - Example usage configurations
     - Any special considerations or limitations
3. **Create a pull request** to merge your branch into the main branch

## Reviewing

The Infrastructure team will review each proposed Action based on:
- Overall usefulness to the ASF community
- Maintenance complexity
- Security considerations
- Code quality

Once approved, the Infrastructure team will merge the pull request and add the new Action to the list of available Actions for all ASF projects.

We highly appreciate contributed reviews, especially from people
associated with the projects that (would like to) use a particular
action, even if they're not committers on _this_ project: you're
especially qualified to judge and vouch for the safety and
correctness of the action.

## Available GitHub Actions

  - [ASF Infrastructure Pelican Action](/pelican/README.md): Generate and publish project websites with GitHub Actions
  - [Stash Action](/stash/README.md): Manage large build caches

## Management of Organization-wide GitHub Actions Allow List

As stated in the [ASF GitHub Actions Policy](https://infra.apache.org/github-actions-policy.html), GitHub Actions from external sources are blocked by default in all `apache/*` repositories. Only actions from the following namespaces are automatically allowed:
- `apache/*`
- `github/*`
- `actions/*`

All other actions must be explicitly added to the allow list after undergoing a security review. This review process applies to both new actions and new versions of previously approved actions (though reviews for new versions are typically expedited).

```mermaid
graph TD;
    manual["manual PRs"]--new entries-->actions.yml
    dependabot--updates (after review)-->dummy[".github/workflows/dummy.yml"];
    dummy--updates-->actions.yml
    actions.yml--new entries-->dummy
    actions.yml--generates-->approved_patterns.yml
```

### Adding a New Action to the Allow List

To request addition of an action to the allow list:

1. **Fork** this repository
2. **Add** an entry to `actions.yml` using the following format:

```yaml
repo/owner:
  '<exact-commit-sha>':
    tag: vX.Y.Z
```

3. **Create a PR** against the `main` branch
4. **Include in your PR description**:
   - Why this action is needed for your project
   - Any alternatives you've considered
   - Any security concerns you've identified

5. **Wait for review** by the infrastructure team

> [!NOTE]
> Always pin actions to exact commit SHAs, never use tags or branch references.

The infrastructure team will review your request and either approve, request changes, or provide feedback on alternatives.

### Adding a new _version_ to the allow list

In most cases, new versions are automatically added through Dependabot:
- Dependabot opens PRs to update actions to the newest releases
- The previously approved version will be marked to expire in 3 months
- This grace period gives projects sufficient time to update their workflows

Projects are encouraged to help review updates to actions they use. Please have a look at the diff and mention in your approval what you have checked and why you think the action is safe.

#### Verifying Compiled JavaScript

Many GitHub Actions ship pre-compiled JavaScript in their `dist/` directory. To verify that the published compiled JS matches a clean rebuild from source, use the verification script:

```bash
uv run utils/verify-action-build.py org/repo@commit_hash
```

For example:

```bash
uv run utils/verify-action-build.py dorny/test-reporter@dc3a92680fcc15842eef52e8c4606ea7ce6bd3f3
```

The script will:
1. Clone the action at the specified commit inside an isolated Docker container
2. Save the original `dist/` files as published in the repository
3. Rebuild the action from source (`npm ci && npm run build`)
4. Reformat both versions of the JavaScript for readable comparison
5. Show a colored diff of any differences

A clean result confirms that the compiled JS was built from the declared source. Any differences will be flagged for manual inspection.

#### Batch-Reviewing Dependabot PRs

To review all open dependabot PRs at once, run:

```bash
uv run utils/verify-action-build.py --check-dependabot-prs
```

This will:
1. List all open PRs from dependabot
2. For each PR, extract the action reference from the diff
3. Run the full build verification (rebuild in Docker, compare compiled JS)
4. Show source changes between the previously approved version and the new one
5. If verification passes, ask whether to approve and merge the PR
6. On merge, add a review comment documenting what was verified

> [!NOTE]
> **Prerequisites:** `docker`, `uv`, and `gh` (GitHub CLI, authenticated via `gh auth login`).
> The build runs in a `node:20-slim` container so no local Node.js installation is needed.

#### Dependabot Cooldown Period

This repository uses a [Dependabot cooldown period](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file#cooldown) of 7 days. After a Dependabot PR is merged or closed, Dependabot will wait 7 days before opening the next PR for the same ecosystem. This helps keep the volume of update PRs manageable and gives reviewers time to catch up.

> [!TIP]
> We recommend that ASF projects configure a similar cooldown in their own `dependabot.yml` to avoid being overwhelmed by update PRs and to catch up with approved actions here:
> ```yaml
> updates:
>   - package-ecosystem: "github-actions"
>     directory: "/"
>     schedule:
>       interval: "weekly"
>     cooldown:
>       default-days: 7
> ```
> Adjust the `default-days` value to match your project's review capacity.

### Manual Addition of Specific Versions

If you need to add a specific version of an already approved action (especially an older one):

1. **Fork** this repository
2. **Add** a new version entry to an existing action in `actions.yml` with the following format:

```yaml
existing/action:
  '<exact-commit-sha>':
    expires_at: 2025-01-01
    keep: true
    tag: vX.Y.Z
```

3. **Create a PR** against the `main` branch
4. **Include in your PR description**:
   - Specific reason why this version is required
   - Any blockers preventing upgrade to newer versions
   - Risk assessment for using an older version
   - Expected timeline for migration to newer versions (if applicable)

> [!WARNING]
> Older versions may contain security vulnerabilities or performance issues. Always evaluate if using the latest version is possible before requesting older versions.

### Removing a version manually

> [!IMPORTANT]
> If a version or entire action needs to be removed immediately due to a security vulnerability:

1. **Fork** this repository
2. **Remove** the relevant entry from `actions.yml`
3. **Create a PR** against the `main` branch
4. **Mark it as urgent** in the PR title (e.g., "URGENT: Remove vulnerable action X")
5. **Include in your PR description**:
   - The reason for removal
   - Any CVE or security advisory ID if applicable
   - Impact on projects currently using the action
   - Recommended alternatives if available

The infrastructure team will prioritize these removal requests and may take additional steps to notify affected projects if necessary.

For 'regular' removals (not security responses), you can use `./utils/action-usage.sh someorg/theaction` to see if/how an action is still used anywhere in the ASF, and create a 'regular' PR removing it from `actions.yml` (or adding an expiration date) when it is no longer used.
