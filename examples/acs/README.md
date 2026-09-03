# The double-refund scenario, through ACS hooks

ACS has no `examples/` directory of its own as of commit `c7ad162` (2026-08-11), so this
follows CTRLRun's own convention: one runnable script, its policy beside it, no network, and
its own state directory.

```
python examples/acs/main.py
```

## What it shows

A tool call fires two ACS hooks. `steps/toolCallRequest` is where a Guardian decides;
`steps/toolCallResult` is where the platform reports what happened. CTRLRun answers both:
it takes the reservation at the first and closes it at the second, which is what makes the
retry refusable — the first call's effect record is still open when the second arrives.

The remote commits the refund and the response goes missing. ACS reports `exit_status:
"timeout"`, and **ACS does not say what a timeout means for the side effect**. CTRLRun
records `AMBIGUOUS` and refuses the retry, because a tool that timed out after acting and one
that timed out before acting send the same string.

See [`docs/ACS.md`](../../docs/ACS.md) for the full mapping and for where the two models
disagree.
