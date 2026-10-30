# Tasks: Add Grafana MCP over the existing NAS Tunnel

The change is a coordination contract. Check boxes for implementation phases remain **unchecked** until separately verified in the owning repositories.

## Phase 0 — Organization specification (this commit)

- [x] Confirm the `mcp-service-hosting` convention, canonical `/{service}/mcp` endpoint, service ownership and per-service audience model.
- [x] Review the existing ANAS Custom Domain, path-scoped Access Terraform, NAS Tunnel configuration and NAS Grafana deployment source.
- [x] Document the approved direction: a dedicated Grafana Proxy Worker, official NAS container, **existing** NAS Tunnel, scoped VPC Service, no public origin.
- [x] Record security, protocol, rollback and independent ANAS regression gates.

## Phase 1 — Preflight and repository ownership

- [ ] Create `firstsun-dev/grafana-mcp` if absent; protect its default branch and add an application-specific OpenSpec/README. Do not fork Grafana's upstream MCP server.
- [ ] Verify Access `/anas/mcp` OAuth + headless baseline, Custom Domain, route precedence, current `firstsun-nas` Tunnel status, and its `remote_config` versus Terraform `config_src` drift.
- [ ] Confirm NAS port availability, Docker networking to existing Grafana API, Grafana service account privileges, compatible pinned official MCP image, and Cloudflare VPC role/QUIC support.
- [ ] If VPC HTTP streaming, Tunnel reachability or route exactness fails, stop and submit a revised design; no public Tunnel hostname workaround.

## Phase 2 — NAS origin (infra-config)

- [ ] Add independently managed NAS Ansible role or separate Compose project for a pinned official `grafana/mcp-grafana` image. Avoid changes or forced restart to the existing `nas_monitor` stack.
- [ ] Enable `streamable-http` and internal `/grafana/mcp`, `--disable-write`, a minimal read-only tool allowlist, a loopback-only host port, resource limits, health check and restrictive Host/Origin handling.
- [ ] Provision Grafana read-only service account and separate upstream caller token in the authorized secret store. Never commit or log the secrets.
- [ ] Prove local `initialize`, `tools/list`, Prometheus query, dashboard read and alert read. Verify write tools are absent.

## Phase 3 — Grafana Proxy Worker (grafana-mcp)

- [ ] Implement exact Host/path/method handling, signed Access JWT verification (JWKS, issuer, Grafana-specific AUD, exp/nbf), fail-closed 401/503 and log redaction.
- [ ] Implement a narrowly scoped HTTP VPC Service binding; preserve Streamable HTTP request/response streaming, MCP/session headers, upstream statuses, cancellation and timeouts.
- [ ] Strip client authorization and identity headers, `X-Grafana-*` overrides and all unapproved forwarded headers; inject only the origin caller secret.
- [ ] Keep `workers_dev=false` and `preview_urls=false`. Do not declare Cloudflare Routes or Custom Domain in consumer Wrangler.
- [ ] Add unit/integration tests for valid/invalid/wrong-AUD Access JWT, spoofed headers, path collisions, method constraints, origin 401, outages, streaming, session lifecycle and write attempts.
- [ ] Add thin CI/CD invoking approved shared workflow; pin workflow reference per organization policy and ensure safe smoke/rollback behavior.

## Phase 4 — Cloudflare Terraform (infra-config)

- [ ] Register `grafana` on the canonical hostname with one exact Worker Route to `grafana-mcp-proxy`.
- [ ] Provision independent Grafana path-scoped Access app with Managed OAuth + DCR/PKCE, explicit allow identity policy and dedicated Service Token policy, default deny, new AUD.
- [ ] Provision scoped HTTP VPC Service using **existing** `firstsun_nas` Tunnel and verified loopback origin/port; no extra DNS, public Tunnel ingress or Tunnel replacement.
- [ ] Expose non-secret Grafana AUD and service ID to Worker config without materializing/printing secret credentials.
- [ ] Run `terraform fmt`, `validate`, and a **refreshed full plan**. Reject unrelated drift, any existing ANAS/Custom-Domain/Tunnel modification, any destroy/replace and access-policy widening.
- [ ] Do not apply until explicitly approved; stage VPC/Access before enabling the public route so the service is never accidentally open.

## Phase 5 — End-to-end acceptance and cutover

- [ ] Verify anonymous 401 and protected-resource metadata for `/grafana/mcp`, real Managed OAuth PKCE (including ChatGPT), and real Service Token `initialize` / `tools/list`.
- [ ] Confirm Grafana and ANAS Access AUDs are independent and mutually rejected, with no cross-service policy/token inheritance.
- [ ] Verify MCP POST, optional GET event streams, DELETE/session behavior, headers, long-running/canceled requests, origin caller auth, query timeouts and error translation.
- [ ] Verify Grafana read-only operations end to end and demonstrate state-changing tools cannot be invoked.
- [ ] Verify disallowed paths, alternate hostnames, client headers and unregistered services cannot reach the Grafana MCP or ANAS tools.
- [ ] Re-run ANAS OAuth, Service Token, `tools/list` and read-only tool regression after enabling Grafana; confirm no Custom Domain/DNS/Tunnel changes.
- [ ] Capture evidence (redacted logs/CI links, Terraform plan summary, query timestamps) and update runbook.

## Phase 6 — Rollback and operational readiness

- [ ] Exercise Grafana-only disable/rollback sequence. Do not roll back ANAS or touch the shared Custom Domain/Tunnel.
- [ ] Configure resource/availability monitoring, safe secret rotation and documented update procedure for the pinned image and Proxy Worker.
- [ ] Mark this OpenSpec work implemented only after production evidence, user-visible client connection, and all security gates pass.

## Mandatory no-go conditions

- Any unauthorized credential in source, Terraform output, CI log or client response.
- Any unreviewed `terraform apply`, unrelated production drift, or destroy/replace of existing Cloudflare resources.
- Access policy widened to `any_valid_service_token`, anonymous bypass, generic public origin or reuse of ANAS AUD.
- Worker Route collision, ambiguous OAuth discovery or direct Client → NAS access.
- Missing VPC QUIC/HTTP-streaming support, unsafe Grafana header overrides, write tools enabled or monitoring disruption.
