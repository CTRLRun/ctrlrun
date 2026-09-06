"""The documentation audit: three checks and one generator, run by CI and by hand.

- `snippets.py`  — every fenced block marked `runnable` is executed offline and must succeed.
- `lint.py`      — the forbidden-words lint, with `lint-allowlist.txt` beside it.
- `links.py`     — internal links and anchors resolve.
- `render_capabilities.py` — renders `docs/capabilities.yaml` three ways and checks that no
  rendered copy has drifted from the generator.

Each is a script with a `main()` returning an exit status, and a function the tests call
directly. None of them imports anything outside the standard library, `pyyaml` and `ctrlrun`
itself, so they run wherever the kernel's own test suite runs.
"""
