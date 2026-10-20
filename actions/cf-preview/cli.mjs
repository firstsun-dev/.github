#!/usr/bin/env node
// CLI used by _cf-worker-v2.yml. Prints key=value lines (GITHUB_OUTPUT-safe) or exits non-zero.
import { readFileSync } from 'node:fs';
import {
  assertWranglerSupported, auditPreviewConfig, classifyEvent, extractPreviewUrl,
  isStale, previewIdentity, readConfig,
} from './lib.mjs';

const [cmd, ...rest] = process.argv.slice(2);
const opt = (n, d = '') => {
  const i = rest.indexOf(`--${n}`);
  return i >= 0 ? rest[i + 1] : d;
};
const list = (s) => s.split(/[\s,]+/).filter(Boolean);
const out = (o) => { for (const [k, v] of Object.entries(o)) console.log(`${k}=${v}`); };

try {
  switch (cmd) {
    case 'identity': {
      const id = previewIdentity({ repo: opt('repo'), worker: opt('worker'), ref: opt('ref') });
      out({ preview_name: id.name, preview_slug: id.slug, preview_hash: id.hash });
      break;
    }
    case 'wrangler-check':
      assertWranglerSupported(opt('version'));
      out({ wrangler_ok: 'true' });
      break;
    case 'extract-url':
      out({ preview_url: extractPreviewUrl(readFileSync(opt('file'), 'utf8')) });
      break;
    case 'classify': {
      const r = classifyEvent({
        event: opt('event'), ref: opt('ref'), strategy: opt('strategy'),
        isFork: opt('fork') === 'true', deletedRefType: opt('ref-type'),
        devRef: opt('dev-ref'), dispatchTarget: opt('dispatch-target'),
      });
      out({ action: r.action, reason: r.reason });
      break;
    }
    case 'preflight': {
      const { errors, warnings } = auditPreviewConfig(readConfig(opt('config')), {
        allow: list(opt('allow')), denyResources: list(opt('deny')),
      });
      for (const w of warnings) console.log(`::warning::${w}`);
      for (const e of errors) console.log(`::error::${e}`);
      if (errors.length) process.exit(1);
      out({ preflight: 'ok' });
      break;
    }
    case 'stale':
      out({ stale: String(isStale({ runSha: opt('sha'), branchTipSha: opt('tip') })) });
      break;
    default:
      console.error('usage: cli.mjs identity|wrangler-check|extract-url|classify|preflight|stale');
      process.exit(2);
  }
} catch (e) {
  console.log(`::error::${e.message}`);
  process.exit(1);
}
