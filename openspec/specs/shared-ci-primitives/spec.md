# Shared CI Primitives Capability Specification

## Requirements

### Requirement: Shared Node/pnpm setup is configurable

The `actions/setup` composite action SHALL provide common Git safe-directory setup, optional Node setup, optional pnpm setup, and optional frozen-lockfile dependency installation.

#### Scenario: Standard hosted-runner setup is requested

- **WHEN** callers use the default setup behavior
- **THEN** Node 24 is configured unless another version is supplied
- **AND** pnpm setup is enabled
- **AND** dependency installation runs with `pnpm install --frozen-lockfile`
- **AND** the pnpm store is configured at `/cache/pnpm-store`

#### Scenario: A runner already provides Node and pnpm

- **WHEN** `setup-node` and/or `setup-pnpm` are set to `false`
- **THEN** the corresponding installer step is skipped
- **AND** dependency installation may still run when `install` is `true`

#### Scenario: Dependency installation is not needed

- **WHEN** `install` is `false`
- **THEN** the action does not execute `pnpm install`

### Requirement: Shared setup marks the workspace as a safe Git directory

The setup action SHALL configure the current `GITHUB_WORKSPACE` as a global Git safe directory before optional runtime/package-manager setup.

#### Scenario: Setup runs in a containerized or self-hosted environment

- **WHEN** Git ownership differs from the executing user
- **THEN** the workspace has already been added to Git's safe-directory configuration by the shared setup action

### Requirement: Multiline environment input is exported line by line

The `actions/parse-env` action SHALL copy non-empty, non-comment input lines into `GITHUB_ENV`.

#### Scenario: Environment input contains values and comments

- **WHEN** the input contains `KEY=VALUE` lines, blank lines, and lines beginning with `#`
- **THEN** blank lines and comment lines are skipped
- **AND** every other line is appended to `GITHUB_ENV` without additional schema validation

### Requirement: Mailpit setup exposes local test endpoints

The `actions/mailpit` action SHALL start a local Mailpit process for CI tests and export the connection values expected by consumers.

#### Scenario: A test workflow starts Mailpit

- **WHEN** the composite action completes its startup steps
- **THEN** `SMTP_HOST` is exported as `127.0.0.1`
- **AND** `MAILPIT_API_URL` is exported as `http://127.0.0.1:8025`

### Requirement: Changed-path detection compares caller-selected commits

The `_detect-changes.yml` reusable workflow SHALL diff the caller-provided `base-sha` and `head-sha` and classify changed paths into independent database, application, and configuration categories.

#### Scenario: Changed files match caller patterns

- **WHEN** added, modified, or deleted paths in `base-sha..head-sha` match the newline-separated patterns for a category
- **THEN** that category's output is the string `true`
- **AND** categories are evaluated independently

#### Scenario: No path matches a category

- **WHEN** no changed path matches that category's supplied patterns
- **THEN** the corresponding output is the string `false`

### Requirement: Path detection operates on full Git history

The changed-path workflow SHALL check out the repository with full history before diffing arbitrary caller-provided SHAs.

#### Scenario: Base and head are not available in a shallow checkout

- **WHEN** the detect job starts
- **THEN** checkout uses `fetch-depth: 0`
- **AND** `git diff --name-only` can evaluate the requested commit range
