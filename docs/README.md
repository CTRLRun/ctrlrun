# The Mintlify website

One Mintlify project, deployed from this repository's `/docs` directory.

- `index.mdx` serves `/`: the product homepage, execution boundary, failure example, and scenario explorer.
- `docs.mdx` serves `/docs`: the preserved technical overview.
- `docs/` contains all technical pages, published under `/docs/...`. The three technical navigation tabs retain their full sidebar structure.
- `risk-check.mdx` and `protect-my-agent.mdx` are custom-mode product pages.
- `snippets/` holds small client-side React components. Mintlify injects React hooks; do not add cross-snippet imports or third-party browser dependencies.
- `style.css` scopes product styling to `.cr-site`; documentation keeps the native Mintlify layout.
- `docs.json` defines navigation, metadata, and permanent redirects from the former technical URLs.
- `images/` contains public assets. `assets/` contains source assets and browser verification scripts.
- `capabilities.yaml` and `generated/` retain their source/render workflow. Generators now target technical pages in `docs/`.

## Preview and validate

```bash
cd docs
mint dev
```

The preview runs at `http://localhost:3000`. From the repository root:

```bash
python tools/docs_audit/snippets.py
python tools/docs_audit/lint.py
python tools/docs_audit/links.py
python tools/docs_audit/render_capabilities.py --check
node --test integrations/website-form/review.test.mjs
NODE_PATH="$(npm root -g)" node docs/assets/verify-website.cjs
cd docs && mint validate && mint broken-links
```

The browser harness requires Playwright and the preview server. It covers the demo state transitions, keyboard picker, mobile overflow, risk scoring, form validation, mocked email failure/retry/success, documentation sidebar, and key canonical URLs. It never sends an email. Set `WEBSITE_BASE_URL` to the hosted Mintlify preview URL to run the same checks against a deployment. Hosted checks also catch redirect normalization that differs from the local server. Do not add `/index` → `/` or `/docs/index` → `/docs` redirects: Mintlify normalizes those sources to their destinations, causing self-redirects.

## Scenario and conversion behavior

The Medical Affairs workbench uses `medical-workbench.js`, styles scoped to
`#cr-medical-workbench`, and `examples/medical_workbench.py`. Keep its embedded `MODULE`
equivalent to the Python source; `tests/test_medical_workbench.py` checks the copy. The browser
loads CTRLRun 0.6.1 on demand. Evidence and synthesis are synthetic; release decisions and
receipts execute in Python. `assets/verify-medical-workbench.cjs` exports a Playwright check
function accepting a page and an optional preview base URL. It exercises browser Python, both
downloads, error recovery and all six stages at three viewport widths. The letter demo remains
independent.

The homepage explorer contains 48 domains and 238 actions. All rules are illustrative, client-side simulations. The original Python runtime demos remain at `/docs/try-it` and `/docs/demos/medical-affairs`; they load their runtime only when invoked.

The risk check stores no answers and sends no form data until the visitor submits an architecture review. Its result distinguishes indicated patterns from unknown answers and explains the scoring rule.

The review form uses the private Vercel Function in `../integrations/website-form`, which calls Resend. Work email is required for follow-up. The recipient is fixed server-side. No API key appears in the Mintlify files. A mailto/copy fallback remains available. Browser retries reuse a request ID; success appears only after the server confirms Resend accepted the message.

`website-events.js` and the components emit `ctrlrun:conversion` custom events for page visits, CTA clicks, selections, scenario outcomes, risk-check completion, and review submission. No analytics provider was configured, so these are integration hooks, not stored analytics. A consent-aware listener can connect an existing provider later. Event payloads exclude contact details and free-text form contents.

## Deployment

The Mintlify GitHub App deploys the website from `main`, with content directory `/docs`. The redesign stays on its review branch until merged. The Vercel project serves only the form API; it does not host a second frontend.

Keep the `/mcp` platform endpoint unchanged. The `/docs/mcp/...` pages document MCP integrations; they are separate from Mintlify's generated documentation search endpoint.
