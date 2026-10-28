// Real GitHub / Cloudflare clients for gc.mjs. Tokens are only ever placed in request headers or the child
// process environment; they are never logged.
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { GcAbort } from './gc.mjs';

const MAX_PAGES = 200;

export function githubClient({ repo, token, apiUrl = 'https://api.github.com', fetchImpl = fetch }) {
  const hdr = { authorization: `Bearer ${token}`, accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28', 'user-agent': 'cf-preview-gc' };
  async function req(path, init = {}) {
    const res = await fetchImpl(path.startsWith('http') ? path : `${apiUrl}${path}`, { ...init, headers: { ...hdr, ...(init.headers || {}) } });
    // Rate limit or missing permission: stop the whole run, never guess.
    if (res.status === 429 || res.status === 401 || (res.status === 403)) throw new GcAbort(`GitHub API ${res.status} on ${path.split('?')[0]} (rate limit or permission)`);
    return res;
  }
  async function json(path, init) {
    const res = await req(path, init);
    if (!res.ok) throw new Error(`GitHub API ${res.status} on ${path.split('?')[0]}`);
    return res;
  }
  async function paged(path) {
    const out = [];
    let url = path;
    for (let i = 0; url; i++) {
      if (i >= MAX_PAGES) throw new GcAbort(`GitHub enumeration of ${path.split('?')[0]} exceeded ${MAX_PAGES} pages: incomplete data`);
      const res = await json(url);
      out.push(...(await res.json()));
      const next = /<([^>]+)>;\s*rel="next"/.exec(res.headers.get('link') || '');
      url = next ? next[1] : null;
    }
    return out;
  }
  const enc = encodeURIComponent;
  return {
    listDeployments: () => paged(`/repos/${repo}/deployments?per_page=100`),
    async getLatestDeployment(env) {
      const res = await json(`/repos/${repo}/deployments?environment=${enc(env)}&per_page=1`);
      return (await res.json())[0] ?? null;
    },
    async listStatuses(id) {
      const res = await json(`/repos/${repo}/deployments/${id}/statuses?per_page=30`); // newest first
      return res.json();
    },
    async listBranches() {
      return (await paged(`/repos/${repo}/branches?per_page=100`)).map((b) => b.name);
    },
    async branchExists(name) {
      const res = await req(`/repos/${repo}/git/ref/heads/${name.split('/').map(enc).join('/')}`);
      if (res.status === 404) return false;
      if (res.ok) return true;
      throw new Error(`GitHub API ${res.status} checking branch ${name}`);
    },
    async recordCleaned(deploymentId, description) {
      await json(`/repos/${repo}/deployments/${deploymentId}/statuses`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ state: 'inactive', description: description.slice(0, 140) }),
      });
    },
  };
}

export function cloudflareClient({ token, wranglerCmd = ['npx', '--yes', 'wrangler@4.135.0'], fetchImpl = fetch, spawnImpl = spawn }) {
  const base = 'https://api.cloudflare.com/client/v4';
  const seg = (s) => encodeURIComponent(s);
  return {
    /** GET .../previews/<name>: 200 => exists, 404 => absent, anything else (401/403/429/5xx) => abort. */
    async previewExists({ accountId, worker, name }) {
      const res = await fetchImpl(`${base}/accounts/${seg(accountId)}/workers/workers/${seg(worker)}/previews/${seg(name)}`, {
        headers: { authorization: `Bearer ${token}` },
      });
      if (res.status === 404) return false;
      if (res.ok) {
        const body = await res.json().catch(() => null);
        const got = body?.result?.name;
        if (got !== undefined && got !== name) throw new Error(`Cloudflare returned Preview ${JSON.stringify(got)} for ${name}`);
        return true;
      }
      if (res.status === 401 || res.status === 403 || res.status === 429) throw new GcAbort(`Cloudflare API ${res.status} (permission or rate limit)`);
      throw new Error(`Cloudflare API ${res.status} reading Preview ${name}`);
    },
    /** `wrangler preview delete` with explicit Worker and account. Never `wrangler delete`. */
    deletePreview({ accountId, worker, name }) {
      return new Promise((resolve, reject) => {
        const [cmd, ...pre] = wranglerCmd;
        const child = spawnImpl(cmd, [...pre, 'preview', 'delete', '--name', name, '--worker-name', worker, '--skip-confirmation'], {
          cwd: mkdtempSync(join(tmpdir(), 'cf-gc-')),
          env: { ...process.env, CLOUDFLARE_API_TOKEN: token, CLOUDFLARE_ACCOUNT_ID: accountId },
          stdio: ['ignore', 'pipe', 'pipe'],
        });
        let err = '';
        child.stderr.on('data', (d) => { err += d; });
        child.on('error', reject);
        child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`wrangler preview delete exited ${code}: ${err.replaceAll(token, '***').slice(-300)}`))));
      });
    },
  };
}
