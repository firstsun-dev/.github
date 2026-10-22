# Tasks: Explicit Preview sharing of a Production storage resource

- [x] `auditPreviewConfig`: exact `kind:BINDING=identity` allow for shareable storage kinds waives Production-collision and denylist checks
- [x] Unit tests: approved share passes (also against the denylist); other bucket, other binding and Production D1 still fail
- [x] Docs: `docs/cf-worker-v2.md` binding audit rules
- [ ] Merge, then move the floating `@v2` tag (owner)
- [ ] Consumer `blog#448`: `previews.r2_buckets` + `preview_binding_allow`, drop the bucket from `preview_deny_resources`
