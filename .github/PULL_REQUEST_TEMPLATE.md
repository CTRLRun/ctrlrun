<!-- One build-list item per pull request. CONTRIBUTING.md has the long version of every line. -->

## What this changes

<!-- The item, the specification section it implements or amends, and the user-visible result. -->

## Checklist

- [ ] **Specification first.** The section this implements is named above; a change to a frozen name or a new entry point amends the spec in this same PR.
- [ ] **Tests first.** The acceptance tests were red before the implementation and are green after it; every polling or waiting test bounds its clock or iteration count.
- [ ] **Mutation table.** Each MUST in the touched sections was removed, its named test confirmed red, and the guard restored. The table is in the description, checked against the four shapes in CONTRIBUTING.md.
- [ ] **`docs/docs/CLAIMS.md`.** Every new or changed README sentence has a row with code and a test; every removed capability took its row and its sentence with it.
- [ ] **Docs audit green.** `python tools/docs_audit/snippets.py`, `lint.py`, `links.py` and `render_capabilities.py --check` pass; a capability table was edited in `docs/capabilities.yaml`, never by hand.
- [ ] **`scripts/check.sh` green** under the project's interpreter.
- [ ] **Independent review** requested for anything touching authorization, identity, delegation, the gateway, an adapter or the store.
- [ ] **Nothing in `src/` merges on green CI alone**; a maintainer reads it.

## Mutation table

| Guard | Test | Result |
|---|---|---|
|  |  |  |
