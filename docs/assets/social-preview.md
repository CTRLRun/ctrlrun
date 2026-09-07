# The social preview image

What GitHub, Slack, X and LinkedIn show when somebody pastes a link to this repository. Uploaded
by hand: repository settings → *Social preview* → upload `docs/assets/social-preview.png`.

## Specification

Matches the ctrlrun.dev homepage redesign (light theme, `CTRLRun_` mark, "Execution safety for
AI agents." headline) rather than the earlier dark GitHub-only card.

| | |
|---|---|
| Size | 1280 × 640 px (GitHub's recommended size; rendered at 2:1 everywhere it is shown) |
| Safe area | Keep text inside 72 px margins; previews are cropped to 1.91:1 on some services |
| Background | `#ffffff`, no photograph, no gradient |
| Wordmark | The amber keycap (`#F5A623`, edge `#B8730A`) and `CTRLRun` in `#14161b`, top left, matching `docs/images/wordmark-light.svg` |
| Eyebrow | `CONTROL THE ACTION. KEEP THE AUTONOMY.` at 20 px, letter-spaced, `#8a8f98` |
| Headline | Two lines at 76 px bold, `#14161b`: *Execution safety / for AI agents.* — the final period in `#F5A623` |
| Subheadline | The homepage's tagline at 28 px, `#6b7280`: *Let agents act. Keep control of what happens next.* |
| Panel | A light `#f6f6f4` code panel with a 6 px amber left edge: `$ pip install ctrlrun`, a domain-neutral `@ctrlrun.protect(tool_name, effect=effect_key)` line (deliberately not a named vendor like Stripe, so the card doesn't read as payments-only), and "Refunds. Access changes. Deploys. Emails. One boundary." |
| Corner | `ctrlrun.dev` at 20 px monospace, bottom right, `#14161b` |
| Fonts | Inter / system sans / Helvetica / Arial; monospace SFMono / Menlo / Consolas. System fonts, so the render depends on the machine; the committed PNG is the reference |

The share unit leads with the same promise as the homepage hero — execution safety without
losing autonomy — backed by the one line of code that turns it on. No screenshots, no badges,
no adopters, no stars.

## Regenerating

The image is rendered from `docs/assets/social-preview.svg` by
`docs/assets/render-social-preview.sh`, which needs `rsvg-convert`. Edit the SVG, run the
script, commit the PNG, upload it again.
