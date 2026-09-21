# CodeGraph Index CI Capability Specification

## Requirements

### Requirement: Shared CodeGraph CI is owned by this repository and opted into by thin callers

`_codegraph.yml` SHALL be a `workflow_call` workflow that contains all CodeGraph install, cache, init/sync, and verification logic. Consumers SHALL opt in only with a thin caller workflow that references `firstsun-dev/.github/.github/workflows/_codegraph.yml@v1`.

#### Scenario: A consumer enables CodeGraph CI

- **WHEN** a consumer adds a caller workflow triggered by pushes to its default branch (and optionally `workflow_dispatch`)
- **THEN** the caller contains no CodeGraph version, install, cache, or init/sync logic
- **AND** the only optional input is `runner` (default `ubuntu-latest`), matching the other reusable workflows

#### Scenario: The workflow runs on a non-default ref

- **WHEN** the caller is dispatched from a ref other than the repository default branch
- **THEN** the job is skipped and no index is restored or saved

### Requirement: CodeGraph is pinned centrally

The workflow SHALL install `@colbymchenry/codegraph` at an exact version defined once in the workflow-level `CODEGRAPH_VERSION` env, SHALL verify the installed version matches, and SHALL NOT use `latest`, `main`, an install script, or `codegraph install`.

#### Scenario: The pinned version is bumped

- **WHEN** `CODEGRAPH_VERSION` changes
- **THEN** the cache key and restore prefix change with it
- **AND** the first run starts from a cold cache and performs a fresh initialization

### Requirement: The workflow is minimal, secret-free, and telemetry-free

The workflow SHALL declare `permissions: contents: read`, SHALL require no secrets, and SHALL set `CODEGRAPH_TELEMETRY=0`, `DO_NOT_TRACK=1`, and `CI=true` at job level.

#### Scenario: Any run

- **WHEN** the workflow executes
- **THEN** no source code or graph data leaves the runner other than through the GitHub Actions cache

### Requirement: The index is cached per version, platform, repository, branch, and commit

The `.codegraph/` directory SHALL be cached with key `codegraph-<version>-<os>-<arch>-<repository-id>-<default-branch>-<sha>` and restored with the same key minus `<sha>`. The cache SHALL be saved only after health verification succeeds and only when the exact key was not already restored.

#### Scenario: A new commit lands on the default branch

- **WHEN** no exact-key entry exists
- **THEN** the most recent entry with the same version, platform, repository, and branch is restored
- **AND** an entry from a different CodeGraph version is never matched

### Requirement: Restored indexes are synced; missing or invalid indexes are initialized

The workflow SHALL judge health from `codegraph status` output, because the CLI exits `0` even on failure.

#### Scenario: A usable index is restored

- **WHEN** `codegraph status` reports index statistics and no failure
- **THEN** the workflow logs "Mode: incremental sync" and runs `codegraph sync`

#### Scenario: The cache misses, or the restored index is unusable, or sync fails

- **WHEN** no usable index is available
- **THEN** only `.codegraph/` is removed and the workflow logs "Mode: fresh initialization" and runs `codegraph init --yes`
- **AND** a first-ever run does not fail for lack of a cache

### Requirement: The index must be healthy at the end of every run

The workflow SHALL fail unless a final `codegraph status` reports a healthy index.

#### Scenario: Health verification fails

- **WHEN** the final status output lacks index statistics or reports a failure
- **THEN** the job fails and no cache is saved

### Requirement: Phase 1 scope is limited to index maintenance

The workflow SHALL only maintain the CI-side index. It is not a required merge gate and SHALL NOT post PR comments, run affected-file analysis, or select tests. Those are future scope pending operational data.

#### Scenario: Indexes and worktrees

- **WHEN** developers use local `.codegraph` indexes or git worktrees
- **THEN** they are outside this CI lifecycle; the CI index is ephemeral, never committed, and never shared with local indexes
