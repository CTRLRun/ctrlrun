# The documentation site

The site is built by Mintlify from this directory: `docs.json` is the configuration, every
`.mdx` file is a page, and the specifications and reference documents that were here before
the site stay where they are and are linked from the *Architecture and specifications* tab.

## Preview locally

```bash
npm i -g mint
cd docs && mint dev
```

`mint dev` serves the site at <http://localhost:3000>. `mint validate` builds it without serving,
and `mint broken-links` checks the internal links the way the deployed site resolves them.

## Where things are

| | |
|---|---|
| `docs.json` | navigation, theme, SEO defaults, redirects |
| `index.mdx`, `why.mdx` | Home and Why |
| `get-started/`, `concepts/`, `guides/`, `cookbook/`, `reference/`, `compare/`, `security/` | the sections in `IA.md` |
| `images/` | assets the site serves; `assets/` holds the sources (the vhs tape, the SVGs) |
| `capabilities.yaml`, `generated/` | the capability tables' source and its renders; edit the YAML, never a render |
| `IA.md`, `STYLE.md` | what to write and how; not pages |
| `.mintignore` | what in this directory is not a page |

## Before a pull request

From the repository root:

```bash
python tools/docs_audit/snippets.py
python tools/docs_audit/lint.py
python tools/docs_audit/links.py
python tools/docs_audit/render_capabilities.py --check
cd docs && mint validate && mint broken-links
```

Deployment is Mintlify's GitHub App on pushes to `main`, configured as a monorepo with the path
`/docs`. That setup is a dashboard step, listed in the launch checklist.
