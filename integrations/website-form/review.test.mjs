import test from 'node:test';
import assert from 'node:assert/strict';
import { createHandler } from './api/review.mjs';

const valid = { email: 'engineer@example.com', company: 'Example', purpose: 'Support agent', actions: 'Refund customer', status: 'Building', concerns: ['Retry safety'], requestId: '11111111-1111-4111-8111-111111111111' };
const request = (body = valid, headers = {}) => ({ method: 'POST', headers: { origin: 'https://ctrlrun.dev', 'content-type': 'application/json', 'x-vercel-forwarded-for': '192.0.2.1', ...headers }, body });
const response = () => ({ code: 200, headers: {}, body: null, setHeader(key, value) { this.headers[key] = value; }, status(code) { this.code = code; return this; }, json(body) { this.body = body; return this; }, end() { return this; } });
const handler = (overrides = {}) => createHandler({ env: { RESEND_API_KEY: 'test-only' }, rateStore: new Map(), fetcher: async () => ({ ok: true, json: async () => ({ id: 'test-email' }) }), ...overrides });

test('validated lead is sent only to the fixed recipient with reply-to and idempotency', async () => {
  let sent;
  const fn = handler({ fetcher: async (url, options) => { sent = { url, ...options }; return { ok: true, json: async () => ({ id: 'test-email' }) }; } });
  const res = response(); await fn(request({ ...valid, to: 'attacker@example.com', from: 'spoof@example.com' }), res);
  assert.equal(res.code, 200); assert.equal(res.body.ok, true);
  const body = JSON.parse(sent.body);
  assert.deepEqual(body.to, ['contact@arpanghoshal.com']); assert.equal(body.reply_to, valid.email);
  assert.equal(body.from, 'CTRLRun <reviews@updates.arpanghoshal.com>');
  assert.equal(sent.headers['Idempotency-Key'], 'architecture-review/' + valid.requestId);
});

test('untrusted origins and malformed input cannot send mail', async () => {
  let calls = 0;
  const fn = handler({ fetcher: async () => { calls++; throw new Error('must not send'); } });
  for (const req of [request(valid, { origin: 'https://elsewhere.example' }), request({ ...valid, email: 'a@b.com\r\nBcc: x@y.com' }), request({ ...valid, company: '' }), request({ ...valid, concerns: ['unsupported'] }), request({ ...valid, requestId: 'invalid' }), request({ ...valid, website: 'spam' }), request('{'), request({ ...valid, purpose: 'x'.repeat(401) })]) {
    const res = response(); await fn(req, res); assert.ok(res.code >= 400);
  }
  assert.equal(calls, 0);
});

test('preflight is readable only by the Mintlify domain and never sends', async () => {
  const res = response(); await handler()({ ...request(), method: 'OPTIONS' }, res);
  assert.equal(res.code, 204); assert.equal(res.headers['Access-Control-Allow-Origin'], 'https://ctrlrun.dev');
});

test('provider failure, timeout and malformed success never become success', async () => {
  for (const fetcher of [async () => ({ ok: false }), async () => { throw new Error('timeout'); }, async () => ({ ok: true, json: async () => ({}) })]) {
    const res = response(); await handler({ fetcher })(request(), res); assert.equal(res.code, 502); assert.equal(res.body.ok, undefined); assert.ok(!JSON.stringify(res.body).includes('test-only'));
  }
});

test('missing server secret fails closed', async () => {
  const res = response(); await handler({ env: {} })(request(), res); assert.equal(res.code, 503);
});

test('per-instance rate backstop rejects a sixth request and expires', async () => {
  let time = 1; let calls = 0;
  const fn = handler({ now: () => time, fetcher: async () => { calls++; return { ok: true, json: async () => ({ id: 'test-email' }) }; } });
  for (let i = 0; i < 6; i++) { const res = response(); await fn(request(), res); assert.equal(res.code, i < 5 ? 200 : 429); }
  assert.equal(calls, 5); time = 600_002;
  const res = response(); await fn(request(), res); assert.equal(res.code, 200);
});

test('oversized requests and non-JSON are rejected before sending', async () => {
  for (const [headers, status] of [[{ 'content-length': '9000' }, 413], [{ 'content-type': 'text/plain' }, 415]]) {
    const res = response(); await handler()(request(valid, headers), res); assert.equal(res.code, status);
  }
});
