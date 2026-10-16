# Post-Deploy Verification Capability Specification

## Requirements

### Requirement: Lighthouse verification audits a caller-selected live URL

`_lighthouse-template.yml` SHALL run Lighthouse CI against the supplied `site_url` and SHALL surface available scores/report links in the GitHub job summary.

#### Scenario: Lighthouse completes with a report

- **WHEN** `lhci autorun` produces a Lighthouse result
- **THEN** the workflow attempts to extract the temporary public report URL
- **AND** publishes available category scores in the job summary

#### Scenario: Lighthouse verification fails

- **WHEN** the job fails and report files exist
- **THEN** Lighthouse output/report files are uploaded as a bounded-retention artifact

### Requirement: Lighthouse is advisory by default

The Lighthouse job SHALL use `continue-on-error: true` so quality-audit failure does not by itself fail the caller workflow.

#### Scenario: Lighthouse reports threshold or execution failure

- **WHEN** the reusable job concludes unsuccessfully
- **THEN** the job may expose failure evidence
- **AND** the reusable job's advisory policy prevents that result from becoming a hard pipeline gate by itself

### Requirement: PageSpeed Insights checks both mobile and desktop

`_speedinsights-template.yml` SHALL query Google's PageSpeed Insights API separately for mobile and desktop strategies using performance, accessibility, best-practices, and SEO categories.

#### Scenario: SpeedInsights verification runs

- **WHEN** the caller supplies a `site_url`
- **THEN** both mobile and desktop strategies are requested
- **AND** each request includes the maintained category set

### Requirement: PageSpeed Insights retries transient failures

Each PageSpeed strategy SHALL make up to three attempts before giving up on that strategy.

#### Scenario: The API does not return HTTP 200 initially

- **WHEN** an attempt fails
- **THEN** the workflow retries up to the maintained limit with increasing delay
- **AND** the strategy is skipped after all attempts are exhausted

### Requirement: PageSpeed report formatting remains caller-owned

The shared workflow SHALL delegate PSI response formatting to the caller-provided `report_script` path.

#### Scenario: A consumer has product-specific report thresholds or layout

- **WHEN** it invokes the reusable workflow
- **THEN** it may provide its own report script path
- **AND** the shared workflow passes the PSI JSON response plus strategy context to that script

### Requirement: PageSpeed API authentication is optional

The SpeedInsights workflow SHALL accept an optional `PSI_API_KEY` secret and SHALL be able to call the API without a key when none is supplied.

#### Scenario: An API key is available

- **WHEN** `PSI_API_KEY` is non-empty
- **THEN** it is appended to the PSI request

#### Scenario: No API key is supplied

- **WHEN** the secret is absent
- **THEN** the request is made without a key

### Requirement: SpeedInsights is advisory by default

The SpeedInsights job SHALL use `continue-on-error: true` and SHALL upload generated report files on failure when available.

#### Scenario: PSI/report generation fails

- **WHEN** the job fails
- **THEN** available report files may be retained as failure artifacts
- **AND** the reusable job does not become a hard deployment gate by itself

### Requirement: ZAP supports passive production-safe and active non-production scan modes

`_zap-scan-template.yml` SHALL expose `baseline` (passive) and `full` (active) scan modes and an `environment` input (`production`, `staging`, `development`) that defaults to `production`. The template SHALL refuse any active scan (`full` mode or the API scan) when `environment` is `production` or when the target host matches `protected_hosts`.

#### Scenario: Production baseline
- **WHEN** `environment` is `production` and `scan_mode` is `baseline`
- **THEN** only the passive baseline scan runs and no OpenAPI/API scan is scheduled

#### Scenario: Active scan requested for production
- **WHEN** `environment` is `production` and `scan_mode` is `full`
- **THEN** the policy step fails the job before any ZAP traffic is sent

#### Scenario: Active scan against a protected host
- **WHEN** an active scan targets a host (or an OpenAPI `servers` entry) matching `protected_hosts`
- **THEN** the policy step fails the job before any ZAP traffic is sent

### Requirement: ZAP API scanning is enabled only for non-production full scans with an OpenAPI path

The ZAP workflow SHALL add an API matrix entry only when `openapi_path` is non-empty, `scan_mode` is `full` and `environment` is not `production`.

#### Scenario: Conditions met
- **WHEN** the scan matrix is constructed for a non-production full scan with an OpenAPI path
- **THEN** a baseline/full entry and an API entry are created and the spec is downloaded locally before ZAP runs

#### Scenario: Otherwise
- **WHEN** any condition is not met
- **THEN** only the web entry runs

### Requirement: Protected API specifications may use caller-supplied Basic Auth

The ZAP API preparation step SHALL accept the optional encoded `ZAP_DOC_BASIC_AUTH` secret for downloading a protected OpenAPI document without embedding credentials in the target URL.

#### Scenario: Protected API docs are scanned

- **WHEN** the API preparation step downloads the spec
- **THEN** credentials are decoded from the secret and masked
- **AND** curl Basic Auth is used for the download
- **AND** ZAP receives the resulting local file path

### Requirement: ZAP findings are summarized without opening issues and survive artifact failures

The shared ZAP workflow SHALL run ZAP directly, SHALL publish redacted risk counts and alert details (URL query values, userinfo and credential-like evidence removed) to the job summary, SHALL NOT open GitHub issues automatically, and SHALL upload only a small redacted JSON report as a best-effort artifact with bounded retention.

#### Scenario: Findings exist
- **WHEN** ZAP completes with exit code 0, 1 or 2
- **THEN** counts and a per-alert table appear in the job summary and a warning annotation lists the alert names
- **AND** findings alone do not fail the job

#### Scenario: Artifact upload fails
- **WHEN** artifact upload fails (e.g. storage quota)
- **THEN** the job emits an infrastructure warning distinct from security findings and the summary remains intact

#### Scenario: Scan engine failure
- **WHEN** ZAP exits with a code above 2 or produces no report
- **THEN** the job fails and is not reported as a clean scan
