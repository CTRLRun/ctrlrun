<!-- One build-list item per pull request. CONTRIBUTING.md has the long version of every line. -->

## What this changes

<!-- The item, the specification section it implements or amends, and the user-visible result. -->

## Checklist

- [ ] **Specification first.** The section this implements is named above; a change to a frozen name or a new entry point amends the spec in this same PR.
- [ ] **Tests first.** The acceptance tests were red before the implementation and are green after it; every polling or waiting test bounds its clock or iteration count.
- [ ] **Mutation table.** Each MUST in the touched sections was removed, its named test confirmed red, and the guard restored. The table is in the description, checked against the four shapes in CONTRIBUTING.md.
- [ ] **`CLAIMS.md`.** Every new or changed README sentence has a row with code and a test; every removed capability took its row and its sentence with it. The table lives in [CTRLRun/ctrlrun-docs](https://github.com/CTRLRun/ctrlrun-docs), so a README change here is a pull request there too.
- [ ] **Docs audit green.** In a `ctrlrun-docs` checkout with `CTRLRUN_SOURCE` pointing at this one: `python tools/docs_audit/snippets.py`, `lint.py`, `links.py` and `render_capabilities.py --check` pass. A capability table was edited in that repository's `capabilities.yaml`, never by hand. CI here runs the same checks against this commit, so a stale page is red on this pull request and not on somebody else's.
- [ ] **`scripts/check.sh` green** under the project's interpreter.
- [ ] **Independent review** requested for anything touching authorization, identity, delegation, the gateway, an adapter or the store.
- [ ] **Nothing in `src/` merges on green CI alone**; a maintainer reads it.
- [ ] **Signed off.** Every commit carries `Signed-off-by` with its author's email (`git commit -s`); CONTRIBUTING.md's *Sign your work* says what that certifies.

## Mutation table

| Guard | Test | Result |
|---|---|---|
|  |  |  |
