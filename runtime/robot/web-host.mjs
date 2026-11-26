import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { localCrypto } from './local-crypto.mjs';

export function createWebHost({ invoke, uiRoot }) {
  const routes = new Set(['/health', '/robot/settings', '/robot/state', '/robot/start', '/robot/input',
    '/robot/stop', '/robot/close', '/robot/export', '/robot/records']);
  return http.createServer(async (request, response) => {
    const send = (code, body, type='application/json') => {
      response.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin',
        'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; connect-src 'self'; frame-ancestors 'none'" });
      response.end(type === 'application/json' ? JSON.stringify(body) : body);
    };
    try {
      const expected = `127.0.0.1:${response.socket.localPort}`;
      if (![expected, `localhost:${response.socket.localPort}`].includes(request.headers.host)) return send(403, { error: 'Invalid host' });
      const url = new URL(request.url, 'http://'+request.headers.host);
      if (request.headers.origin && request.headers.origin !== url.origin) return send(403, { error: 'Cross-origin request rejected' });
      if (request.headers['sec-fetch-site'] === 'cross-site') return send(403, { error: 'Cross-site request rejected' });
      if (url.pathname.startsWith('/api/')) {
        const route = url.pathname.slice(4);
        if (!routes.has(route) || !['GET','POST'].includes(request.method)) return send(404, { error: 'Unknown robot route' });
        if (request.method === 'POST' && !request.headers['content-type']?.startsWith('application/json')) return send(415, { error: 'JSON required' });
        const chunks = []; let size = 0;
        for await (const chunk of request) {
          size += chunk.length;
          if (size > 26*1024*1024) return send(413, { error: 'Request too large' });
          chunks.push(chunk);
        }
        const body = size ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined;
        return send(200, await invoke({ path: route, method: request.method, body }));
      }
      if (!['GET','HEAD'].includes(request.method)) return send(405, { error: 'GET or HEAD required' });
      const relative = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
      const file = path.resolve(uiRoot, '.'+relative);
      if (!file.startsWith(path.resolve(uiRoot)+path.sep)) return send(403, { error: 'Invalid path' });
      const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' }[path.extname(file)];
      if (!mime) return send(404, { error: 'Unknown asset' });
      try { const asset = await fs.readFile(file); return send(200, request.method === 'HEAD' ? '' : asset, mime); }
      catch (error) { if (error.code === 'ENOENT') return send(404, { error: 'Missing asset' }); throw error; }
    } catch (error) { send(400, { error: error.message }); }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  process.env.EMBUDDIES_DATA_DIR ||= path.join(process.env.HOME || root, '.local/share/agentic-robotics/data');
  globalThis.embuddiesCrypto = await localCrypto(process.env.EMBUDDIES_DATA_DIR);
  const app = await import('./application.mjs');
  await app.startApplication();
  const server = createWebHost({ invoke: app.invokeApplication, uiRoot: path.join(root, 'frontend/dist') });
  server.listen(Number(process.env.ROBOT_WEB_PORT || 8787), '127.0.0.1', () => console.log(JSON.stringify({ web: server.address() })));
  for (const signal of ['SIGINT','SIGTERM']) process.on(signal, async () => {
    server.close(); await app.closeApplication(); process.exit(0);
  });
}
