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

### Requirement: Branch-specific preview aliases are opt-in

The pipeline SHALL accept an `enable_preview_alias` boolean input, defaulting to `false`, and SHALL NOT change deployment behavior for any caller that omits it.

#### Scenario: A caller does not set `enable_preview_alias`

- **WHEN** the reusable workflow is invoked without this input
- **THEN** no preview alias is computed or attached to any deployment
- **AND** deployment behavior is identical to the pipeline's behavior before this capability existed

#### Scenario: A caller explicitly enables `enable_preview_alias`

- **WHEN** `enable_preview_alias` is `true`
- **THEN** the alias behavior described below applies only to that caller's deployments

### Requirement: Opted-in non-main deployments receive a stable, deterministic preview alias

When `enable_preview_alias` is `true` and the ref is not `refs/heads/main`, the deploy job SHALL derive a Cloudflare Aliased Preview URL alias from the branch name and attach it to the Worker version upload.

#### Scenario: A non-main branch is deployed with the feature enabled

- **WHEN** `enable_preview_alias` is `true` and `github.ref` is not `refs/heads/main`
- **THEN** the branch name (`github.ref_name`) is normalized to a lowercase alias using only `a-z`, `0-9`, and `-`
- **AND** the normalized alias begins with a letter, has no leading/trailing/repeated `-`, and is at most 32 characters
- **AND** `wrangler versions upload` is invoked once with `--preview-alias <alias>` for the same upload that produces the deployed `VERSION_ID`

#### Scenario: The same branch is pushed again

- **WHEN** a later commit is pushed to a branch that was previously deployed with an alias
- **THEN** the normalization is recomputed from the same branch name and yields the same alias
- **AND** the alias now points at the newly uploaded Worker version for that branch

#### Scenario: A branch name would exceed the alias length limit

- **WHEN** the normalized branch name exceeds 32 characters
- **THEN** the alias is truncated and suffixed with a short hash derived from the original branch name (not the commit SHA)
- **AND** two long branch names that differ only near the end SHALL NOT normalize to the same alias

### Requirement: Aliased previews do not replace the shared development deployment

Attaching a preview alias SHALL NOT change the pipeline's existing dev-deployment architecture.

#### Scenario: An aliased deployment completes

- **WHEN** the aliased Worker version upload succeeds
- **THEN** the pipeline still deploys that same version to the `dev` environment at 100% traffic exactly as it does when the alias feature is disabled
- **AND** the shared dev endpoint (e.g. a caller's `app_url_dev`) continues to reflect the latest non-main push, regardless of alias usage

### Requirement: Production deployments never receive a preview alias

`main` deployments SHALL NOT pass `--preview-alias` under any input configuration.

#### Scenario: `enable_preview_alias` is true and the ref is `refs/heads/main`

- **WHEN** the deploy job runs for `refs/heads/main`
- **THEN** no alias is computed or passed to `wrangler versions upload`
- **AND** production deployment, verification, and rollback behavior are unchanged by this capability

### Requirement: Preview alias lifecycle cleanup is out of scope for this capability's initial phase

The pipeline SHALL NOT attempt to delete, expire, or reassign aliases when a branch is deleted or merged.

#### Scenario: A branch with an alias is deleted

- **WHEN** the source branch for a previously created alias no longer exists
- **THEN** the pipeline takes no automatic action against that alias
- **AND** cleanup remains a manual or future-phase operation
