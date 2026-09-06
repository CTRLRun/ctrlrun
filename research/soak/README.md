# The v0.6 soak

Build-list item 8; `SPEC-v0.6.md` §8.1. Outside `src/`, never packaged, on
`research/framework-probe/`'s precedent (`v0.4 §7.1`).

```
python research/soak/run.py --minutes 20 --postgres "$CTRLRUN_TEST_POSTGRES" --out soak.json
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

## What was run

One measured run, and the numbers are the numbers:

```
CTRLRun soak — postgres (schema soak_968eae6651)
  ran            20m 0s (2026-09-05T19:05:37Z → 2026-09-05T19:25:37Z)
  actions        889735
  ambiguous      133393  (133393 explained, 0 unexplained)
  control fired  yes

  no unexplained ambiguity
```

The JSON table is `results/2026-09-05-postgres-20m.json`.

Four worker threads, one `PostgresStateStore` each, against PostgreSQL on the same host, into a
schema created for the run and dropped after it. The injection mix is `soak/runner.py`'s
`INJECTIONS`: 70% clean, 10% executor timeout, 10% unknown exception from the executor, 8%
`NotExecuted`, 2% a lease short enough to lapse mid-execution.

**What that is evidence of.** Across 889,735 attempts, every `AMBIGUOUS` the store held at the end
had a ledger entry recorded before the failure that produced it. Nothing became ambiguous that the
harness did not make ambiguous, and the positive control fired, so the run was capable of saying
otherwise.

**What it is not evidence of, stated plainly: `ROADMAP.md`'s exit criterion.** That criterion is a
soak of at least **one week** with no unexplained `AMBIGUOUS`, and this ran for twenty minutes. A
week of calendar time does not compress, and a bigger action count is not a substitute for it — a
ten-hour run would meet the criterion no better than this one does, it would just put a larger
number next to something still unmet. `exit_criterion_met: true` in the JSON is about the
**ambiguity count**, which is all the harness is allowed to decide; the clock is reported and left
to a human, and `docs/ROADMAP.md` records the criterion as **not met**.

The throughput figure is a by-product and is not a performance claim. Note in particular what it
does **not** measure: most of these actions are denied or refused by policy before any receipt is
written, so it says very little about the one-row head that `SPEC-v0.6.md` §6.3 serializes every
receipt write on. `docs/postgres.md` describes that ceiling; this run does not size it.

## What it does not do

- It is **not a load test**. The throughput numbers are a by-product; nothing here is tuned for
  them and none is published as a performance claim.
- It does not exercise a network partition or a second host. That is item 4's
  `tests/test_cross_host.py`, and §4.5 says which of those were actually run.
- It says nothing about the receipt chain. `ctrlrun receipts --verify-chain` is that.
