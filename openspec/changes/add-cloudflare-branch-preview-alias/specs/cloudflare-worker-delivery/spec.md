# Cloudflare Worker Delivery Capability Specification (Delta)

## ADDED Requirements

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
