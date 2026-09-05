# The v0.6 soak

Build-list item 8; `SPEC-v0.6.md` §8.1. Outside `src/`, never packaged, on
`research/framework-probe/`'s precedent (`v0.4 §7.1`).

```
python research/soak/run.py --minutes 600 --postgres "$CTRLRUN_TEST_POSTGRES" --out soak.json
```

## The one question

*Does an `AMBIGUOUS` ever appear that the harness did not cause?*

`ROADMAP.md`'s exit criterion is a week with **no** unexplained `AMBIGUOUS`, and that is the one
number nobody can produce by reasoning about the code. Everything here exists to make an
unexplained ambiguity **visible** rather than rare.

## "Unexplained" is defined before the run starts

Or the criterion is unfalsifiable.

- An `AMBIGUOUS` whose **attempt** has a recorded injection is *explained*.
- One with no corresponding injection is *unexplained*, and is what the criterion counts.

Keyed on the attempt — `(effect_key, action_id)` — and never on the effect key alone. One key may
be attempted more than once, `v0.1 §5.4`'s retry being the ordinary way, and an injection against
attempt 1 says nothing about attempt 2. Keying on the key would let one recorded injection absorb
every later ambiguity on it, which is how a real finding disappears.

**Injections are recorded before they are caused.** A crash in between leaves an injection with no
ambiguity, which is harmless. Recording afterwards would leave an ambiguity with no injection —
a false finding, manufactured by the harness, in the exact number it exists to report.

## The positive control runs first, in its own store

A harness that reports "zero unexplained" without being able to *see* one is reporting that it
looked, not that there was nothing to find (`v0.4 §1.3`).

So a short control phase injects an ambiguity it deliberately does not record and requires the
classifier to report it. It runs in its own store and its own ledger, because a first version ran
the control inside the measured phase and made `exit_criterion_met` unreachable — the run could
prove it could see, or report zero, never both.

A table whose control did not fire says so, in words, and is not evidence.

## The duration is measured, never asserted

§8.1's week is calendar time and "does not compress". The table prints how long the run **actually
lasted** and `exit_criterion_met` is about the ambiguity count alone. A harness that decided for
itself whether the duration was met would be making the one claim §8.1 says would be exactly as
false as it looks.

Whoever writes the changelog reads the measured duration and says that. `tests/test_soak.py`
asserts the rendered table never contains the word "week".

## What it does not do

- It is **not a load test**. The throughput numbers are a by-product; nothing here is tuned for
  them and none is published as a performance claim.
- It does not exercise a network partition or a second host. That is item 4's
  `tests/test_cross_host.py`, and §4.5 says which of those were actually run.
- It says nothing about the receipt chain. `ctrlrun receipts --verify-chain` is that.
