# The v0.6 soak

Build-list item 8; `SPEC-v0.6.md` §8.1. Outside `src/`, never packaged, on
`research/framework-probe/`'s precedent (`v0.4 §7.1`).

```
python research/soak/run.py --minutes 20 --postgres "$CTRLRUN_TEST_POSTGRES" --out soak.json
```

## The one question

*Does an `AMBIGUOUS` ever appear that the harness did not cause?*

`ROADMAP.md`'s exit criterion is a published run with **no** unexplained `AMBIGUOUS` and a
positive control that fired, and that is the one number nobody can produce by reasoning about the
code. Everything here exists to make an unexplained ambiguity **visible** rather than rare.

The criterion asked for **at least a week of calendar time** until 2026-09-07. `SPEC-v0.6.md`
§8.1 records the amendment, what it costs and what did not change; the duration is still measured
and published, and this file still prints it.

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

The table prints how long the run **actually lasted**, and `exit_criterion_met` is the ambiguity
count and the control alone. That split predates the amendment and outlives it: a harness that
graded its own duration would be making the one claim §8.1 says would be exactly as false as it
looks, and that was true when the criterion had a clock in it and is true now that it does not.

Whoever writes the changelog reads the measured duration and says that. `tests/test_soak.py`
asserts the rendered table never claims a duration it did not measure.

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

**What it is not evidence of, stated plainly: a long run.** This ran for twenty minutes. Anything
a soak finds by *accumulating* — a connection pool degrading over hours, table growth against the
one-row chain head `SPEC-v0.6.md` §6.3 serializes receipts on, a lease that lapses only under load
held longer than this, an operator restart mid-run — is outside what twenty minutes can show.
Since 2026-09-07 that is **unestablished rather than owed**: §8.1 removed the duration from the
criterion instead of waiting it out, and the thing a week would have bought is claimed by nothing
here. `exit_criterion_met: true` in the JSON is the ambiguity count and the control, which is all
the harness is allowed to decide; the clock is reported and left to a human.

The throughput figure is a by-product and is not a performance claim. Note in particular what it
does **not** measure: most of these actions are denied or refused by policy before any receipt is
written, so it says very little about the one-row head that `SPEC-v0.6.md` §6.3 serializes every
receipt write on. `docs/docs/postgres.md` describes that ceiling; this run does not size it.

## What it does not do

- It is **not a load test**. The throughput numbers are a by-product; nothing here is tuned for
  them and none is published as a performance claim.
- It does not exercise a network partition or a second host. That is item 4's
  `tests/test_cross_host.py`, and §4.5 says which of those were actually run.
- It says nothing about the receipt chain. `ctrlrun receipts --verify-chain` is that.
