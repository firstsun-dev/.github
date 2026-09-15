# PostgreSQL Migration Delivery Capability Specification

## Requirements

### Requirement: Migration validation uses an ephemeral PostgreSQL target

`_pg-migration-test.yml` SHALL validate Atlas-managed migrations against a disposable PostgreSQL service without production database secrets.

#### Scenario: Migration validation runs with defaults

- **WHEN** the reusable test workflow is invoked without overrides
- **THEN** it uses `db/migrations` as the migration directory
- **AND** `db/atlas.hcl` as the Atlas config
- **AND** PostgreSQL 16 as the test database major version
- **AND** an ephemeral `test` database with test-only credentials

### Requirement: Atlas CLI version is pinned within migration workflows

The PostgreSQL test and deploy workflows SHALL use the configured pinned Atlas CLI version rather than implicitly following the latest release.

#### Scenario: A migration workflow installs Atlas

- **WHEN** installation executes
- **THEN** Atlas Community `v1.3.0` is requested
- **AND** the installed version is printed before migration operations

### Requirement: Migration test validates, applies, and checks final state

The test workflow SHALL first validate the migration directory, then apply migrations to the ephemeral database, then verify that Atlas reports a clean state.

#### Scenario: Valid migrations apply cleanly

- **WHEN** `atlas migrate validate` and `atlas migrate apply` succeed
- **THEN** the final status must contain `Migration Status: OK`
- **AND** it must not report one or more pending files

#### Scenario: Atlas reports dirty or pending state

- **WHEN** the post-apply status is not clean or pending migrations remain
- **THEN** the migration test fails

### Requirement: Caller-selected Atlas config is passed to Atlas operations

The `atlas-config` input SHALL be applied to validation, apply, and status operations that expose the input.

#### Scenario: A repository stores Atlas configuration outside the default path

- **WHEN** the caller supplies another `atlas-config` path
- **THEN** the test workflow uses that path for validate, apply, and status
- **AND** the deploy workflow uses that path for apply

### Requirement: Production-target migration deployment receives the database URL only as a secret

`_pg-migration-deploy.yml` SHALL require the `database-url` workflow secret and SHALL pass it to Atlas through the deploy job environment rather than embedding it in the workflow source.

#### Scenario: A caller invokes migration deployment

- **WHEN** the deploy workflow begins
- **THEN** a `database-url` secret must have been supplied by the caller
- **AND** Atlas applies migrations to that URL

### Requirement: Migration deployment is serialized per repository and ref

The deploy workflow SHALL use a non-cancelling concurrency group derived from repository and ref.

#### Scenario: Two migration deployments target the same repository/ref

- **WHEN** both workflow runs overlap
- **THEN** they share the same migration concurrency group
- **AND** an in-progress migration is not cancelled in favor of the newer run

#### Scenario: Deployments target different refs or repositories

- **WHEN** repository or ref differs
- **THEN** the concurrency key differs and the workflow does not serialize them solely because both use this reusable workflow

### Requirement: Migration workflows remain application-agnostic

The shared workflows SHALL accept migration paths, Atlas config, runner, and PostgreSQL test version without embedding a consumer application name.

#### Scenario: Another Firstsun repository adopts Atlas migrations

- **WHEN** its directory layout matches defaults or supplies overrides
- **THEN** it can invoke the shared workflows without forking their migration implementation
