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
