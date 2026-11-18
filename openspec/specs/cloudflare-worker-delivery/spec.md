# Cloudflare Worker Delivery Capability Specification

## Supported interface: Cloudflare Worker v2

### Requirement: The shared Worker delivery capability remains on v2

The supported shared Worker delivery entry point is `.github/workflows/_cf-worker-v2.yml`, with its internal CI, delivery, lifecycle, and preview-GC workflows plus `actions/cf-preview/`. The shipped v2 contract is recorded in the [v2 baseline delta specification](../../changes/redesign-cloudflare-worker-cicd-v2/specs/cloudflare-worker-delivery/spec.md). That source labels itself as a historical, partially implemented contract; requirements marked there as unfinished remain open work. Its statements that v1/v1.5 remain valid describe the earlier state and are superseded by the v1 retirement below. Current executable code on `main` remains the observed behavior under the source-precedence rules in [`openspec/project.md`](../../project.md).

The [stage and Dev simplification](../../changes/simplify-cf-ci-stages-and-dev-delivery/specs/cloudflare-worker-delivery/spec.md), [in-place refactor](../../changes/refactor-cloudflare-worker-cicd-v2/specs/cloudflare-worker-delivery/spec.md), and [target-unification](../../changes/unify-cf-worker-v2-delivery-targets/specs/cloudflare-worker-delivery/spec.md) specifications are follow-on change contracts. They do not silently replace the shipped v2 behavior while their changes remain in progress.

#### Scenario: A consumer uses the supported shared delivery entry point

- **WHEN** a repository invokes the shared Worker delivery workflow
- **THEN** it uses `_cf-worker-v2.yml` and the supported v2 contract above
- **AND** current implementation behavior is reconciled against executable sources on `main`

The [v2 guide](../../../docs/cf-worker-v2.md) documents the operator-facing stages and targets; it is a guide, while this capability spec and its linked v2 contract are normative.

## Retired v1 contract

The legacy reusable workflow `.github/workflows/_cf-worker-template.yml` and its branch-alias action were removed on 2026-11-16 after organization-wide searches found no current callers. The v1 contract is retired and is no longer a supported interface. Historical proposals and the 2026-09-15 source audit retain v1 references as records of their original state.
