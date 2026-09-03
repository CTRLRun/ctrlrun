# Claims

Every sentence of the README's opening paragraph, mapped to the code that implements it and
the test that proves it.

**This table is regenerated at every release.** A claim that loses its code or its test is
removed from the README in the same commit — the README is not allowed to describe behaviour
that no longer ships. If you find a row here that does not hold against the version you
installed, that is a bug: please open an issue.

Regenerated for: **v0.1.0**. Line numbers refer to that tag.

## The opening paragraph

> CTRLRun is open-source infrastructure for controlling consequential AI-agent actions. You
> decide, per action, what an agent can do autonomously, what requires human approval, and
> what is blocked. CTRLRun binds approvals to the exact action, blocks duplicate execution
> attempts for the same logical effect, stops blind retries when an execution outcome is
> uncertain, and records what actually happened. Autonomy belongs to the action, not the
> agent.

| Claim | Code | Proof |
|---|---|---|
| "Transaction safety for AI-agent actions" | `Control.execute` — `control.py:188` drives reserve → execute → commit/fail/ambiguous over `EffectState` (`effect.py:56`) | `test_T1_a_lost_response_leaves_the_effect_ambiguous` |
| "Agents can retry. Your refund shouldn't." | A retry against an `AMBIGUOUS` key is refused — `state.py:187` | `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote` |
| "open-source" | Apache-2.0 `LICENSE`; `license = "Apache-2.0"` in `pyproject.toml` | n/a — licence file |
| "controlling consequential AI-agent actions" | `@protect` — `control.py:688`; `context()` — `control.py:86` | `test_T6_unknown_action_raises_ActionDenied_with_reason_unknown_action` |
| "what an agent can do autonomously, what requires human approval, and what is blocked" | `Decision.ALLOW / APPROVE / DENY` — `policy.py:55`. Exactly three members. | `test_T6_unknown_action_is_denied_with_reason_unknown_action` |
| "binds approvals to the exact action" | `ApprovalRequest.action_hash` — `approval.py:81`; `consume_approval(approval_id, action_hash)` — `approval.py:278` | `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch`, `test_T2_any_material_change_invalidates_the_approval` |
| "blocks duplicate execution attempts for the same logical effect" | `reserve_effect` — `state.py:292`, decided inside the `BEGIN IMMEDIATE` of `_authorize_and_reserve` (`state.py:888`) against `effect_key TEXT PRIMARY KEY` (`state.py:69`) | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` (8 OS processes), `test_T3_the_fake_remote_is_called_exactly_once` |
| "stops blind retries when an execution outcome is uncertain" | Only `NotExecuted` maps to `FAILED` — `control.py:250`. Every other exception, timeouts included, yields `AMBIGUOUS`. | `test_T1_a_blind_retry_writes_a_blocked_receipt`, `test_T1_the_ambiguous_record_survives_the_blocked_retry` |
| "records what actually happened" | `ReceiptResult` — `receipt.py:44`; `Event` and JSONL `append_event` — `receipt.py:222` | `test_T11_every_demo_receipt_carries_every_field_in_the_spec`, `test_T11_every_demo_receipt_parses_back_into_a_Receipt` |
| "Autonomy belongs to the action, not the agent." | `Policy.evaluate(action)` — `policy.py:233` — passes only the action's **name and arguments** to `_ActionPolicy.evaluate` (`policy.py:164`), whose signature has no principal in it. A rule cannot read who is acting even by accident. `agent_eq` and `user_eq` are refused at load (`policy.py:50`) rather than silently matching nothing. | `test_T6_an_action_name_is_matched_exactly`, `test_a_condition_naming_an_action_field_is_refused_at_load` |

## Two claims stated as limits

The README also makes two negative claims. They matter as much as the positive ones.

| Claim | Where it holds |
|---|---|
| "CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control." | Stated, not implemented — see `THREAT_MODEL.md`, "Out of scope". CTRLRun never asserts what a remote did; only `NotExecuted`, raised by the executor, claims that. |
| "It will not *knowingly* execute the same logical effect twice, and will never treat an unknown outcome as a failure." | `state.py:187` (refuse retry on `AMBIGUOUS`) and `control.py:250` (only `NotExecuted` → `FAILED`) |

## Demo output

The README quotes `ctrlrun demo` verbatim.
`test_the_readme_demo_section_quotes_the_demo_output_verbatim` runs the demo and asserts every
line it prints appears in the README, masking only the generated approval ids. A change to the
demo's output that nobody carried across fails there rather than shipping a README that lies.

## How these line numbers are kept honest

They are not, automatically — a citation is prose, and prose drifts. Every row above was
re-derived against the tree at the tag named at the top of this file, and the two that had
drifted since the previous pass are called out here rather than quietly corrected: the
`BEGIN IMMEDIATE` citation pointed at `grant_approval`'s transaction rather than the
reservation path's, and the "no principal" claim pointed at the private
`_ActionPolicy.evaluate` while the sentence described the public `Policy.evaluate`. Both now
point where the sentence says they do. If you are regenerating this file, re-derive every
row; do not carry one forward on trust.
