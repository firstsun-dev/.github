// Mock MCP server (Streamable HTTP, JSON or SSE answers) for the smoke tests. Models: an Access gate (service token), an optional Bypass (anonymous
// allowed), an Access redirect, a portal with the dynamic `optimize_context=search_and_execute` mode, and a hanging server.
import { createServer } from 'node:http';

export const TOKEN = { id: 'mock-id-0123456789.access', secret: 'mock-secret-s3cr3t-DO-NOT-LEAK' };

export function startMock(opts = {}) {
  const o = { gate: true, bypass: false, redirect: false, sse: false, hang: false, tools: ['health', 'search_dashboards'], dynamic: false, portalExecute: true, ...opts };
  const seen = [];
  const server = createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const url = new URL(req.url, 'http://x');
      const authed = o.anyToken ? Boolean(req.headers['cf-access-client-id']) : req.headers['cf-access-client-id'] === TOKEN.id && req.headers['cf-access-client-secret'] === TOKEN.secret;
      seen.push({ authed, path: req.url });
      if (o.hang) return; // never answers
      if (o.redirect && !authed) {
        res.writeHead(302, { location: 'https://firstsun.cloudflareaccess.com/cdn-cgi/access/login/x' }).end();
        return;
      }
      if (o.gate && !authed && !o.bypass) {
        res.writeHead(403, { 'content-type': 'text/plain' }).end('forbidden: not allowed by Access policy');
        return;
      }
      let msg = {};
      try {
        msg = JSON.parse(raw || '{}');
      } catch {
        res.writeHead(400).end('bad json');
        return;
      }
      if (msg.id === undefined) {
        res.writeHead(202).end();
        return;
      }
      const dynamic = o.dynamic && url.searchParams.get('optimize_context') === 'search_and_execute';
      const reply = (result) => {
        const body = JSON.stringify({ jsonrpc: '2.0', id: msg.id, result });
        if (o.sse) res.writeHead(200, { 'content-type': 'text/event-stream', 'mcp-session-id': 'sess-1' }).end(`event: message\ndata: ${body}\n\n`);
        else res.writeHead(200, { 'content-type': 'application/json', 'mcp-session-id': 'sess-1' }).end(body);
      };
      const text = (t, isError = false) => reply({ content: [{ type: 'text', text: t }], isError });
      const schema = (props) => ({ type: 'object', properties: props });
      if (msg.method === 'initialize') {
        return reply({ protocolVersion: msg.params.protocolVersion, capabilities: { tools: {} }, serverInfo: { name: o.serverName ?? 'Mock MCP', version: '1.0.0' } });
      }
      if (msg.method === 'tools/list') {
        const list = dynamic
          ? [
              { name: 'portal_query_tools', inputSchema: schema({ pattern: { type: 'string' } }) },
              ...(o.portalExecute ? [{ name: 'portal_execute', inputSchema: schema({ tool_name: { type: 'string' }, arguments: { type: 'object' } }) }] : []),
            ]
          : o.tools.map((name) => ({ name, inputSchema: schema({}) }));
        return reply({ tools: list });
      }
      if (msg.method === 'tools/call') {
        const { name, arguments: a } = msg.params;
        if (dynamic && name === 'portal_query_tools') return text(JSON.stringify((o.hidden ? [] : o.tools).filter((t) => new RegExp(a.pattern).test(t))));
        if (dynamic && name === 'portal_execute') return o.tools.includes(a.tool_name) ? text(`executed ${a.tool_name}: ok`) : text('unknown tool', true);
        if (name === 'health') return text(o.healthError ? 'boom' : '{"status":"ok"}', Boolean(o.healthError));
        return text('{}');
      }
      res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Method not found' } }));
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ url: `http://127.0.0.1:${server.address().port}`, seen, close: () => server.closeAllConnections?.() || server.close() })));
}

// Mock of the Cloudflare MCP server sync endpoint. `script` is an array of {status, body} answers consumed per request (last one repeats).
export function startMockApi(script) {
  const calls = [];
  const server = createServer((req, res) => {
    calls.push({ method: req.method, path: req.url, auth: req.headers.authorization });
    const step = script[Math.min(calls.length - 1, script.length - 1)];
    res.writeHead(step.status, { 'content-type': 'application/json' }).end(JSON.stringify(step.body));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ base: `http://127.0.0.1:${server.address().port}/client/v4`, calls, close: () => server.closeAllConnections?.() || server.close() })));
}
