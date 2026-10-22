# Cloudflare Worker Delivery — Preview Shared Resource Delta Specification

## ADDED Requirements

### Requirement: A Native Preview SHALL share a Production storage resource only through an exact allow entry

The Preview preflight SHALL reject a Preview binding that targets a Production resource or a `preview_deny_resources` entry, unless `preview_binding_allow` contains an exact `kind:BINDING=identity` entry for a shareable storage kind (R2, KV, Vectorize, Analytics Engine) matching that binding and resource. The entry SHALL waive only those two checks for that binding and identity. A Production D1 database SHALL never be approvable, and every other check SHALL still apply.

#### Scenario: Exact approved R2
- **WHEN** the Preview config binds `SHINYI_MUYU_VOICE_CACHE` to `firstsun-blog-assets` and `preview_binding_allow` contains `r2_buckets:SHINYI_MUYU_VOICE_CACHE=firstsun-blog-assets`
- **THEN** the preflight passes, even if that bucket is listed in `preview_deny_resources`.

#### Scenario: Not the approved pair
- **WHEN** the same binding points at a different Production bucket, or the approved bucket is bound under another binding
- **THEN** the preflight fails.

#### Scenario: Production D1
- **WHEN** an allow entry names a Production D1 database
- **THEN** the preflight still fails.
