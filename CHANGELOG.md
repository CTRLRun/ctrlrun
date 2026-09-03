# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Public API names are frozen in `docs/SPEC-v0.1.md` §8. Before 1.0 they may still change, and
any change to one appears here.

## [Unreleased]

## [0.1.0] — 2026-09-03

First packaged release. The v0.1 kernel is complete: every acceptance test in
`docs/SPEC-v0.1.md` §7 passes, including the multi-process concurrency test.

### Added

- **Action** — canonical form, `action_hash`, deep-frozen arguments. `float` is rejected in
  arguments: `0.1` and `0.10` are the same money and different hashes.
- **Policy** — YAML loader with `ALLOW` / `APPROVE` / `DENY` and fail-closed defaults. An
  unknown action is denied; there is no default-allow. See the config-breaking rule below
  for how condition keys are validated.
- **`@ctrlrun.protect()`** — binds a function call to an Action, evaluates it, and executes
  from the action's canonical arguments rather than the caller's objects.
- **Approval binding** — approvals carry the `action_hash` of what a human saw, and are
  single-use and expiring. A mutated action cannot present an approval granted for another.
- **Effect key and reservation** — template-resolved effect identity, reserved atomically
  across processes via `BEGIN IMMEDIATE` and a unique constraint on `effect_key`.
- **Effect state machine** — `NEW → RESERVED → EXECUTING → COMMITTED | FAILED | AMBIGUOUS`.
  Only an executor raising `NotExecuted` produces `FAILED`; every other exception, timeouts
  included, produces `AMBIGUOUS`, and only a human resolves it.
- **Receipts and events** — portable JSONL evidence for every action.
- **CLI** — `init`, `demo`, `approve`, `deny`, `receipts`, `effects`, `resolve`.
- **`ctrlrun demo`** — four failure scenarios, in process, no network.
- `SECURITY.md` and `docs/CLAIMS.md`, which maps every README claim to its code and test.

### Config-breaking rules

Rules that reject a policy file which an earlier build of this kernel would have loaded.
A `ctrlrun.yaml` written before this release may need an edit; the process refuses to start
until it gets one, which is the point.

- **A condition key naming an `Action` field is now a load-time `PolicyError`.** The
  reserved names are `action_id`, `agent`, `environment`, `principal`, `resource` and
  `user`. `when: { environment_eq: production }` reads exactly like it scopes a rule to
  production, and matched nothing at all — conditions address an action's *arguments*, and
  those are not arguments. Combined with a catch-all `decision: allow` beneath it, a rule
  that looked restrictive silently permitted everything. If a protected function genuinely
  takes an argument by one of those names, rename the argument (SPEC-v0.1 §3.2). Only the
  whole name is reserved: `resource_id_eq` is unaffected.
- **A condition on an argument the action does not carry now logs a warning.** The decision
  is unchanged — still false, still never an error, per SPEC-v0.1 §3.2 — but a typo such as
  `amont_lte` no longer disappears in silence. Nothing to edit; expect new log output.

### Notes

- Requires Python ≥ 3.11. Runtime dependencies are `pyyaml` and `click`.
- Single-host only: reservation is atomic across processes on one machine via SQLite.
  Multi-host needs the Postgres store planned for v0.6.
- Receipts are not signed. A database administrator can alter history (v0.6).
- Approver identity is free text and is not authenticated (v0.3).
- Generated ids (`act_`, `apr_`, `ctr_`) are 128 bits. An approval id is not a bearer token
  in v0.1 — consuming one needs write access to the store — but it becomes one with the
  webhook provider in v0.2, and an id format cannot be widened after records exist.
- Effect key templates do not escape placeholder values, so a crafted argument can make two
  distinct effects share one key. The result is a refusal rather than a double execution;
  `docs/THREAT_MODEL.md` states the limit and the workaround.
- Policy conditions address an action's arguments only. Scoping a rule by environment,
  resource or principal arrives with the authority model in v0.3.

[Unreleased]: https://github.com/CTRLRun/ctrlrun/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.1.0
