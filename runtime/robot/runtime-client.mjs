import { boundedOperation } from './deadline.mjs';

export class RuntimeClient {
  constructor(url, token, fetcher = fetch) {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('CaP 地址必须是固定 HTTP(S) 服务地址');
    this.base = url.replace(/\/$/, '');
    this.token = token;
    this.fetcher = fetcher;
  }
  async request(path, body, signal, timeout = 15_000) {
    return boundedOperation(async operationSignal => {
    const response = await this.fetcher(this.base + path, { method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: operationSignal });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      const error = new Error(result.error || `CaP 服务请求失败（${response.status}）`);
      error.code = result.code || (response.status === 409 && /stale/i.test(result.error || '') ? 'STALE_REVISION' : 'RUNTIME_REQUEST_FAILED');
      throw error;
    }
    return response.json();
    }, { signal, milliseconds: Math.min(15_000, timeout), label: 'Runtime request' });
  }
  async bytes(path, timeout = 15_000) {
    return boundedOperation(async signal => {
    const response = await this.fetcher(this.base + path, { headers: { Authorization: `Bearer ${this.token}` }, signal });
    if (!response.ok) throw new Error(`读取执行产物失败（${response.status}）`);
    return Buffer.from(await response.arrayBuffer());
    }, { milliseconds: Math.min(600_000, timeout), label: 'Runtime artifact' });
  }
  async events(episodeId, after, signal, callback) {
    const response = await this.fetcher(`${this.base}/episodes/${episodeId}/events?after=${after}`, {
      headers: { Authorization: `Bearer ${this.token}` }, signal });
    if (!response.ok) throw new Error(`事件连接失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true }).replace(/\r/g, '');
        let boundary;
        while ((boundary = pending.indexOf('\n\n')) >= 0) {
          const frame = pending.slice(0, boundary);
          pending = pending.slice(boundary + 2);
          const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
          if (data) await callback(JSON.parse(data));
        }
      }
    } finally { await reader.cancel().catch(() => {}); }
  }
}
