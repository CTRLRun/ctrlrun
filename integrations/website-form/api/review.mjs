import { createHash } from 'node:crypto';

const ORIGINS = new Set(['https://ctrlrun.dev', 'https://www.ctrlrun.dev']);
const CONCERNS = new Set(['Wrong actions', 'Duplicate execution', 'Human approval', 'Retry safety', 'Permissions', 'Auditability', 'Other']);
const RECIPIENT = 'contact@arpanghoshal.com';
// A small per-instance backstop. Edge rate limiting should also protect /api/review.
const requests = new Map();
const WINDOW_MS = 600_000;
const MAX_REQUESTS = 5;

export function validate(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Invalid request.');
  const field = (name, max, required = true) => {
    const value = input[name];
    if (value === undefined && !required) return '';
    if (typeof value !== 'string' || value.length > max || (required && !value.trim()) || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value)) throw new Error('Please check ' + name + '.');
    return value.trim();
  };
  const email = field('email', 254);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || /[\r\n]/.test(email)) throw new Error('Enter a valid work email.');
  const company = field('company', 100);
  const purpose = field('purpose', 400);
  const actions = field('actions', 400);
  const domain = field('domain', 80, false);
  const risk = field('risk', 120, false);
  const status = field('status', 30);
  if (!['Exploring', 'Building', 'Already in production'].includes(status)) throw new Error('Select a production status.');
  if (!Array.isArray(input.concerns) || input.concerns.length > 7 || input.concerns.some(item => !CONCERNS.has(item))) throw new Error('Select valid concerns.');
  const requestId = field('requestId', 36);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(requestId)) throw new Error('Please reload the form and try again.');
  return { company, email, purpose, actions, domain, risk, status, concerns: [...new Set(input.concerns)], requestId };
}

export function createHandler({ env = process.env, fetcher = fetch, now = Date.now, rateStore = requests } = {}) {
  return async function handler(req, res) {
    const origin = req.headers.origin;
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Vary', 'Origin');
    if (!ORIGINS.has(origin)) return res.status(403).json({ error: 'Origin not allowed.' });
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    if (req.method === 'OPTIONS') return res.status(204).end();
    if (req.method !== 'POST') { res.setHeader('Allow', 'POST, OPTIONS'); return res.status(405).json({ error: 'Method not allowed.' }); }
    if (!req.headers['content-type']?.startsWith('application/json')) return res.status(415).json({ error: 'Send JSON.' });
    if (Number(req.headers['content-length']) > 8192) return res.status(413).json({ error: 'Request is too large.' });
    let input;
    try {
      input = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      if (JSON.stringify(input || '').length > 8192) return res.status(413).json({ error: 'Request is too large.' });
    } catch { return res.status(400).json({ error: 'Invalid request.' }); }
    // Honeypot never triggers an email. Do not report success for a rejected request.
    if (input?.website) return res.status(400).json({ error: 'Unable to submit this request.' });
    let data;
    try { data = validate(input); } catch (error) { return res.status(400).json({ error: error.message }); }
    if (!env.RESEND_API_KEY) return res.status(503).json({ error: 'The review form is temporarily unavailable. Please email contact@arpanghoshal.com.' });
    const ip = req.headers['x-vercel-forwarded-for'] || req.headers['x-forwarded-for']?.split(',')[0] || req.socket?.remoteAddress || 'unknown';
    const rateKey = createHash('sha256').update(String(ip)).digest('hex');
    const time = now();
    for (const [key, entry] of rateStore) if (time - entry.start >= WINDOW_MS) rateStore.delete(key);
    const entry = rateStore.get(rateKey) || { start: time, count: 0 };
    if (entry.count >= MAX_REQUESTS || rateStore.size >= 10_000) { res.setHeader('Retry-After', '600'); return res.status(429).json({ error: 'Too many requests. Please try again later or email us directly.' }); }
    rateStore.set(rateKey, { start: entry.start, count: entry.count + 1 });
    const text = ['CTRLRun architecture review request', '', 'Company: ' + data.company, 'Reply email: ' + data.email, data.domain && 'Domain: ' + data.domain, 'Agent purpose: ' + data.purpose, 'Actions it can execute: ' + data.actions, 'Production status: ' + data.status, 'Primary concerns: ' + (data.concerns.join(', ') || 'Discuss during review'), data.risk && 'Execution risk check: ' + data.risk].filter(Boolean).join('\n');
    try {
      const response = await fetcher('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + env.RESEND_API_KEY, 'Content-Type': 'application/json', 'Idempotency-Key': 'architecture-review/' + data.requestId },
        body: JSON.stringify({ from: 'CTRLRun <reviews@updates.arpanghoshal.com>', to: [RECIPIENT], reply_to: data.email, subject: 'CTRLRun architecture review — ' + data.company.replace(/[\r\n]/g, ' '), text }),
        signal: AbortSignal.timeout(10_000)
      });
      if (!response.ok) return res.status(502).json({ error: 'We could not confirm submission. Retry this request or email contact@arpanghoshal.com.' });
      const sent = await response.json();
      if (typeof sent.id !== 'string') return res.status(502).json({ error: 'We could not confirm submission. Please retry.' });
      return res.status(200).json({ ok: true, id: sent.id });
    } catch {
      // Reuse the request ID on client retries: an uncertain response must not cause duplicate mail.
      return res.status(502).json({ error: 'We could not confirm submission. Retry this request or email contact@arpanghoshal.com.' });
    }
  };
}

export default createHandler();
