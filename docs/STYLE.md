# Style sheet for the documentation

Every page on the docs site and every sentence in the README follows this sheet. The audit
tools under `tools/docs_audit/` enforce the parts a machine can check; the rest is what a
reviewer reads for.

## The page

- **Answer first.** The first paragraph answers the question the title asks, with no preamble.
  That paragraph is what a search snippet, an AI assistant and an impatient engineer read, and
  often the only thing they read.
- **One definitional sentence** on every Concepts page, phrased so it stands alone when
  quoted: *An effect key is …*. Write it for a person; if it reads badly aloud it reads badly to
  a model.
- **At most 900 words**, except Reference pages. A page that needs more is two pages.
- **Code before prose** wherever the code can carry the point. Show the policy, then say what
  it does.
- **Every page ends with "Next"**: two or three links to where the reader goes from here. Every
  page links to Why and to Get started somewhere in its body or its Next block.
- **Headings are the questions people search.** *What happens on a timeout?* rather than
  *Timeouts*. One H1 per page, and it is the title.

## The sentence

- **Second person, present tense.** *You declare an effect key* — not *the user will declare*.
- **One idea per paragraph.** Two or three sentences is a paragraph; six is two.
- **No exclamation marks.** No *we're excited*, no *simply*, no *just*, no *easy*.
- **Plain claims.** A sentence either describes what the shipped code does, and has a row in
  `docs/docs/CLAIMS.md`, or it is marked *(design)*, or it is cut.
- **Numbers travel with their units.** Amounts are integer minor units and the page says so
  the first time one appears.

## The words

- **CTRLRun**, always in that capitalisation. Never *Ctrlrun*, *ctrlrun* in prose, or *CTRL Run*.
  In code, the package and command are `ctrlrun`.
- **The fixed copy** is fixed. The tagline, the principle, the category line, the promise and
  the opener are quoted from `docs/IA.md` and are not paraphrased.
- **The forbidden list** is enforced by `tools/docs_audit/lint.py`. In a heading, a title, a
  description or a hero line: never *runtime control*, *governance*, *guardrails*,
  *compliant*, *secure* as a bare adjective, *exactly-once*, *transaction*. Anywhere at all:
  never a compliance or standards claim, never *pack* or *sector*, never a regulation named as
  supported, never social proof that does not exist.
- **The definitional words appear once each**, in the sentence written for search:
  *idempotency*, *human-in-the-loop*, *MCP gateway*, *double execution*, *AI agent safety*.
- **Outcomes are spelled as the code spells them**: `COMMITTED`, `FAILED`, `AMBIGUOUS`.
  Decisions likewise: `allow`, `approve`, `deny`.

## The examples

- **Three domains minimum on every list of examples**, from: money, infrastructure,
  permissions, records, communications. The refund is the first example because everyone
  understands it. It is never the only one.
- **Real names, invented values.** `stripe.refund`, `k8s.delete_namespace`, `iam.grant_role`,
  `crm.update_record`, `email.send`. Amounts, ids and addresses are obviously invented.
- **The share unit is a failure.** An example shows an agent doing something wrong and CTRLRun
  refusing. A list of features is not an example.

## The code blocks

- **A block either runs or makes no promise.** A fence marked `runnable` is executed offline by
  `tools/docs_audit/snippets.py` on every CI run. A block without the marker is illustration,
  and it says so in the prose beside it or elides visibly (`...`).
- **The marker is a word on the fence's info string**: ```` ```python runnable ````,
  ```` ```bash runnable ````, ```` ```yaml runnable ````.
- **All runnable blocks on one page share one temporary directory**, in page order. A
  `yaml runnable` block is validated by the real loaders and then written as `ctrlrun.yaml`,
  so a later block can read it. Add `file=name.yaml` to the info string to write it elsewhere.
- **A `python runnable` block is its own script.** Add `continue` to run it appended to the
  page's previous runnable Python blocks, so a function defined above can be called below.
- **Bash runs under `-euo pipefail`** with the checkout's `ctrlrun` on `PATH`. A line that
  needs the network — `pip install`, a webhook, a real remote — is not runnable and is not
  marked.
- **Expected output is shown as `text` or `console`**, never marked runnable, and quoted from a
  real run. Where a test already quotes it (the demo, the verify report), the page quotes the
  same lines.

## Links

- Internal links are relative paths or root-relative docs paths, never absolute GitHub URLs
  unless the target is a file that has no page. `tools/docs_audit/links.py` resolves every one,
  anchors included.
- A link's text says where it goes: *the effect-keys concept*, not *here*.

## What the tools check

| Tool | Checks | Runs |
|---|---|---|
| `snippets.py` | every `runnable` block executes offline and exits 0 | CI, `docs` job |
| `lint.py` | the forbidden words, in their scope, minus `lint-allowlist.txt` | CI, `docs` job |
| `links.py` | internal links and anchors resolve | CI, `docs` job |
| `render_capabilities.py --check` | every rendered capability table matches `capabilities.yaml` | CI, and `tests/test_docs_audit.py` |

Run them by hand from the repository root:

```bash
python tools/docs_audit/snippets.py
python tools/docs_audit/lint.py
python tools/docs_audit/links.py
python tools/docs_audit/render_capabilities.py --check
```

## Product experience

The `/`, `/risk-check`, and `/protect-my-agent` pages use Mintlify custom mode. The product brief governs their concise copy and layout; the documentation-only Next section, fixed-copy, and three-domain-list rules do not apply to these pages. Technical pages under `/docs` retain the rules above.
