# Obsidian Plugin Delivery Capability Specification

## Requirements

### Requirement: Shared plugin CI only runs code-quality work when relevant files change

`obsidian-plugin-ci.yml` SHALL classify plugin code changes using the maintained path filter before running lint, compatibility/test, artifact, and release-metadata jobs.

#### Scenario: A pull request changes plugin implementation or build inputs

- **WHEN** a changed path matches the configured code filter
- **THEN** the filter job exposes `code=true`
- **AND** downstream plugin CI jobs gated by that output may run

#### Scenario: No configured code path changed

- **WHEN** the filter reports `code=false`
- **THEN** lint, test, artifact, and release-metadata jobs are skipped

### Requirement: Plugin CI checks supported Node versions independently

The shared test job SHALL run the plugin build and test suite across Node 22 and Node 24 without fail-fast cancellation.

#### Scenario: Compatibility matrix runs

- **WHEN** plugin code changed
- **THEN** each matrix entry installs dependencies with `npm ci`
- **AND** runs `npm run build`
- **AND** runs tests with coverage
- **AND** one failing Node version does not cancel the other matrix entry

### Requirement: Coverage upload is bounded to the caller-selected primary Node version

The test matrix SHALL upload the coverage directory only from the matrix entry whose Node version matches the reusable workflow's `node-version` input.

#### Scenario: The default configuration is used

- **WHEN** Node 22 and 24 test entries complete
- **THEN** only the Node 22 entry attempts to publish the `coverage-report` artifact

### Requirement: Pull-request/plugin-build artifacts are built and packaged independently

The artifact job SHALL build the plugin and package release-relevant files into a branch-identifiable ZIP artifact.

#### Scenario: Plugin code changed

- **WHEN** the artifact job runs
- **THEN** it reads the plugin version from `manifest.json`
- **AND** sanitizes the branch name for the ZIP filename
- **AND** includes `main.js`, `manifest.json`, and `styles.css` when present
- **AND** uploads the ZIP with seven-day retention

### Requirement: Release metadata is finalized only after required CI succeeds

The release-metadata job SHALL depend on code filtering, lint, and test jobs and SHALL use semantic-release to create release metadata rather than building/uploading final release bytes itself.

#### Scenario: Plugin CI succeeds for a releasable change

- **WHEN** semantic-release runs
- **THEN** it may update version metadata/changelog, create the release commit/tag, and create the GitHub Release shell
- **AND** this job does not attest or upload the final `main.js` release asset

### Requirement: Final release assets are built from the exact published tag

`obsidian-plugin-release-build.yml` SHALL be invoked from a caller workflow triggered by `release: published` and SHALL check out `github.event.release.tag_name` before building release assets.

#### Scenario: A GitHub Release is published

- **WHEN** the release-build reusable workflow executes
- **THEN** checkout targets the exact published tag
- **AND** dependencies are installed from that source tree
- **AND** the plugin is built once from that tag

### Requirement: Release provenance covers the same bytes that are uploaded

The release-build workflow SHALL attest the build outputs generated from the published tag and SHALL upload those same local bytes without rebuilding between attestation and upload.

#### Scenario: Release output includes `main.js`

- **WHEN** the tagged build completes
- **THEN** `main.js` must exist
- **AND** its checksum is observable before attestation/upload
- **AND** build provenance is created for that exact file
- **AND** that same file is supplied to `gh release upload`

#### Scenario: `styles.css` exists

- **WHEN** the tagged build produces `styles.css`
- **THEN** it is also attested
- **AND** it is uploaded as a release asset

#### Scenario: `styles.css` does not exist

- **WHEN** no stylesheet is produced
- **THEN** stylesheet attestation and upload are skipped
- **AND** `main.js` and `manifest.json` remain required release assets

### Requirement: Uploaded plugin bytes are verified after release upload

The release-build workflow SHALL download the uploaded `main.js` asset and compare its SHA-256 digest with the local attested build output.

#### Scenario: Uploaded and local bytes match

- **WHEN** the downloaded release asset digest equals the local digest
- **THEN** release verification succeeds

#### Scenario: Uploaded and local bytes differ

- **WHEN** the digests differ
- **THEN** the workflow fails rather than claiming provenance for unmatched release bytes

### Requirement: Release credentials remain caller-controlled

Both reusable plugin workflows SHALL allow the caller to provide an optional `RELEASE_TOKEN`, falling back to the workflow-provided GitHub token where implemented.

#### Scenario: A caller supplies a dedicated release token

- **WHEN** semantic-release or `gh release upload` needs GitHub write access
- **THEN** the supplied release token is used in preference to the default token
