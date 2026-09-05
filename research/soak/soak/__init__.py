"""The v0.6 soak. Build-list item 8; SPEC-v0.6 §8.1.

Outside `src/`, never packaged, on `research/framework-probe/`'s precedent (`v0.4 §7.1`).

**"Unexplained" is defined before the run starts**, or the exit criterion is unfalsifiable. The
harness is the definition:

- an `AMBIGUOUS` whose effect key has a **recorded injection** for that attempt is *explained*;
- one with no corresponding injection is *unexplained*, and is the thing `ROADMAP.md`'s exit
  criterion counts.

The injector records what it did **before** it does it, so a crash between the injection and the
record cannot turn an explained ambiguity into an unexplained one by losing the note. The
alternative -- recording afterwards -- makes the harness optimistic in exactly the direction that
would hide a real finding.

**The positive control is not optional** (`v0.4 §1.3`). A harness that reports "zero unexplained"
without being able to *see* one is reporting that it looked, not that there were none. `--control`
injects an ambiguity it deliberately does not record, and the run MUST report it. `soak.py`
refuses to publish a table from a run whose control did not fire.
"""
