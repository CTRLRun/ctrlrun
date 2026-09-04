# Claims

Every sentence of the README's opening paragraph, mapped to the code that implements it and
the test that proves it.

**This table is regenerated at every release.** A claim that loses its code or its test is
removed from the README in the same commit — the README is not allowed to describe behaviour
that no longer ships. If you find a row here that does not hold against the version you
installed, that is a bug: please open an issue.

Regenerated for: **v0.3.0**. Line numbers refer to that tag.

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
| "The gateway prints … every action in your policy that has no `effect:` template" | `_announce` — `gateway/__init__.py` (it moved out of `cli/main.py` with SPEC-v0.3 §8.4, which added the environment, the identity provider and the authority section to the same block) | `test_the_startup_block_names_the_environment_identity_and_authority`, `test_the_startup_block_says_so_when_there_is_no_authority_section` |
| "Tools become actions named `mcp.<alias>.<tool>`" | `Gateway._intercept` — `gateway/server.py` | `test_T19_the_action_is_named_for_the_alias_and_the_tool` |
| "Declare their effect and resource templates there" | `Policy.effect_template` / `resource_template` — `policy.py:281`; `McpOptions` — `policy.py:187` | `test_T16_a_v2_document_loads_and_exposes_its_templates`, `test_T16_a_decorator_and_a_policy_template_produce_the_same_action_hash` |
| "Everything but `tools/call` is relayed untouched" | `parse_request(...).intercept` — `gateway/mcp.py:84` | `test_every_other_method_is_relayed_not_intercepted` |
| "A lost response over the wire blocks the retry exactly as it does in-process" | `classify` — `gateway/outcome.py:144`, translated into v0.1 §5.5's own vocabulary by the gateway's executor | `test_T23_the_identical_call_sent_again_is_refused_and_the_upstream_called_once` |
| "the only thing besides a human permitted to move a record out of `AMBIGUOUS`" | `Control._reconciled` — `control.py:713`; `RECONCILED_STATES` — `effect.py` | `test_T13_a_hook_answering_not_executed_moves_the_record_to_failed`, `test_T14_a_hook_answering_committed_refuses_the_retry_as_a_duplicate` |
| "and only in the direction its answer points" | `"unknown"` is absent from `RECONCILED_STATES` — `effect.py` | `test_T15_a_hook_that_cannot_answer_leaves_the_record_ambiguous` |
| "one OpenTelemetry span per action, one span event per step" | `OTelEventSink` — `otel.py:45` | `test_T29_one_action_produces_one_span_named_for_the_action`, `test_T29_every_event_becomes_a_span_event_named_by_its_type` |
| "Argument values stay out of it unless you ask for them" | `OTelEventSink(arguments=...)` — `otel.py:45` | `test_T29_argument_values_are_not_attributes_by_default` |

## What v0.3 adds to the README

The authority section, observe mode and the identity extra. Every sentence, mapped the same
way — and the four the README deliberately *does not* say are below.

| Claim | Code | Proof |
|---|---|---|
| "A policy … cannot see who is asking — deliberately, since v0.1" | `Policy.evaluate` still takes only the action's name and arguments; `RESERVED_ARGUMENTS` — `policy.py:104` — refuses `agent_eq` and every other principal-addressing condition at load, in a document of **every** schema version | `test_T74b_a_reserved_name_in_a_policy_rule_is_a_load_error`, `test_T74b_a_reserved_name_in_a_grant_constraint_is_a_load_error` |
| "`authority:` is the second axis" | `Authority.evaluate` — `authority.py:807`; `Control._authority_result` — `control.py:394` | `test_T67_a_principal_with_no_grant_is_denied` |
| "opt-in, and then fail-closed" | `_optional_authority` returns `None` for a document with no section — `control.py`; `Control.authority is None` is v0.2 behaviour exactly | `test_T66_a_document_with_no_authority_section_leaves_control_authority_none`, `test_T66_no_authority_event_is_appended_without_a_section`, and T66's session-wide guard in `tests/conftest.py` |
| "every principal needs a grant and no grant means denied" | `NO_AUTHORITY` — the fail-closed default of `Authority.evaluate` (`authority.py:807`), reached for reads and for actions with no effect key alike | `test_T67_an_action_the_policy_allows_outright_still_needs_a_grant` |
| "A grant carries no `decision:`" | `_GRANT_KEYS` — `authority.py` — is a closed set that does not contain `decision` | `test_T73b_grant_refuses_what_the_loader_refuses` |
| "the two axes … combine as the stricter of the two" | `Control.evaluate` returns the combined result — `control.py`; a denial on either axis is a denial | `test_T70_the_stricter_of_the_two_wins` |
| "authority first" | `Control.execute` evaluates authority before policy and a denial appends `AUTHORITY_DENIED` and never `POLICY_EVALUATED` — `control.py:425` | `test_T74_a_denial_leaves_no_pending_approval_request` |
| "narrow it at runtime with `ctrlrun delegate`" | `Control.delegate` — `control.py:1460`; `Authority.plan_delegation` — `authority.py:878`; `ctrlrun delegate` — `cli/main.py:675` | `test_t75_the_delegation_authorizes_an_action_within_its_limits` |
| "provably a subset of its parent on every dimension, at creation and again at every evaluation" | `contained_dimension` — `authority.py:604` — runs from `plan_delegation` (`authority.py:878`) **and** from the chain walk in `Authority.evaluate` (`authority.py:807`) | `test_t76_each_dimension_violated_alone`, `test_t77b_a_narrowed_parent_narrows_its_children` |
| "Omitting a dimension the parent constrains is rejected, not inherited" | `contained_dimension` treats an absent child dimension as unconstrained and therefore wider — `authority.py:604`; the subject half is `_subject_contained` (`authority.py:635`) | `test_t81_omission_is_not_unlimited`, `test_T73b_a_subject_addressed_to_every_principal_is_refused`, `test_t76_each_dimension_violated_alone` |
| "`ctrlrun revoke` cuts a chain of any depth with one write" | `Control.revoke` — `control.py:1476` — writes one row (`state.py:529`) and visits no children; every evaluation walks to the root | `test_t78_a_revoked_parent_denies_its_grandchild`, `test_put_delegation_is_never_an_upsert` |
| "`mode: observe` … records what *would* have been blocked, without blocking anything" | `_parse_mode` — `policy.py:405`; `Control._observed` — `control.py:693`; `_WouldHave` — `receipt.py:180`; `ReceiptResult.OBSERVED` — `receipt.py:101` | `test_T82_observe_executes_what_enforce_would_deny`, `test_T83_a_duplicate_is_recorded_and_still_runs` |
| "One top-level line" | `mode:` is refused anywhere but the top level — `reject_nested_mode`, `policy.py:424` | `test_T84_mode_is_refused_anywhere_but_the_top_level` |
| "`ctrlrun stats` gives you the numbers" | `stats` — `cli/main.py:500`; counted from `would_have.blocked_reason` and nothing else | `test_T86_stats_counts_what_observe_mode_recorded`, `test_T86_stats_reaches_no_network` |
| "It is not a dry run: it executes" | `_observed` runs the executor on every path, including the ones enforce mode would have refused — `control.py:693` | `test_T82_observe_executes_what_enforce_would_deny`, `test_T83_an_executor_that_fails_on_a_held_key_still_writes_the_record` |
| "verifies a bearer token against a JWKS or a pinned key" | `JWTIdentityProvider._verified` — `jwt_identity.py:175`; the algorithm comes from the configured list and never from the token | `test_T88_a_valid_token_becomes_a_principal`, `test_T89_every_invalid_token_is_refused_by_cause` |
| "maps the verified claims onto a principal" | `_principal` — `jwt_identity.py` — copies only the claims named in `claim_names` | `test_T88_only_the_named_claims_reach_the_principal` |
| "`pip install \"ctrlrun[identity]\"`" | `identity = ["pyjwt[crypto]>=2.8"]` in `pyproject.toml`; imported lazily by `_jwt()` — `jwt_identity.py` | `test_T92_constructing_without_the_extra_names_the_install_command`, `test_T92_importing_ctrlrun_pulls_in_no_jwt_module` |
| "CTRLRun issues no credential and defines no identity format" | There is no minting, signing or issuing code path in the package: `jwt_identity.py` calls `decode` and never `encode` | `test_the_package_never_encodes_a_token` |

### Four claims the v0.3 README deliberately does not make

- **Nothing about compliance, conformance or alignment**, with any standard, in the README, in
  a docstring or in CLI output. `authority:` is an authorization model; it is not an ACS, an
  OAuth or an OIDC claim, and none of those words appears as an assertion about CTRLRun.
- **Nothing about issuing identity.** The README says "consumed, never invented" and means it:
  no token minting, no OAuth flow, no authorization server, no introspection, no revocation
  list. A `Principal` is a *reading* of somebody else's credential.
- **Nothing about revoking a token.** A verified token is valid until its `exp`, which is why
  one without an `exp` is refused. Shared-signals mechanisms exist and v0.3 implements none.
- **Nothing about hot-reloading a grant.** Revocation and expiry are live; an edit to the file
  takes effect when the process next loads it. `docs/authority.md` says so, and the README does
  not imply otherwise.

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
