# The social preview image

What GitHub, Slack, X and LinkedIn show when somebody pastes a link to this repository. Uploaded
by hand: repository settings → *Social preview* → upload `docs/assets/social-preview.png`.

## Specification

| | |
|---|---|
| Size | 1280 × 640 px (GitHub's recommended size; rendered at 2:1 everywhere it is shown) |
| Safe area | Keep text inside 72 px margins; previews are cropped to 1.91:1 on some services |
| Background | `#14161b`, no photograph, no gradient |
| Wordmark | `docs/assets/wordmark.svg` at 58 px, top left, in `#c9ccd3` |
| Line 1 | The tagline, two lines at 46 px bold, `#f2f3f5`: *The last check before an AI agent / does something it can't undo.* |
| Line 2 | The principle at 26 px, `#a7abb4`: *Autonomy belongs to the action, not the agent.* |
| Panel | A terminal panel carrying the demo's first scenario, three lines in monospace, the third being the demo's `✗ BLOCKED — effect may already have committed; blind retry refused` line in `#f28b82` |
| Corner | `pip install ctrlrun` at 20 px, bottom right, `#6f747d` |
| Fonts | Helvetica Neue / Helvetica / Arial; monospace SFMono / Menlo / Consolas. System fonts, so the render depends on the machine; the committed PNG is the reference |

The share unit is a failure, not a feature list: the image shows an agent doing something wrong
and CTRLRun refusing. No logos, no badges, no adopters, no stars.

## Regenerating

The image is rendered from `docs/assets/social-preview.svg` by
`docs/assets/render-social-preview.sh`, which needs `rsvg-convert`. Edit the SVG, run the
script, commit the PNG, upload it again.
