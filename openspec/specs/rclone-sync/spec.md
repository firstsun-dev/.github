# Rclone Sync Capability Specification

## Requirements

### Requirement: Rclone setup installs the CLI only when needed

The `actions/setup-rclone` composite action SHALL ensure `rclone` is available on supported Linux and macOS runners without reinstalling it when the command already exists.

#### Scenario: Linux runner does not have rclone

- **WHEN** the setup action runs on Linux and `rclone` is not found
- **THEN** the action installs rclone through the system package manager

#### Scenario: macOS runner does not have rclone

- **WHEN** the setup action runs on macOS and `rclone` is not found
- **THEN** the action installs rclone through Homebrew

#### Scenario: Runner already provides rclone

- **WHEN** `rclone` is already available
- **THEN** installation is skipped

### Requirement: Google Drive remote configuration is caller-controlled

The setup action SHALL create an rclone Drive remote from caller-provided client credentials, token, optional root-folder ID, and remote name.

#### Scenario: A caller configures the default remote

- **WHEN** no custom remote name is supplied
- **THEN** the remote is named `gdrive`
- **AND** the configured type is `drive`
- **AND** the caller-provided client ID, client secret, token, and optional root-folder ID are written to rclone's user configuration

#### Scenario: A caller selects another remote name

- **WHEN** `remote_name` is supplied
- **THEN** the generated rclone config uses that name
- **AND** downstream commands can address the same configured remote by name

### Requirement: The reusable sync workflow owns generic source-to-Drive synchronization

`rclone-sync.yml` SHALL accept a source path and destination path from the caller and SHALL perform `rclone sync` against the configured remote.

#### Scenario: A sync run uses defaults

- **WHEN** the caller supplies only the required paths and Drive secrets
- **THEN** the remote name defaults to `gdrive`
- **AND** sync arguments default to `--progress --delete-after`
- **AND** the workflow runs on `ubuntu-latest`

### Requirement: Sync arguments are caller-configurable

The reusable workflow SHALL accept an optional `sync_args` string and pass it to the `rclone sync` invocation.

#### Scenario: A caller needs another rclone policy

- **WHEN** `sync_args` is overridden
- **THEN** the supplied arguments replace the default argument string
- **AND** the shared workflow does not independently reinterpret those flags

### Requirement: Post-sync verification is enabled by default

The reusable workflow SHALL run `rclone check` after synchronization unless the caller disables it.

#### Scenario: Default verification runs

- **WHEN** `run_check` is `true`
- **THEN** the workflow compares source and destination using `rclone check --size-only`

#### Scenario: Caller disables verification

- **WHEN** `run_check` is `false`
- **THEN** the post-sync check step is skipped

### Requirement: Google Drive secrets remain workflow secrets at the reusable-workflow boundary

The sync workflow SHALL require Drive client ID, client secret, and token as secrets, with the root-folder ID optional.

#### Scenario: A repository adopts the reusable sync workflow

- **WHEN** it calls `rclone-sync.yml`
- **THEN** `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, and `GDRIVE_TOKEN` are required secret inputs
- **AND** `GDRIVE_FOLDER_ID` may be omitted
- **AND** secret values are passed to the setup action rather than committed in workflow source

### Requirement: The shared workflow does not hide destructive sync semantics

The reusable workflow SHALL use rclone's `sync` operation rather than `copy`; callers therefore remain responsible for understanding that destination files absent from the source may be removed according to the selected sync arguments.

#### Scenario: Default `--delete-after` semantics apply

- **WHEN** destination-only files exist and default sync arguments are used
- **THEN** rclone may delete those files after transfer according to rclone sync semantics
- **AND** the shared workflow does not transform the operation into a non-destructive copy
