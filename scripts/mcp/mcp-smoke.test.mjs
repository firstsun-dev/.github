// End-to-end tests of actions/mcp-smoke/mcp-smoke.mjs and actions/mcp-portal-sync/sync.mjs against mock servers (no network, no Cloudflare).
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { startMock, startMockApi, TOKEN } from './mock-mcp-server.mjs';

const SMOKE = new URL('../../actions/mcp-smoke/mcp-smoke.mjs', import.meta.url).pathname;
const SYNC = new URL('../../actions/mcp-portal-sync/sync.mjs', import.meta.url).pathname;

const exec = (script, env) =>
  new Promise((resolve) => {
    const p = spawn(process.execPath, [script], { env: { PATH: process.env.PATH, ...env } });
    let out = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (out += d));
    p.on('close', (code) => resolve({ code, out }));
  });
const creds = { CF_ACCESS_CLIENT_ID: TOKEN.id, CF_ACCESS_CLIENT_SECRET: TOKEN.secret };
const fast = { TIMEOUT_MS: '1500', RETRIES: '0', RETRY_DELAY_MS: '10' };
const smoke = async (mockOpts, env) => {
  const m = await startMock(mockOpts);
  try {
    return { ...(await exec(SMOKE, { MCP_URL: `${m.url}${env.PATH_SUFFIX ?? '/mcp'}`, ...fast, ...env })), m };
  } finally {
    m.close();
  }
};
const noLeak = (out) => assert.ok(!out.includes(TOKEN.secret), 'the secret must never be printed');

test('upstream: initialize + tools/list + expected tools + read-only call pass; header injected; secret masked and never printed', async () => {
  const r = await smoke({}, { ...creds, EXPECT_TOOLS: 'health,search_dashboards', READ_CALLS: '[{"tool":"health","arguments":{},"expect":"ok"}]' });
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, new RegExp(`::add-mask::${TOKEN.id}`));
  assert.match(r.out, /anonymous request refused \(HTTP 403\)/);
  assert.match(r.out, /invalid service token refused \(HTTP 403\)/);
  assert.match(r.out, /read-only tools\/call health/);
  assert.ok(r.m.seen.some((s) => s.authed), 'authenticated requests carry both Access headers');
  noLeak(r.out.replace(`::add-mask::${TOKEN.secret}`, '')); // the mask command itself is the one intended appearance (GitHub hides it)
});

test('upstream over SSE and an expected server name', async () => {
  const r = await smoke({ sse: true, serverName: 'Firstsun Analytics' }, { ...creds, EXPECT_TOOLS: 'health', EXPECT_SERVER_NAME: '^Firstsun Analytics$' });
  assert.equal(r.code, 0, r.out);
});

test('wrong service token: fails with an actionable message, no secret in the output', async () => {
  const r = await smoke({}, { CF_ACCESS_CLIENT_ID: TOKEN.id, CF_ACCESS_CLIENT_SECRET: 'wrong-secret-value', EXPECT_TOOLS: 'health' });
  assert.equal(r.code, 1);
  assert.match(r.out, /initialize failed: HTTP 403.*service token/);
  assert.ok(!r.out.includes('wrong-secret-value') || /::add-mask::wrong-secret-value/.test(r.out));
  assert.ok(!r.out.replace('::add-mask::wrong-secret-value', '').includes('wrong-secret-value'));
});

test('Access redirect is diagnosed as an Access challenge', async () => {
  const r = await smoke({ redirect: true }, { CF_ACCESS_CLIENT_ID: 'x-id-1234', CF_ACCESS_CLIENT_SECRET: 'x-secret-1234', REQUIRE_ANON_REJECT: 'false' });
  assert.equal(r.code, 1);
  assert.match(r.out, /redirected to Cloudflare Access login/);
});

test('a Bypass (anonymous allowed) fails the run', async () => {
  const r = await smoke({ bypass: true }, { ...creds });
  assert.equal(r.code, 1);
  assert.match(r.out, /anonymous request was NOT refused.*no Bypass/);
});

test('a missing expected tool fails and lists what the server returned', async () => {
  const r = await smoke({}, { ...creds, EXPECT_TOOLS: 'health,missing_tool' });
  assert.equal(r.code, 1);
  assert.match(r.out, /expected tool 'missing_tool' is not exposed; tools\/list returned: health, search_dashboards/);
});

test('regex expectation and a failing read-only call', async () => {
  assert.equal((await smoke({}, { ...creds, EXPECT_TOOLS: 're:^search_' })).code, 0);
  const r = await smoke({ healthError: true }, { ...creds, READ_CALLS: '[{"tool":"health"}]' });
  assert.equal(r.code, 1);
  assert.match(r.out, /tools\/call health: the tool reported an error/);
});

test('partial credentials fail instead of running anonymously; bad URL / mode / READ_CALLS fail fast', async () => {
  for (const env of [
    { MCP_URL: 'https://x.example/mcp', CF_ACCESS_CLIENT_ID: 'only-id' },
    { MCP_URL: 'http://evil.example/mcp' },
    { MCP_URL: 'https://x.example/mcp', MCP_MODE: 'nope' },
    { MCP_URL: 'https://x.example/mcp', READ_CALLS: '{not json' },
    {},
  ]) {
    const r = await exec(SMOKE, env);
    assert.equal(r.code, 1, JSON.stringify(env));
    assert.match(r.out, /::error::/);
  }
});

test('a hanging server fails with a timeout (no infinite wait)', async () => {
  const r = await smoke({ hang: true }, { ...creds, TIMEOUT_MS: '300', REQUIRE_ANON_REJECT: 'false' });
  assert.equal(r.code, 1);
  assert.match(r.out, /timed out after 300 ms/);
});

test('portal-dynamic: query + execute exposed, upstream discovered via portal_query_tools, read-only call through portal_execute', async () => {
  const r = await smoke({ dynamic: true }, { ...creds, MCP_MODE: 'portal-dynamic', EXPECT_TOOLS: 'health,search_dashboards', READ_CALLS: '[{"tool":"health","expect":"executed health"}]' });
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, /dynamic portal exposes portal_query_tools \+ portal_execute/);
  assert.match(r.out, /portal_query_tools discovered 2 expected upstream tool/);
  assert.match(r.out, /read-only portal_execute -> health/);
});

test('portal-dynamic: missing portal_execute, wrong tool names, and an undiscoverable tool fail loudly', async () => {
  let r = await smoke({ dynamic: true, portalExecute: false }, { ...creds, MCP_MODE: 'portal-dynamic' });
  assert.equal(r.code, 1);
  assert.match(r.out, /dynamic portal tool 'portal_execute' is missing/);
  r = await smoke({ dynamic: true }, { ...creds, MCP_MODE: 'portal-dynamic', DYNAMIC_TOOLS: 'query,execute' });
  assert.equal(r.code, 1);
  assert.match(r.out, /'query' is missing/);
  r = await smoke({ dynamic: true }, { ...creds, MCP_MODE: 'portal-dynamic', EXPECT_TOOLS: 'ghost_tool' });
  assert.equal(r.code, 1);
  assert.match(r.out, /did not discover expected upstream tool 'ghost_tool'/);
});

test('portal (static) mode against the non-dynamic URL does not see the dynamic tools', async () => {
  const r = await smoke({ dynamic: true }, { ...creds, MCP_MODE: 'portal', EXPECT_TOOLS: 'health' });
  assert.equal(r.code, 0, r.out);
});

// ---- capability sync ----
const syncEnv = (api, extra = {}) => ({ CLOUDFLARE_API_TOKEN: 'cf-token-SECRET-0001', CLOUDFLARE_ACCOUNT_ID: 'a'.repeat(32), MCP_SERVER_IDS: 'srv-one', CF_API_BASE: api.base, SYNC_RETRY_MS: '10', ...extra });
const ready = { status: 200, body: { success: true, errors: [], result: { status: 'ready', tools: [{ name: 'health' }] } } };

test('sync: POSTs the documented endpoint with a bearer token and passes on ready; token masked, never printed', async () => {
  const api = await startMockApi([ready]);
  const r = await exec(SYNC, syncEnv(api, { MCP_SERVER_IDS: 'srv-one, srv-two' }));
  api.close();
  assert.equal(r.code, 0, r.out);
  assert.deepEqual(api.calls.map((c) => [c.method, c.path]), [
    ['POST', `/client/v4/accounts/${'a'.repeat(32)}/access/ai-controls/mcp/servers/srv-one/sync`],
    ['POST', `/client/v4/accounts/${'a'.repeat(32)}/access/ai-controls/mcp/servers/srv-two/sync`],
  ]);
  assert.equal(api.calls[0].auth, 'Bearer cf-token-SECRET-0001');
  assert.ok(!r.out.replace('::add-mask::cf-token-SECRET-0001', '').includes('cf-token-SECRET-0001'));
});

test('sync: waiting is retried until ready; a retryable error is retried; a stale/error status fails loudly', async () => {
  let api = await startMockApi([{ status: 200, body: { success: true, result: { status: 'waiting' } } }, ready]);
  let r = await exec(SYNC, syncEnv(api));
  api.close();
  assert.equal(r.code, 0, r.out);
  assert.equal(api.calls.length, 2);
  api = await startMockApi([{ status: 200, body: { success: true, result: { status: 'error', error: 'upstream down', error_details: { retryable: true, is_upstream: true, status_code: 502 } } } }, ready]);
  r = await exec(SYNC, syncEnv(api));
  api.close();
  assert.equal(r.code, 0, r.out);
  api = await startMockApi([{ status: 200, body: { success: true, result: { status: 'error', error: 'bad', error_details: { retryable: false, is_upstream: false, cause: 'dns' } } } }]);
  r = await exec(SYNC, syncEnv(api));
  api.close();
  assert.equal(r.code, 1);
  assert.equal(api.calls.length, 1, 'a non-retryable error is not retried');
  assert.match(r.out, /sync of MCP server srv-one failed: status error: bad; cause: dns.*could not reach/);
  api = await startMockApi([{ status: 200, body: { success: true, result: { status: 'stale' } } }]);
  r = await exec(SYNC, syncEnv(api));
  api.close();
  assert.equal(r.code, 1);
  assert.match(r.out, /status stale/);
});

test('sync: HTTP 403 explains the missing permission; success=false and a malformed answer fail; one failing server fails the run but all are attempted', async () => {
  let api = await startMockApi([{ status: 403, body: { success: false, errors: [{ code: 10000, message: 'Authentication error' }] } }]);
  let r = await exec(SYNC, syncEnv(api));
  api.close();
  assert.equal(r.code, 1);
  assert.match(r.out, /HTTP 403.*MCP Portals Write/);
  api = await startMockApi([{ status: 200, body: { nonsense: true } }]);
  r = await exec(SYNC, syncEnv(api));
  api.close();
  assert.equal(r.code, 1);
  api = await startMockApi([{ status: 500, body: { success: false } }, ready]);
  r = await exec(SYNC, syncEnv(api, { MCP_SERVER_IDS: 'a1 b2', SYNC_ATTEMPTS: '1' }));
  api.close();
  assert.equal(r.code, 1);
  assert.equal(api.calls.length, 2, 'the second server is still synced');
  assert.match(r.out, /✅|ok   synced b2/);
});

test('sync: invalid configuration fails fast (no request)', async () => {
  for (const env of [{ CLOUDFLARE_API_TOKEN: '' }, { MCP_SERVER_IDS: '../x' }, { CLOUDFLARE_ACCOUNT_ID: 'short' }, { CF_API_BASE: 'http://evil.example' }]) {
    const api = await startMockApi([ready]);
    const r = await exec(SYNC, syncEnv(api, env));
    api.close();
    assert.equal(r.code, 1, JSON.stringify(env));
    assert.equal(api.calls.length, 0);
  }
});

test('L1/L2: an invalid service token that is NOT refused fails the run', async () => {
  const r = await smoke({ anyToken: true }, { ...creds });
  assert.equal(r.code, 1);
  assert.match(r.out, /INVALID service token was NOT refused/);
});

test('L2: tools that must be absent (cross-portal / per-user-OAuth) fail when exposed; pass when absent', async () => {
  let r = await smoke({ tools: ['health', 'get_nutrition_context'] }, { ...creds, EXPECT_TOOLS: 'health', EXPECT_ABSENT_TOOLS: 'get_nutrition_context' });
  assert.equal(r.code, 1);
  assert.match(r.out, /'get_nutrition_context' must NOT be exposed/);
  r = await smoke({}, { ...creds, EXPECT_TOOLS: 'health', EXPECT_ABSENT_TOOLS: 'get_nutrition_context,re:^diet_' });
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, /2 absent as required/);
});

test('L2 dynamic: a tool that must be absent is not discoverable through portal_query_tools', async () => {
  let r = await smoke({ dynamic: true }, { ...creds, MCP_MODE: 'portal-dynamic', EXPECT_ABSENT_TOOLS: 'health' });
  assert.equal(r.code, 1);
  assert.match(r.out, /discovered 'health', which must not be visible/);
  r = await smoke({ dynamic: true, hidden: true }, { ...creds, MCP_MODE: 'portal-dynamic', EXPECT_ABSENT_TOOLS: 'health' });
  assert.equal(r.code, 0, r.out);
});

test('L3: sync requires a ready server that really lists tools (0 tools / no tool list fail; min 0 disables)', async () => {
  for (const result of [{ status: 'ready', tools: [] }, { status: 'ready' }]) {
    const api = await startMockApi([{ status: 200, body: { success: true, result } }]);
    const r = await exec(SYNC, syncEnv(api));
    api.close();
    assert.equal(r.code, 1);
    assert.match(r.out, /status ready but .*tool.*need >= 1/);
  }
  const api = await startMockApi([{ status: 200, body: { success: true, result: { status: 'ready' } } }]);
  const r = await exec(SYNC, syncEnv(api, { SYNC_MIN_TOOLS: '0' }));
  api.close();
  assert.equal(r.code, 0, r.out);
});
