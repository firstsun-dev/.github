# Shared CI Primitives — Verify/Test Stage Contract Delta

This delta introduces a reusable stage executor and aggregate gate contract for firstsun-dev consumers of the coordinated v2 Cloudflare pipeline; it does not change unrelated legacy `actions/setup`, `parse-env`, Mailpit or changed-path consumers.

## ADDED Requirements

### Requirement: Application repositories SHALL own stage tasks, not stage orchestration

The reusable CI interface SHALL accept `verify_tasks` and `test_tasks` as validated JSON arrays of objects with required nonempty `id` and `cmd` fields. The caller SHALL choose which actual work belongs to Verify or Test independently of task type; the reusable executor SHALL own setup, invocation, logging, results and gate semantics. Task IDs SHALL be unique per app/stage, stable and safe as Check labels. Invalid/unknown shapes SHALL fail before task execution.

#### Scenario: Unit Test should run on every feature Push
- **WHEN** a caller places its Unit Test command in `verify_tasks`
- **THEN** the Unit Test SHALL run in Verify on feature Pushes and PR merge candidates
- **AND** no new common-workflow source code shall be required to recognize the test type.

#### Scenario: Integration Test is expensive
- **WHEN** a caller places its Integration Test command in `test_tasks`
- **THEN** it SHALL run on PR and `merge_group` (where enabled), rather than ordinary feature Pushes.

#### Scenario: A new task is configured
- **WHEN** the app supplies a unique valid task ID and command for its chosen stage
- **THEN** the task SHALL be eligible for independent parallel execution under the same bounded stage runner.

#### Scenario: Task definitions are malformed
- **WHEN** the list is malformed JSON, contains duplicate/unsafe IDs, empty commands or unsupported fields
- **THEN** the stage SHALL fail closed before executing arbitrary or partial tasks.

### Requirement: Independent tasks SHALL run in parallel without an artificial serial stage barrier

The executor SHALL fan out independent configured tasks in a stage; it MAY use a bounded matrix for efficiency and concurrency control. `needs` edges SHALL be used only for actual artifact/command dependencies, not just to draw a linear Verify→Test graph. E2E MAY use a dedicated matrix for browser setup/variants and MUST contribute to the same Test Gate.

#### Scenario: Lint, Build and UT are independent
- **WHEN** they are configured without shared mutable prerequisites
- **THEN** they SHALL be allowed to execute concurrently
- **AND** one failure SHALL not create a false successful aggregate result.

#### Scenario: Test requires a build artifact
- **WHEN** a documented dependency exists
- **THEN** only the dependent Test job SHALL wait for the verified producer
- **AND** the artifact SHALL be reused only when revision, environment and build identity match.

### Requirement: Stage and PR Gates SHALL be deterministic and fail closed

A Verify Gate SHALL require success from every expected configured Verify task. A PR Gate SHALL require all expected Verify + Test + optional E2E tasks associated with the *same* eligible PR merge SHA / merge-group SHA. An explicitly empty optional stage MAY be marked not-configured, but a configured job that fails, is cancelled or is unexpectedly skipped SHALL cause the Gate to fail. A stable app-scoped PR Gate status SHALL always be reported when required by branch protection.

#### Scenario: One configured Check fails
- **WHEN** any expected stage task fails, is cancelled or is unexpectedly skipped
- **THEN** the corresponding Gate SHALL be red even if other tasks succeed.

#### Scenario: No Test tasks are defined
- **WHEN** the repository has an empty Test list and no E2E command
- **THEN** a PR SHALL still report the PR Gate against the merge candidate
- **AND** the Gate SHALL depend on passing Verify rather than pretending an omitted Test failed.

#### Scenario: The app has no affected files
- **WHEN** trusted changed-path detection identifies the app as truly unaffected
- **THEN** an explicitly named No-op decision MAY satisfy that app's stable required gate
- **AND** the workflow SHALL NOT be skipped entirely when that would strand a required check Pending.

### Requirement: Feature Push and PR SHALL have distinct, safe test policies

Feature Push SHALL run Verify for fast feedback. Same-repo PR SHALL run Verify and Test in the same run against the merge candidate; the independent tasks MAY start concurrently. PR Merge Gate SHALL NOT depend on an external Push run's latest HEAD status as an implicit proof of the tested merge revision. A `merge_group` SHALL run the no-deployment PR gate if the caller uses GitHub Merge Queue. Main Push SHALL pass its release eligibility policy before Production can mutate state.

#### Scenario: Feature Push is green but merge candidate breaks Build
- **WHEN** the latest PR base has introduced an incompatibility that the feature HEAD build did not see
- **THEN** PR Verify SHALL fail on the merge candidate
- **AND** the PR Gate SHALL refuse merge.

#### Scenario: Push and PR happen for the same HEAD
- **WHEN** both events are raised
- **THEN** heavy Test SHALL only run on the PR/merge-group event as defined by its stage
- **AND** intentionally repeated fast Verify SHALL be allowed to prove merge compatibility, without a cross-run gate simulator.

#### Scenario: Merge Queue is enabled
- **WHEN** GitHub emits `merge_group` and the repo requires the PR Gate
- **THEN** the workflow SHALL produce the corresponding required status against that merge-group revision
- **AND** no deployment SHALL occur.

### Requirement: Public fork tests SHALL use a safe trust boundary

Untrusted fork PR code SHALL NOT be executed on privileged organization self-hosted runners and SHALL NOT receive Cloudflare deploy, secrets, elevated write tokens or protected environment access. Callers with incompatible runner requirements SHALL document and implement a secure alternative rather than silently exposing secrets.

#### Scenario: Fork of the public MCP repository opens a PR
- **WHEN** its workflow is eligible for fork PR validation
- **THEN** all untrusted execution SHALL be confined to an approved unprivileged runner with no deployment credentials
- **AND** a failure to provision a safe runner SHALL NOT fall back to the privileged runner.

### Requirement: The shared workflow's own test suite SHALL be small and risk focused

The shared CI/CD implementation SHALL validate syntax and reference contract with static tooling; validate pure planning/task/gate decisions with unit tests; and validate mutation-safety invariants with targeted integration/stub tests. The required result SHALL be one aggregate Pipeline Gate. A real GitHub-runner canary SHALL cover reusable workflow integration before the floating version tag is promoted. A custom Actions expression scheduler or a count-of-assertions manifest SHALL NOT become an acceptance dependency.

#### Scenario: A change affects routing but not provider mutations
- **WHEN** the shared workflow is updated
- **THEN** static checks and routing/Gate unit tests SHALL run in parallel with independent safety tests
- **AND** all configured required test categories SHALL contribute to the Pipeline Gate.

#### Scenario: Canary does not complete
- **WHEN** real reusable-workflow verification is missing, failed, cancelled or has the wrong candidate SHA
- **THEN** the shared `@v2` tag SHALL NOT be promoted
- **AND** local simulator success alone SHALL NOT count as equivalent proof.

## MODIFIED Requirements

### Requirement: Shared Node/pnpm setup is configurable

Shared CI task execution SHALL reuse the existing setup action and repository-owned commands, allowing the selected runner to provide compatible Node/pnpm or explicitly installing required versions. Test execution SHALL not infer permission to use privileged self-hosted runners merely because setup would be faster.

#### Scenario: Public PR requires safe hosted setup
- **WHEN** a public fork PR is eligible for CI
- **THEN** its runner selection and dependency installation SHALL use unprivileged compatible runtime setup
- **AND** lack of preinstalled pnpm SHALL be handled by safe setup or an explicit failed job, never by privileged runner fallback.

