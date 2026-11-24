#!/usr/bin/env node
// Reusable Access-authenticated MCP smoke: ONE implementation for an upstream MCP endpoint and for an MCP portal (static or dynamic).
// Zero dependencies (Node >= 20). Configuration is environment only; nothing secret is ever printed.
//
//   MCP_URL                 required. Full MCP endpoint (https; http only for localhost, for the mock-server tests).
//   MCP_MODE                upstream (default) | portal | portal-dynamic. portal-dynamic appends ?optimize_context=search_and_execute.
//   CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET   optional pair, injected as CF-Access-Client-Id / CF-Access-Client-Secret on every
//                           authenticated request, masked with ::add-mask::. One without the other fails (never a silent anonymous run).
//   REQUIRE_ANON_REJECT     true (default) | false. An anonymous request must be refused (401/403/redirect to Access): proves there is no Bypass.
//   EXPECT_TOOLS            comma/newline list. Every entry must be exposed by tools/list (exact name; `re:<regex>` for a pattern).
//                           Not used in portal-dynamic mode (upstream tools are hidden there; they are discovered via portal_query_tools).
//   EXPECT_ABSENT_TOOLS     comma/newline list (same syntax). None may be exposed: no cross-portal exposure, and per-user-OAuth tools (Diet Manager)
//                           are expected to be ABSENT from a machine (service-token) session.
//   CHECK_BAD_CREDS         true (default when credentials are set) | false. A syntactically valid but WRONG service token must be refused.
//   EXPECT_SERVER_NAME      optional regex the initialize serverInfo.name must match.
//   READ_CALLS              optional JSON array of safe read-only calls: [{"tool":"health","arguments":{},"expect":"substring","server":"anas-mcp"}].
//                           Through a portal the tool is `{server_id}_{tool}`: give `server` (or the full name in `tool`).
//                           In portal-dynamic mode they run through portal_execute.
//   DYNAMIC_TOOLS           portal-dynamic only: comma list of the two portal tools (default portal_query_tools,portal_execute).
//   TIMEOUT_MS (15000)  RETRIES (2: transient network / 5xx only)  RETRY_DELAY_MS (3000)  LABEL (heading of the step summary)
// Exit 0 = every assertion passed; 1 = an assertion failed (message names the failing step, never a credential).
import process from 'node:process';
import console from 'node:console';
import { appendFileSync } from 'node:fs';
import { URL } from 'node:url';

const PROTOCOLS = ['2025-11-25', '2025-06-18', '2025-03-26'];
const MAX_BYTES = 1024 * 1024;
const ACCESS_HOST = /(^|\.)cloudflareaccess\.com$/i;

class SmokeError extends Error {}
const must = (cond, msg) => {
  if (!cond) throw new SmokeError(msg);
};
const list = (s) =>
  String(s ?? '')
    .split(/[\n,]/)
    .map((x) => x.trim())
    .filter(Boolean);
const bool = (s, d) => (s === undefined || s === '' ? d : /^(1|true|yes)$/i.test(String(s)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function parseConfig(env) {
  const mode = env.MCP_MODE || 'upstream';
  must(['upstream', 'portal', 'portal-dynamic'].includes(mode), `MCP_MODE must be upstream|portal|portal-dynamic (got '${mode}')`);
  must(env.MCP_URL, 'MCP_URL is required');
  let url;
  try {
    url = new URL(env.MCP_URL);
  } catch {
    throw new SmokeError('MCP_URL is not a valid URL');
  }
  const local = ['127.0.0.1', 'localhost'].includes(url.hostname);
  must(url.protocol === 'https:' || (url.protocol === 'http:' && local), 'MCP_URL must use HTTPS');
  must(!url.username && !url.password && !url.hash, 'MCP_URL must not carry credentials or a fragment');
  if (mode === 'portal-dynamic') url.searchParams.set('optimize_context', 'search_and_execute');
  const id = env.CF_ACCESS_CLIENT_ID || '';
  const secret = env.CF_ACCESS_CLIENT_SECRET || '';
  must(Boolean(id) === Boolean(secret), 'Set BOTH CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET, or neither');
  must(!/[\r\n]/.test(id + secret), 'Access credentials must be single-line');
  let readCalls = [];
  if (env.READ_CALLS?.trim()) {
    try {
      readCalls = JSON.parse(env.READ_CALLS);
    } catch {
      throw new SmokeError('READ_CALLS is not valid JSON');
    }
    must(
      Array.isArray(readCalls) && readCalls.every((c) => c && typeof c.tool === 'string' && c.tool),
      'READ_CALLS must be an array of {"tool","arguments?","expect?"}'
    );
  }
  const dynamicTools = list(env.DYNAMIC_TOOLS || 'portal_query_tools,portal_execute');
  must(mode !== 'portal-dynamic' || dynamicTools.length === 2, 'DYNAMIC_TOOLS must name exactly two tools (query, execute)');
  return {
    mode,
    url,
    id,
    secret,
    anonReject: bool(env.REQUIRE_ANON_REJECT, true),
    expectTools: list(env.EXPECT_TOOLS),
    absentTools: list(env.EXPECT_ABSENT_TOOLS),
    badCreds: Boolean(id) && bool(env.CHECK_BAD_CREDS, true),
    serverName: env.EXPECT_SERVER_NAME ? new RegExp(env.EXPECT_SERVER_NAME) : null,
    readCalls,
    dynamicTools,
    timeoutMs: Number(env.TIMEOUT_MS || 15000),
    retries: Number(env.RETRIES ?? 2),
    retryDelayMs: Number(env.RETRY_DELAY_MS ?? 3000),
    label: env.LABEL || `${mode} smoke`,
  };
}

// Removes credentials (and anything header-shaped) from text before it can reach a log.
export function redactor(cfg) {
  const secrets = [cfg.id, cfg.secret].filter((s) => s && s.length >= 4);
  return (text) => {
    let out = String(text);
    for (const s of secrets) out = out.split(s).join('***');
    return out.replace(/(cf-access-client-(?:id|secret)\s*[:=]\s*)\S+/gi, '$1***');
  };
}

async function readBody(res, sse) {
  const reader = res.body?.getReader();
  if (!reader) return '';
  const dec = new TextDecoder();
  let text = '';
  let bytes = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      must(bytes <= MAX_BYTES, 'response exceeds the smoke size limit (1 MiB)');
      text += dec.decode(value, { stream: true });
      if (sse) {
        const frames = text.split(/\r?\n\r?\n/);
        text = frames.pop();
        for (const f of frames) {
          const data = f
            .split(/\r?\n/)
            .filter((l) => l.startsWith('data:'))
            .map((l) => l.slice(5).trimStart())
            .join('\n');
          if (data) return data;
        }
      }
    }
    return text + dec.decode();
  } finally {
    await reader.cancel().catch(() => {});
  }
}

export async function run(cfg, log = console.log, steps = []) {
  const redact = redactor(cfg);
  const authed = cfg.id ? { 'CF-Access-Client-Id': cfg.id, 'CF-Access-Client-Secret': cfg.secret } : {};
  const pass = (name) => {
    steps.push({ name, ok: true });
    log(`ok   ${name}`);
  };

  async function http(url, init) {
    let last;
    for (let i = 0; i <= cfg.retries; i++) {
      try {
        const res = await fetch(url, { ...init, redirect: 'manual', signal: AbortSignal.timeout(cfg.timeoutMs) });
        if (res.status >= 500 && i < cfg.retries) {
          await res.body?.cancel().catch(() => {});
          await sleep(cfg.retryDelayMs);
          continue;
        }
        return res;
      } catch (e) {
        last = e;
        if (i < cfg.retries) await sleep(cfg.retryDelayMs);
      }
    }
    const why = last?.name === 'TimeoutError' ? `timed out after ${cfg.timeoutMs} ms` : 'connection failed';
    throw new SmokeError(`${why} (${cfg.url.origin}${cfg.url.pathname})`);
  }

  // A short, credential-free explanation of a non-200 answer: Access challenge, JSON-RPC error or a body excerpt.
  async function explain(res) {
    const loc = res.headers.get('location') || '';
    let host = '';
    try {
      host = new URL(loc, cfg.url).hostname;
    } catch {
      /* not a URL */
    }
    if (res.status >= 300 && res.status < 400 && ACCESS_HOST.test(host))
      return 'redirected to Cloudflare Access login: the service token is missing, invalid, expired, or not allowed by the Access policy';
    let body = '';
    try {
      body = redact(await readBody(res, false)).replace(/\s+/g, ' ').slice(0, 300);
    } catch {
      /* body unreadable */
    }
    if (res.status === 401 || res.status === 403)
      return `refused (${res.status}); check the service token and its Access policy for this application${body ? `; body: ${body}` : ''}`;
    return body ? `body: ${body}` : 'no body';
  }

  // ---- 0. anonymous request must be refused (no Bypass) ----
  if (cfg.anonReject) {
    const res = await http(cfg.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 0, method: 'tools/list' }),
    });
    const body = await readBody(res, false).catch(() => '');
    must(
      [301, 302, 303, 307, 308, 401, 403].includes(res.status) && !/"tools"\s*:/.test(body),
      `anonymous request was NOT refused (HTTP ${res.status}): the endpoint must be Access-protected with no Bypass policy`
    );
    pass(`anonymous request refused (HTTP ${res.status})`);
  }

  // ---- 0b. a wrong (but well-formed) service token must be refused: invalid credentials never grant access ----
  if (cfg.badCreds) {
    const res = await http(cfg.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        'CF-Access-Client-Id': 'invalid-smoke-check.access',
        'CF-Access-Client-Secret': 'invalid-smoke-check-0000000000000000000000000000000000000000',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 0, method: 'tools/list' }),
    });
    const body = await readBody(res, false).catch(() => '');
    must(
      [301, 302, 303, 307, 308, 401, 403].includes(res.status) && !/"tools"\s*:/.test(body),
      `an INVALID service token was NOT refused (HTTP ${res.status}): the endpoint must reject wrong credentials`
    );
    pass(`invalid service token refused (HTTP ${res.status})`);
  }

  // ---- JSON-RPC helper ----
  let session;
  let protocol = PROTOCOLS[0];
  let nextId = 1;
  const baseHeaders = () => ({
    ...authed,
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
    'MCP-Protocol-Version': protocol,
    ...(session ? { 'Mcp-Session-Id': session } : {}),
  });
  async function rpc(method, params) {
    const id = nextId++;
    const res = await http(cfg.url, { method: 'POST', headers: baseHeaders(), body: JSON.stringify({ jsonrpc: '2.0', id, method, params }) });
    if (res.status !== 200) throw new SmokeError(`${method} failed: HTTP ${res.status}: ${await explain(res)}`);
    const ct = res.headers.get('content-type') || '';
    must(/^(application\/json|text\/event-stream)\b/i.test(ct), `${method}: response is neither JSON nor SSE (content-type '${ct.slice(0, 60)}')`);
    const raw = await readBody(res, /text\/event-stream/i.test(ct));
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      throw new SmokeError(`${method}: invalid JSON response: ${redact(raw).slice(0, 200)}`);
    }
    must(msg?.jsonrpc === '2.0' && msg.id === id, `${method}: not a JSON-RPC 2.0 response for this request`);
    must(!msg.error, `${method}: JSON-RPC error ${msg.error?.code}: ${redact(String(msg.error?.message ?? '')).slice(0, 200)}`);
    must(msg.result && typeof msg.result === 'object', `${method}: missing result`);
    if (method === 'initialize') session = res.headers.get('mcp-session-id') || undefined;
    return msg.result;
  }

  // ---- 1. initialize ----
  const init = await rpc('initialize', { protocolVersion: protocol, capabilities: {}, clientInfo: { name: 'firstsun-ci-smoke', version: '1.0.0' } });
  must(PROTOCOLS.includes(init.protocolVersion), `unsupported protocolVersion '${init.protocolVersion}'`);
  must(init.capabilities?.tools, 'server does not advertise the tools capability');
  if (cfg.serverName) must(cfg.serverName.test(init.serverInfo?.name ?? ''), `serverInfo.name '${init.serverInfo?.name}' does not match ${cfg.serverName}`);
  protocol = init.protocolVersion;
  const note = await http(cfg.url, { method: 'POST', headers: baseHeaders(), body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) });
  await note.body?.cancel().catch(() => {});
  must([200, 202, 204].includes(note.status), `notifications/initialized failed: HTTP ${note.status}`);
  pass(`initialize (${init.serverInfo?.name ?? 'unnamed'} ${init.serverInfo?.version ?? ''}, protocol ${protocol})`.replace(/ +\)/, ')'));

  // ---- 2. tools/list (paginated) ----
  const tools = new Map();
  let cursor;
  for (let page = 0; page < 20; page++) {
    const r = await rpc('tools/list', cursor ? { cursor } : {});
    must(Array.isArray(r.tools) && r.tools.every((t) => typeof t.name === 'string'), 'tools/list: invalid tool catalogue');
    for (const t of r.tools) tools.set(t.name, t);
    cursor = r.nextCursor;
    if (!cursor) break;
    must(page < 19, 'tools/list: pagination limit exceeded');
  }
  // Portal tool names are `{server_id}_{tool}` (split on the FIRST underscore; server ids contain hyphens, e.g. `kavita-mcp_search`). In a portal mode a spec
  // matches the full name or the bare tool part; a direct Worker (upstream mode) exposes bare names only. `re:` patterns are tried against both forms, so
  // `re:^diet-manager-mcp_` (prefix) and `re:health$` both work.
  const portal = cfg.mode !== 'upstream';
  const forms = (n) => (portal && n.includes('_') ? [n, n.slice(n.indexOf('_') + 1)] : [n]);
  const has = (spec) => {
    const re = spec.startsWith('re:') ? new RegExp(spec.slice(3)) : null;
    return [...tools.keys()].some((n) => forms(n).some((f) => (re ? re.test(f) : f === spec)));
  };
  // The name to call: an explicit `server` ({"server":"kavita-mcp","tool":"search"}) makes `kavita-mcp_search`; otherwise the tool as given.
  const fullName = (c) => (c.server ? `${c.server}_${c.tool}` : c.tool);
  const names = () => [...tools.keys()].sort().slice(0, 40).join(', ');

  const callTool = async (name, args) => {
    const r = await rpc('tools/call', { name, arguments: args ?? {} });
    must(r.isError !== true, `tools/call ${name}: the tool reported an error`);
    return r;
  };
  const textOf = (r) => JSON.stringify(r.content ?? r.structuredContent ?? r);

  if (cfg.mode !== 'portal-dynamic') {
    for (const spec of cfg.expectTools) must(has(spec), `expected tool '${spec}' is not exposed; tools/list returned: ${names() || '(none)'}`);
    for (const spec of cfg.absentTools) must(!has(spec), `tool '${spec}' must NOT be exposed here (cross-portal / per-user-OAuth exposure)`);
    pass(`tools/list (${tools.size} tools${cfg.expectTools.length ? `; ${cfg.expectTools.length} expected present` : ''}${cfg.absentTools.length ? `; ${cfg.absentTools.length} absent as required` : ''})`);
    for (const c of cfg.readCalls) {
      must(tools.has(fullName(c)), `read-only call '${fullName(c)}': tool is not exposed; tools/list returned: ${names()}`);
      const r = await callTool(fullName(c), c.arguments);
      if (c.expect) must(textOf(r).includes(c.expect), `read-only call '${c.tool}': result does not contain the expected text`);
      pass(`read-only tools/call ${fullName(c)}`);
    }
  } else {
    const [qName, xName] = cfg.dynamicTools;
    for (const n of cfg.dynamicTools)
      must(tools.has(n), `dynamic portal tool '${n}' is missing; tools/list returned: ${names() || '(none)'} (check DYNAMIC_TOOLS against the current Cloudflare docs)`);
    for (const spec of cfg.absentTools) must(!has(spec), `tool '${spec}' must NOT be listed directly in dynamic mode (upstream tools are hidden there)`);
    pass(`dynamic portal exposes ${qName} + ${xName} (${tools.size} tools listed)`);
    const pick = (tool, keys) => {
      const props = Object.keys(tool.inputSchema?.properties ?? {});
      const key = keys.find((k) => props.includes(k));
      must(key, `${tool.name}: cannot map arguments; its inputSchema properties are [${props.join(', ')}]`);
      return key;
    };
    // Discovery: portal_query_tools must find every expected upstream tool (regex over name/description).
    const wanted = cfg.expectTools.filter((s) => !s.startsWith('re:'));
    if (wanted.length) {
      const qt = tools.get(qName);
      const qKey = pick(qt, ['pattern', 'query', 'regex', 'search']);
      const found = textOf(await callTool(qName, { [qKey]: wanted.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') }));
      for (const w of wanted) must(found.includes(w), `${qName} did not discover expected upstream tool '${w}'`);
      pass(`${qName} discovered ${wanted.length} expected upstream tool(s)`);
    }
    const unwanted = cfg.absentTools.filter((s) => !s.startsWith('re:'));
    if (unwanted.length) {
      const qKey = pick(tools.get(qName), ['pattern', 'query', 'regex', 'search']);
      const found = textOf(await callTool(qName, { [qKey]: unwanted.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') }));
      for (const w of unwanted) must(!found.includes(w), `${qName} discovered '${w}', which must not be visible to this session`);
      pass(`${qName} does not expose ${unwanted.length} tool(s) that must be absent`);
    }
    for (const c of cfg.readCalls) {
      const xt = tools.get(xName);
      const nKey = pick(xt, ['tool', 'tool_name', 'toolName', 'name']);
      const aKey = Object.keys(xt.inputSchema?.properties ?? {}).find((k) => ['arguments', 'args', 'params', 'input'].includes(k));
      const r = await callTool(xName, { [nKey]: fullName(c), ...(aKey ? { [aKey]: c.arguments ?? {} } : {}) });
      if (c.expect) must(textOf(r).includes(c.expect), `${xName} ${c.tool}: result does not contain the expected text`);
      pass(`read-only ${xName} -> ${fullName(c)}`);
    }
  }
  return steps;
}

export function summarize(cfg, steps, error, redact) {
  const rows = steps.map((s) => `- ✅ ${s.name}`);
  if (error) rows.push(`- ❌ ${redact(error)}`);
  return [`### ${cfg.label} (service-token, ${cfg.url.origin}${cfg.url.pathname})`, ...rows, ''].join('\n');
}

async function main() {
  let cfg;
  try {
    cfg = parseConfig(process.env);
  } catch (e) {
    console.error(`::error::${e.message}`);
    process.exit(1);
  }
  for (const s of [cfg.id, cfg.secret]) if (s) console.log(`::add-mask::${s}`);
  const redact = redactor(cfg);
  const steps = [];
  let error;
  try {
    await run(cfg, console.log, steps);
  } catch (e) {
    error = e instanceof SmokeError ? e.message : 'unexpected failure; check connectivity and the MCP endpoint';
    console.error(`::error title=${cfg.label}::${redact(error)}`);
    process.exitCode = 1;
  }
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, summarize(cfg, steps, error, redact));
  if (!error) console.log(`${cfg.label}: passed (${steps.length} checks)`);
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
