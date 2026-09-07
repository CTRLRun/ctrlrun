# CTRLRun review-form endpoint

A dependency-free Vercel Node.js Function for the Mintlify website. There is no frontend here.

- Project: `arpanghoshals-projects/ctrlrun-review-form`
- Endpoint: `https://ctrlrun-review-form.vercel.app/api/review`
- Sender: `CTRLRun <reviews@updates.arpanghoshal.com>`
- Recipient: `contact@arpanghoshal.com`, fixed in server code
- Reply-to: the validated work email submitted by the visitor
- Secret: `RESEND_API_KEY`, a sensitive Vercel production environment variable

The sending domain was verified in Resend during setup. Do not commit API keys, `.env` files, or the Vercel authentication token. The function returns acceptance only after Resend returns a message ID; it does not claim inbox delivery. It sends no automatic email to visitors.

## Validation and retries

The handler restricts CORS to the production Mintlify domains, validates and bounds every field, rejects a honeypot, caps request size, and fixes the sender and recipient. CORS is a browser boundary, not authentication. The per-instance limit of five requests per ten minutes is a backstop; it does not act as a shared global counter across Vercel instances.

The browser generates a UUID per reviewed brief. The server uses it as Resend's idempotency key. Retry without changing the brief after an uncertain response; editing a brief creates a new request. No form contents or credentials are logged by the handler.

```bash
node --test integrations/website-form/review.test.mjs
cd integrations/website-form
vercel link --project ctrlrun-review-form
vercel env add RESEND_API_KEY production --sensitive
vercel deploy --prod
```

Unit tests mock Resend and never send mail. The website browser harness also intercepts submission requests. Live validation checks only preflight and invalid requests.
