# Proposal: Let a Native Preview share an explicitly approved Production storage resource

Consumer: `firstsun-dev/blog#448` (Voice cache R2 bucket).

## Why

Native Preview preflight (`auditPreviewConfig`) rejects any Preview binding that points at the same resource as Production or at a `preview_deny_resources` entry, and `preview_binding_allow` could not waive either check (it only approved high-risk kinds). Shared Dev already lets a Worker share a storage resource through an exact `kind:BINDING=identity` allow entry. A Worker that deliberately shares one bucket with Production and isolates it by object-key namespace (blog's Voice cache) therefore could not use Native Preview without a change to this repository each time.

## What changes

- `preview_binding_allow` accepts `kind:BINDING=identity` for shareable storage kinds (`DEV_SHAREABLE_KINDS`: R2, KV, Vectorize, Analytics Engine), the same grammar as `dev_binding_allow`.
- A matching entry waives only the Production-collision and `preview_deny_resources` checks for that exact binding and identity. Other bindings, other identities and every other check are unchanged.
- A Production D1 can never be approved.
- Existing `kind:BINDING` entries (services, hyperdrive, secrets store) behave as before.

## Non-goals

No new workflow input, no change to Shared Dev policy, no move of the floating `@v2` tag, no consumer change.
