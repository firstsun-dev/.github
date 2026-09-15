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

### Requirement: ZAP supports passive production-safe and active development scan modes

`_zap-scan-template.yml` SHALL expose `baseline` and `full` scan modes, where baseline uses the passive baseline action and full uses the active full-scan action.

#### Scenario: `scan_mode` is `baseline`

- **WHEN** the baseline matrix entry runs
- **THEN** ZAP baseline scanning is used
- **AND** the action is configured not to fail the workflow solely from findings

#### Scenario: `scan_mode` is `full`

- **WHEN** the baseline matrix entry runs
- **THEN** ZAP full scanning is used
- **AND** callers are responsible for limiting this active mode to appropriate non-production targets

### Requirement: ZAP API scanning is enabled only when an OpenAPI path is supplied

The ZAP workflow SHALL add an API matrix entry when `openapi_path` is non-empty.

#### Scenario: The caller supplies an OpenAPI path

- **WHEN** the scan matrix is constructed
- **THEN** baseline/full web scanning and an API scan entry are created
- **AND** the API specification is downloaded locally before the ZAP API action runs

#### Scenario: No OpenAPI path is supplied

- **WHEN** the scan matrix is constructed
- **THEN** only the web/baseline matrix entry runs

### Requirement: Protected API specifications may use caller-supplied Basic Auth

The ZAP API preparation step SHALL accept the optional encoded `ZAP_DOC_BASIC_AUTH` secret for downloading a protected OpenAPI document without embedding credentials in the target URL.

#### Scenario: Protected API docs are scanned

- **WHEN** the API preparation step downloads the spec
- **THEN** credentials are decoded from the secret and masked
- **AND** curl Basic Auth is used for the download
- **AND** ZAP receives the resulting local file path

### Requirement: ZAP findings are summarized without opening issues automatically

The shared ZAP workflow SHALL publish available risk counts/details to the job summary and SHALL disable automatic GitHub issue creation in the ZAP actions.

#### Scenario: Findings exist

- **WHEN** `report_json.json` contains alerts
- **THEN** high, medium, low, and informational counts are summarized
- **AND** alert names/solutions may be included in the job summary
- **AND** the reusable workflow does not automatically create repository issues
