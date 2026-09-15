# Shared Engineering Config Capability Specification

## Requirements

### Requirement: Shared ESLint configuration is exposed as a configurable flat-config factory

`@firstsun/eslint-config` SHALL export `createConfig(...)` as its package entry point and SHALL accept the consumer repository root plus optional ignore/node-file overrides.

#### Scenario: A consumer creates its lint config

- **WHEN** it calls `createConfig({ rootDir })`
- **THEN** the shared configuration is constructed with ESLint recommended rules plus TypeScript-aware configuration
- **AND** type-aware parser resolution uses the supplied repository root

#### Scenario: A consumer has repository-specific generated or Node files

- **WHEN** it supplies `extraIgnores`, `workerIgnores`, or `nodeFiles`
- **THEN** those values extend the maintained shared defaults rather than requiring a fork of the package

### Requirement: Worker-targeted TypeScript code is linted as a Web/Worker environment

The shared ESLint config SHALL apply strict/stylistic type-aware rules to the maintained Worker/backend source globs and SHALL expose service-worker globals rather than browser globals for that target.

#### Scenario: Worker code references unsupported Node/browser globals

- **WHEN** Worker-targeted code uses restricted globals such as `process`, `Buffer`, `window`, `document`, `__dirname`, or `__filename`
- **THEN** ESLint reports an error with guidance toward Worker-compatible APIs

#### Scenario: Worker code imports restricted Node modules

- **WHEN** Worker-targeted code imports `fs`, `path`, `os`, or Node's `crypto` module through the configured restricted-import paths
- **THEN** ESLint reports an error instead of silently treating the code as a Node runtime

### Requirement: React-targeted TypeScript code receives browser and React lint semantics

The shared ESLint config SHALL apply browser globals, React hooks rules, React Refresh rules, and type-aware TypeScript rules to the maintained dashboard/web/frontend/UI globs.

#### Scenario: React hooks are used incorrectly

- **WHEN** frontend code violates the Rules of Hooks
- **THEN** lint fails through `react-hooks/rules-of-hooks`

#### Scenario: React code uses the automatic JSX runtime

- **WHEN** JSX is compiled without importing React into scope
- **THEN** `react/react-in-jsx-scope` does not require the legacy import

### Requirement: Node-oriented support files can use relaxed Node semantics

The shared config SHALL provide Node globals for plain JavaScript and the maintained Node-oriented TypeScript file set, including E2E specs and caller-supplied `nodeFiles`.

#### Scenario: E2E or support code uses Node APIs

- **WHEN** the file matches the Node-oriented configuration
- **THEN** Node globals are available
- **AND** selected unsafe/strict TypeScript rules are relaxed compared with the Worker/frontend rules where the shared config currently defines those exceptions

### Requirement: Shared TypeScript base config is strict and non-emitting

`@firstsun/tsconfig/base.json` SHALL define the common TypeScript baseline with ESNext target/module, bundler module resolution, strict type checking, and `noEmit`.

#### Scenario: A package extends the base config

- **WHEN** TypeScript resolves the shared base
- **THEN** strict mode is enabled
- **AND** TypeScript performs type checking without emitting compiled output
- **AND** JSON modules, isolated modules, import extension support, and ES module interop follow the maintained base options

### Requirement: React TypeScript config extends the shared base

`@firstsun/tsconfig/react.json` SHALL extend `base.json` and add React/browser-specific compiler settings.

#### Scenario: A React consumer extends the shared React config

- **WHEN** TypeScript resolves it
- **THEN** JSX uses `react-jsx`
- **AND** the library set includes ESNext, DOM, and DOM iterable APIs

### Requirement: Worker TypeScript config extends the shared base

`@firstsun/tsconfig/worker.json` SHALL extend `base.json` and add the maintained Worker-related type packages.

#### Scenario: A Worker consumer extends the shared Worker config

- **WHEN** TypeScript resolves it
- **THEN** the configured type set includes Node types and `@cloudflare/workers-types`

### Requirement: Shared config package releases use exact and floating-major Git tags

`scripts/release.sh` SHALL validate release versions in `vX.Y.Z` form, synchronize both shared package version fields, create an exact version tag, and move the corresponding major tag.

#### Scenario: A valid release version is confirmed

- **WHEN** the release script is run with a version such as `v1.2.0` and the interactive confirmation is accepted
- **THEN** both package manifests use version `1.2.0`
- **AND** an exact `v1.2.0` tag is created
- **AND** the floating `v1` tag is moved to the release commit

#### Scenario: An invalid version is supplied

- **WHEN** the version does not match `vX.Y.Z`
- **THEN** the release script exits with an error before tagging

### Requirement: Release preparation does not imply registry publication

The release helper SHALL prepare package versions and Git tags but SHALL NOT be specified as an npm-registry publishing mechanism unless executable publishing behavior is added later.

#### Scenario: Release script completes

- **WHEN** commit and tags have been prepared
- **THEN** the script prints the required Git push commands
- **AND** no package-registry publication guarantee is implied by this capability
