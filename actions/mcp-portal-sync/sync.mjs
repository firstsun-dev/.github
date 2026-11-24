#!/usr/bin/env node
// Capability sync of registered MCP portal servers after a successful PRODUCTION deploy.
//   POST /accounts/{account_id}/access/ai-controls/mcp/servers/{server_id}/sync     (Cloudflare API; permission `MCP Portals Write`)
//   Response: {success, errors, result:{status: waiting|ready|stale|error, error?, error_details?:{cause,is_upstream,mcp_code,retryable,status_code}}}
// Fails loudly: anything but success=true AND status=ready is an error. `waiting` and a `retryable` error are retried (bounded); stale / error /
// HTTP failure / a malformed answer fail the run. Nothing is ever ignored.
//   CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID   required (never printed; the token is masked)
//   MCP_SERVER_IDS        required, comma / space / newline separated Cloudflare MCP server ids
//   SYNC_ATTEMPTS (4)  SYNC_RETRY_MS (5000)  SYNC_TIMEOUT_MS (60000)  CF_API_BASE (default https://api.cloudflare.com/client/v4; tests: localhost http)
import process from 'node:process';
import console from 'node:console';
import { appendFileSync } from 'node:fs';
import { URL } from 'node:url';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function parse(env) {
  const need = (k) => {
    if (!env[k]) throw new Error(`${k} is required`);
    return env[k];
  };
  const token = need('CLOUDFLARE_API_TOKEN');
  const account = need('CLOUDFLARE_ACCOUNT_ID');
  if (!/^[0-9a-f]{32}$/i.test(account)) throw new Error('CLOUDFLARE_ACCOUNT_ID must be a 32-character hex id');
  const ids = need('MCP_SERVER_IDS').split(/[\s,]+/).filter(Boolean);
  if (!ids.length) throw new Error('MCP_SERVER_IDS is empty');
  for (const id of ids) if (!/^[A-Za-z0-9_-]{1,32}$/.test(id)) throw new Error('MCP_SERVER_IDS contains an invalid server id (1-32 of A-Z a-z 0-9 _ -)');
  const base = new URL(env.CF_API_BASE || 'https://api.cloudflare.com/client/v4');
  if (!(base.protocol === 'https:' || (base.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(base.hostname)))) throw new Error('CF_API_BASE must be HTTPS');
  return {
    token,
    account,
    ids: [...new Set(ids)],
    base: base.href.replace(/\/$/, ''),
    attempts: Number(env.SYNC_ATTEMPTS || 4),
    retryMs: Number(env.SYNC_RETRY_MS ?? 5000),
    timeoutMs: Number(env.SYNC_TIMEOUT_MS || 60000),
  };
}

// One server: returns {id, status} or throws an Error whose message holds no credential.
export async function syncOne(cfg, id, log = console.log) {
  const url = `${cfg.base}/accounts/${cfg.account}/access/ai-controls/mcp/servers/${id}/sync`;
  let why = 'no attempt made';
  for (let n = 1; n <= cfg.attempts; n++) {
    let retry = false;
    try {
      const res = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${cfg.token}` }, signal: AbortSignal.timeout(cfg.timeoutMs) });
      const text = await res.text();
      let body;
      try {
        body = JSON.parse(text);
      } catch {
        body = null;
      }
      const r = body?.result;
      if (!res.ok || body?.success !== true) {
        const msg = (body?.errors ?? []).map((e) => `${e.code ?? ''} ${String(e.message ?? '').slice(0, 120)}`.trim()).join('; ');
        why = `HTTP ${res.status}${msg ? `: ${msg}` : ''}`;
        retry = res.status === 429 || res.status >= 500;
        if (res.status === 401 || res.status === 403) why += ' (the API token needs the "MCP Portals Write" permission on this account)';
      } else if (r?.status === 'ready') {
        log(`ok   synced ${id}: ready`);
        return { id, status: 'ready' };
      } else if (r?.status === 'waiting') {
        why = 'status waiting (sync still in progress)';
        retry = true;
      } else {
        const d = r?.error_details ?? {};
        why = `status ${r?.status ?? 'missing'}${r?.error ? `: ${String(r.error).slice(0, 160)}` : ''}${d.cause ? `; cause: ${String(d.cause).slice(0, 160)}` : ''}${
          d.status_code ? `; upstream HTTP ${d.status_code}` : ''
        }${d.is_upstream === false ? '; the portal could not reach the MCP server (check its URL / Access policy)' : ''}`;
        retry = d.retryable === true;
      }
    } catch (e) {
      why = e?.name === 'TimeoutError' ? `timed out after ${cfg.timeoutMs} ms` : 'request failed (network error)';
      retry = true;
    }
    if (!retry || n === cfg.attempts) break;
    log(`warn sync ${id} attempt ${n}/${cfg.attempts}: ${why}; retrying`);
    await sleep(cfg.retryMs);
  }
  throw new Error(`sync of MCP server ${id} failed: ${why}`);
}

async function main() {
  let cfg;
  try {
    cfg = parse(process.env);
  } catch (e) {
    console.error(`::error::${e.message}`);
    process.exit(1);
  }
  console.log(`::add-mask::${cfg.token}`);
  const rows = [];
  let failed = 0;
  for (const id of cfg.ids) {
    try {
      await syncOne(cfg, id);
      rows.push(`- ✅ \`${id}\` synced (ready)`);
    } catch (e) {
      failed++;
      console.error(`::error title=Portal capability sync::${e.message.split(cfg.token).join('***')}`);
      rows.push(`- ❌ \`${id}\`: ${e.message.split(cfg.token).join('***')}`);
    }
  }
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `### Portal capability sync\n${rows.join('\n')}\n`);
  if (failed) process.exit(1);
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
