# Cloudflare Worker Delivery Capability Specification

## Requirements

### Requirement: The reusable pipeline accepts application-owned commands and targets

`_cf-worker-template.yml` SHALL own generic Cloudflare Worker delivery while accepting application identity, path, version, build command, optional test/migration/verification commands, environment variables, URLs, and runner selection from the caller.

#### Scenario: A repository adopts the shared pipeline

- **WHEN** a caller invokes the reusable workflow
- **THEN** it supplies `app_name`, `app_path`, `app_version`, and `build_cmd`
- **AND** it may independently enable unit, integration, E2E, migration, and smoke-verification stages by supplying the corresponding commands
- **AND** app-specific commands remain executed from `app_path`

### Requirement: Test stages run independently before delivery

Configured unit, integration, and E2E stages SHALL run as separate jobs before migration/deployment.

#### Scenario: Multiple test types are configured

- **WHEN** unit, integration, and E2E commands are non-empty
- **THEN** the jobs may execute in parallel
- **AND** a failure or cancellation prevents delivery from proceeding

#### Scenario: A test command is omitted

- **WHEN** a test command is the empty string
- **THEN** that job is skipped
- **AND** skipped optional tests do not by themselves block later delivery

### Requirement: E2E verification supports caller-defined variants

The E2E job SHALL expand the JSON `test_e2e_variants` input as a matrix and SHALL NOT fail-fast across variants.

#### Scenario: Browser or execution variants are supplied

- **WHEN** the caller provides multiple variant objects
- **THEN** each variant runs the base E2E command with its own optional arguments
- **AND** variant-specific artifact suffixes may distinguish failure reports
- **AND** one failing variant does not cancel the remaining variants

### Requirement: Test evidence favors annotations and failure artifacts

Configured test jobs SHALL publish JUnit-compatible reports to GitHub Checks where supported, and E2E/smoke report directories SHALL be uploaded as artifacts on failure rather than unconditionally on successful runs.

#### Scenario: E2E or smoke tests fail

- **WHEN** report/test-result files exist after failure
- **THEN** the workflow attempts to upload them with bounded retention

#### Scenario: Unit or integration report publishing has a reporting problem

- **WHEN** the test-reporter step itself cannot publish a report
- **THEN** report publication is best-effort and does not replace the underlying test command as the source of test success/failure

### Requirement: Mailpit can be enabled for integration and E2E tests

The shared pipeline SHALL allow callers to request the organization Mailpit action for integration/E2E execution.

#### Scenario: `use_mailpit` is true

- **WHEN** integration or E2E tests run
- **THEN** the shared Mailpit action starts before the corresponding test command

### Requirement: Migration is a push-only pre-deployment stage

Migration SHALL run only outside pull-request events, after test jobs have no failure/cancellation, and only when at least one migration command is configured.

#### Scenario: Main is being delivered

- **WHEN** the ref is `refs/heads/main`
- **THEN** the production migration command is selected

#### Scenario: A non-main branch is being delivered

- **WHEN** the ref is not `refs/heads/main`
- **THEN** the development migration command is selected

#### Scenario: The workflow runs for a pull request

- **WHEN** the event is `pull_request`
- **THEN** migration and deployment are skipped

### Requirement: Build-time environment follows the deployment target

The deploy job SHALL derive production vs development build environment from the Git ref and SHALL expose application version/environment values to the build.

#### Scenario: Main is built for production

- **WHEN** `github.ref` is `refs/heads/main`
- **THEN** `VITE_APP_ENV` is `prod`
- **AND** production Vite environment lines are applied

#### Scenario: A non-main branch is built for development

- **WHEN** the ref is not `refs/heads/main`
- **THEN** `VITE_APP_ENV` is `dev`
- **AND** development Vite environment lines are applied
- **AND** `CLOUDFLARE_ENV=dev` is set at build time so Cloudflare adapters resolve the named environment correctly

### Requirement: Deployment uses Cloudflare Worker versions with traceable metadata

The deploy job SHALL upload a Worker version, extract its version ID, and route 100% traffic to that version.

#### Scenario: A deployment is executed

- **WHEN** the application build succeeds
- **THEN** Wrangler uploads a version for the selected environment
- **AND** deployment metadata includes the short commit hash and application version
- **AND** the new Worker version ID is captured as a job output
- **AND** the uploaded version is deployed at 100% traffic

### Requirement: The previous deployed version is captured before deployment

Before uploading the new Worker version, the deploy job SHALL attempt to resolve the currently deployed version for the selected environment.

#### Scenario: A current deployment exists

- **WHEN** Wrangler deployment history returns a deployed version
- **THEN** its version ID is exposed as `previous_version_id` for potential rollback

#### Scenario: No previous version can be resolved

- **WHEN** no prior version ID is available
- **THEN** deployment of the new version may continue
- **AND** a later rollback step may safely decline to revert

### Requirement: Smoke verification runs against the deployed environment

When `verify_cmd` is configured, the workflow SHALL run smoke tests after a successful deployment against the URL selected for the current ref.

#### Scenario: Verification is enabled

- **WHEN** deploy succeeds
- **THEN** the smoke job receives the production URL for main or the development URL for another branch
- **AND** `TEST_ENV` reflects `prod` or `dev`
- **AND** caller-provided verification environment lines are applied before the command

### Requirement: Production verification failure triggers rollback

A production smoke-test failure SHALL trigger an attempt to redeploy the previously captured Worker version.

#### Scenario: Main deployment succeeds but smoke verification fails

- **WHEN** `previous_version_id` is available
- **THEN** the rollback job deploys that version at 100% traffic
- **AND** records a revert message referring to the failed commit

#### Scenario: Verification fails but no previous version was captured

- **WHEN** the rollback job receives an empty previous version ID
- **THEN** it exits successfully without issuing a revert deployment

#### Scenario: Development verification fails

- **WHEN** the failed deployment is not from `main`
- **THEN** the production rollback job does not run

### Requirement: The shared Cloudflare pipeline may rely on the organization runner image

The pipeline SHALL use `actions/setup` with Node and pnpm installer steps disabled for its jobs, relying on the selected runner to provide the required runtime/package-manager when using this template.

#### Scenario: A caller overrides the runner

- **WHEN** a different runner label is supplied
- **THEN** that runner must remain compatible with the pipeline's assumption that Node/pnpm are already available unless the shared template is changed
