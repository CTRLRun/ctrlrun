# Claims

Every sentence of the README's opening paragraph, mapped to the code that implements it and
the test that proves it.

**This table is regenerated at every release.** A claim that loses its code or its test is
removed from the README in the same commit — the README is not allowed to describe behaviour
that no longer ships. If you find a row here that does not hold against the version you
installed, that is a bug: please open an issue.

Regenerated for: **v0.2.0**. Line numbers refer to that tag.

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


## What v0.2 adds to the README

Every sentence the README gained in this release, mapped the same way.

| Claim | Code | Proof |
|---|---|---|
| "Point the client at the gateway instead of at the tool server" | `Gateway.handle` — `gateway/server.py:135`; `serve` — `gateway/server.py:892` | `test_T19_the_upstream_receives_the_canonical_arguments` |
| "No agent changes" | `tools/call` is intercepted and every other method relayed unchanged — `gateway/mcp.py:84` | `test_a_non_intercepted_method_is_relayed_with_no_ctrlrun_outcome` |
| "The gateway prints … every action in your policy that has no `effect:` template" | `_announce_actions_without_an_effect` — `cli/main.py` | Shown in the README block; produced by `Policy.effect_template` — `policy.py:281` |
| "Tools become actions named `mcp.<alias>.<tool>`" | `Gateway._intercept` — `gateway/server.py` | `test_T19_the_action_is_named_for_the_alias_and_the_tool` |
| "Declare their effect and resource templates there" | `Policy.effect_template` / `resource_template` — `policy.py:281`; `McpOptions` — `policy.py:187` | `test_T16_a_v2_document_loads_and_exposes_its_templates`, `test_T16_a_decorator_and_a_policy_template_produce_the_same_action_hash` |
| "Everything but `tools/call` is relayed untouched" | `parse_request(...).intercept` — `gateway/mcp.py:84` | `test_every_other_method_is_relayed_not_intercepted` |
| "A lost response over the wire blocks the retry exactly as it does in-process" | `classify` — `gateway/outcome.py:144`, translated into v0.1 §5.5's own vocabulary by the gateway's executor | `test_T23_the_identical_call_sent_again_is_refused_and_the_upstream_called_once` |
| "the only thing besides a human permitted to move a record out of `AMBIGUOUS`" | `Control._reconciled` — `control.py:713`; `RECONCILED_STATES` — `effect.py` | `test_T13_a_hook_answering_not_executed_moves_the_record_to_failed`, `test_T14_a_hook_answering_committed_refuses_the_retry_as_a_duplicate` |
| "and only in the direction its answer points" | `"unknown"` is absent from `RECONCILED_STATES` — `effect.py` | `test_T15_a_hook_that_cannot_answer_leaves_the_record_ambiguous` |
| "one OpenTelemetry span per action, one span event per step" | `OTelEventSink` — `otel.py:45` | `test_T29_one_action_produces_one_span_named_for_the_action`, `test_T29_every_event_becomes_a_span_event_named_by_its_type` |
| "Argument values stay out of it unless you ask for them" | `OTelEventSink(arguments=...)` — `otel.py:45` | `test_T29_argument_values_are_not_attributes_by_default` |

### Two claims the README deliberately does not make

- **Nothing about ACS conformance.** `ctrlrun.acs.AcsControlHook` (`acs.py:74`) exists and is
  tested (T51–T55), but at the ACS commit read there is no reference implementation and no
  conformance suite. "ACS-compatible" appears nowhere in the README, in docstrings, or in CLI
  output. `docs/ACS.md` says what was read and where the standard is silent.
- **Nothing about exactly-once execution.** Unchanged from v0.1, and still the honest line:
  CTRLRun guarantees it will not *knowingly* execute the same logical effect twice, and that
  it will never treat an unknown outcome as a failure.

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
