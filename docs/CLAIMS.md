# Claims

Every sentence of the README's opening paragraph, mapped to the code that implements it and
the test that proves it.

**This table is regenerated at every release.** A claim that loses its code or its test is
removed from the README in the same commit — the README is not allowed to describe behaviour
that no longer ships. If you find a row here that does not hold against the version you
installed, that is a bug: please open an issue.

Regenerated for: **v0.5.0**, with rows re-pointed on `main` since. Line numbers refer to the current `main`, not to the tag, and item 9 of v0.6 regenerates the file wholesale. `test_the_claims_table_line_numbers_point_at_what_they_name` resolves every one of them against the line it cites, which is why they are re-pointed rather than left to rot.

## The opening paragraph

> CTRLRun is open-source infrastructure for controlling consequential AI-agent actions. You
> decide, per action, what an agent can do autonomously, what requires human approval, and
> what is blocked. CTRLRun binds approvals to the exact action, blocks duplicate execution
> attempts for the same logical effect, stops blind retries when an execution outcome is
> uncertain, and records what actually happened. Autonomy belongs to the action, not the
> agent.

| Claim | Code | Proof |
|---|---|---|
| "Transaction safety for AI-agent actions" | `Control.execute` — `control.py:528` drives reserve → execute → commit/fail/ambiguous over `EffectState` (`effect.py:67`) | `test_T1_a_lost_response_leaves_the_effect_ambiguous` |
| "Agents can retry. Your refund shouldn't." | A retry against an `AMBIGUOUS` key is refused — `effect.py:156`, the one place `plan_reservation` decides it for every store | `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote` |
| "open-source" | Apache-2.0 `LICENSE`; `license = "Apache-2.0"` in `pyproject.toml` | n/a — licence file |
| "controlling consequential AI-agent actions" | `@protect` — `control.py:1807`; `context()` — `control.py:132` | `test_T6_unknown_action_raises_ActionDenied_with_reason_unknown_action` |
| "what an agent can do autonomously, what requires human approval, and what is blocked" | `Decision.ALLOW / APPROVE / DENY` — `policy.py:125`. Exactly three members. | `test_T6_unknown_action_is_denied_with_reason_unknown_action` |
| "binds approvals to the exact action" | `ApprovalRequest.action_hash` — `approval.py:81`; `consume_approval(approval_id, action_hash)` — `approval.py:278` | `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch`, `test_T2_any_material_change_invalidates_the_approval` |
| "blocks duplicate execution attempts for the same logical effect" | `reserve_effect` — `state.py:353`, decided inside the `BEGIN IMMEDIATE` of `_authorize_and_reserve` (`state.py:780`) against `effect_key TEXT PRIMARY KEY` (`migrations.py:107`; `COLLATE "C"` on Postgres, §4.4) | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` (8 OS processes), `test_T3_the_fake_remote_is_called_exactly_once` |
| "stops blind retries when an execution outcome is uncertain" | Only `NotExecuted` maps to `FAILED` — `control.py:959`. Every other exception, timeouts included, yields `AMBIGUOUS`. | `test_T1_a_blind_retry_writes_a_blocked_receipt`, `test_T1_the_ambiguous_record_survives_the_blocked_retry` |
| "records what actually happened" | `ReceiptResult` — `receipt.py:392`; `Event` — `receipt.py:392`; the store is authoritative — `append_event` — `state.py:513`; the JSONL export — `JSONLEventSink` — `receipt.py:392` | `test_T11_every_demo_receipt_carries_every_field_in_the_spec`, `test_T11_every_demo_receipt_parses_back_into_a_Receipt` |
| "Autonomy belongs to the action, not the agent." | `Policy.evaluate(action)` — `policy.py:368` — passes only the action's **name and arguments** to `_ActionPolicy.evaluate` (`policy.py:254`), whose signature has no principal in it. A rule cannot read who is acting even by accident. `agent_eq` and `user_eq` are refused at load by `RESERVED_ARGUMENTS` (`policy.py:104`) rather than silently matching nothing. | `test_T6_an_action_name_is_matched_exactly`, `test_a_condition_naming_an_action_field_is_refused_at_load` |


## What v0.2 adds to the README

Every sentence the README gained in this release, mapped the same way.

| Claim | Code | Proof |
|---|---|---|
| "Point the client at the gateway instead of at the tool server" | `Gateway.handle` — `gateway/server.py:421`; `serve` — `gateway/__init__.py:40` | `test_T19_the_upstream_receives_the_canonical_arguments` |
| "No agent changes" | `INTERCEPTED_METHOD` is `tools/call` and every other method is relayed unchanged — `gateway/mcp.py:40` | `test_a_non_intercepted_method_is_relayed_with_no_ctrlrun_outcome` |
| "The gateway prints … every action in your policy that has no `effect:` template" | `_announce` — `gateway/__init__.py:145` (it moved out of `cli/main.py` with SPEC-v0.3 §8.4, which added the environment, the identity provider and the authority section to the same block) | `test_the_startup_block_names_the_environment_identity_and_authority`, `test_the_startup_block_says_so_when_there_is_no_authority_section` |
| "Tools become actions named `mcp.<alias>.<tool>`" | `Gateway._intercept` — `gateway/server.py:459` | `test_T19_the_action_is_named_for_the_alias_and_the_tool` |
| "Declare their effect and resource templates there" | `Policy.effect_template` / `resource_template` — `policy.py:358`; `McpOptions` — `policy.py:231` | `test_T16_a_v2_document_loads_and_exposes_its_templates`, `test_T16_a_decorator_and_a_policy_template_produce_the_same_action_hash` |
| "Everything but `tools/call` is relayed untouched" | `parse_request(...).intercept` — `gateway/mcp.py:84` | `test_every_other_method_is_relayed_not_intercepted` |
| "A lost response over the wire blocks the retry exactly as it does in-process" | `classify` — `gateway/outcome.py:144`, translated into v0.1 §5.5's own vocabulary by the gateway's executor | `test_T23_the_identical_call_sent_again_is_refused_and_the_upstream_called_once` |
| "the only thing besides a human permitted to move a record out of `AMBIGUOUS`" | `Control._reconciled` — `control.py:1306`; `RECONCILED_STATES` — `effect.py` | `test_T13_a_hook_answering_not_executed_moves_the_record_to_failed`, `test_T14_a_hook_answering_committed_refuses_the_retry_as_a_duplicate` |
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
| "authority first" | `Control.execute` evaluates authority before policy and a denial appends `AUTHORITY_DENIED` and never `POLICY_EVALUATED` — `control.py:434` | `test_T74_a_denial_leaves_no_pending_approval_request` |
| "narrow it at runtime with `ctrlrun delegate`" | `Control.delegate` — `control.py:1480`; `Authority.plan_delegation` — `authority.py:878`; `ctrlrun delegate` — `cli/main.py:919` | `test_t75_the_delegation_authorizes_an_action_within_its_limits` |
| "provably a subset of its parent on every dimension, at creation and again at every evaluation" | `contained_dimension` — `authority.py:604` — runs from `plan_delegation` (`authority.py:878`) **and** from the chain walk in `Authority.evaluate` (`authority.py:807`) | `test_t76_each_dimension_violated_alone`, `test_t77b_a_narrowed_parent_narrows_its_children` |
| "Omitting a dimension the parent constrains is rejected, not inherited" | `contained_dimension` treats an absent child dimension as unconstrained and therefore wider — `authority.py:604`; the subject half is `_subject_contained` (`authority.py:635`) | `test_t81_omission_is_not_unlimited`, `test_T73b_a_subject_addressed_to_every_principal_is_refused`, `test_t76_each_dimension_violated_alone` |
| "`ctrlrun revoke` cuts a chain of any depth with one write" | `Control.revoke` — `control.py:1496` — writes one row (`state.py:1166`) and visits no children; every evaluation walks to the root | `test_t78_a_revoked_parent_denies_its_grandchild`, `test_put_delegation_is_never_an_upsert` |
| "`mode: observe` … records what *would* have been blocked, without blocking anything" | `_parse_mode` — `policy.py:405`; `Control._observed` — `control.py:701`; `_WouldHave` — `receipt.py:190`; `ReceiptResult.OBSERVED` — `receipt.py:190` | `test_T82_observe_executes_what_enforce_would_deny`, `test_T83_a_duplicate_is_recorded_and_still_runs` |
| "One top-level line" | `mode:` is refused anywhere but the top level — `reject_nested_mode`, `policy.py:424` | `test_T84_mode_is_refused_anywhere_but_the_top_level` |
| "`ctrlrun stats` gives you the numbers" | `stats` — `cli/main.py:684`; counted from `would_have.blocked_reason` and nothing else | `test_T86_stats_counts_what_observe_mode_recorded`, `test_T86_stats_reaches_no_network` |
| "It is not a dry run: it executes" | `_observed` runs the executor on every path, including the ones enforce mode would have refused — `control.py:701` | `test_T82_observe_executes_what_enforce_would_deny`, `test_T83_an_executor_that_fails_on_a_held_key_still_writes_the_record` |
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

- **Nothing about ACS conformance.** `ctrlrun.acs.AcsControlHook` (`acs.py:84`) exists and is
  tested (T51–T55), but at the ACS commit read there is no reference implementation and no
  conformance suite. "ACS-compatible" appears nowhere in the README, in docstrings, or in CLI
  output. `docs/ACS.md` says what was read and where the standard is silent.
- **Nothing about exactly-once execution.** Unchanged from v0.1, and still the honest line:
  CTRLRun guarantees it will not *knowingly* execute the same logical effect twice, and that
  it will never treat an unknown outcome as a failure.

## What v0.4 adds to the README

The `ctrlrun verify` section, the badge, and the sentence about what the badge does not mean.

| Claim | Code | Proof |
|---|---|---|
| "It runs the kernel's own failure scenarios against the configuration in front of it" | `ctrlrun.verify.run` — `verify/__init__.py:112`; the eleven guarantees — `GUARANTEES` — `verify/guarantees.py:39`; the scenarios — `verify/scenarios.py` | `test_T100_the_authority_example_passes_every_non_authority_guarantee` (10/10), `test_T100_a_v1_document_with_no_templates_and_no_grants` |
| "in a scratch store, with fake executors, and no network" | One scratch store per guarantee under a temporary directory — `verify/scenarios.py`, `Engine.control`; `state_path()` is never called and `Control.from_file()` is never used | `test_T103_the_operators_store_is_byte_identical_before_and_after`, `test_T103_a_store_that_does_not_exist_is_not_created`, `test_T107_a_full_run_completes_with_no_network` |
| "Your `.ctrlrun/state.db` is byte-identical before and after" | The scratch path is a `tempfile.mkdtemp` removed in a `finally` — `verify/__init__.py` | `test_T103_the_operators_store_is_byte_identical_before_and_after` (SHA-256 and `st_mtime_ns`), `test_T103_CTRLRUN_STATE_is_not_read_and_not_created` |
| "Not applicable is not a pass" | `Report.applicable` is passes plus failures — `verify/report.py`; every N/A reason is a statement about the document — `verify/guarantees.py` | `test_T101_a_policy_with_no_approve_rule_makes_G1_and_G2_not_applicable`, `test_T102_a_policy_with_no_effect_templates_makes_G3_G4_and_G5_not_applicable` |
| "`5/5 (5 not applicable)`, never `10/10`" | `Report.summary_line` — the N/A ids are a separate sentence, never a parenthesis inside the fraction | `test_T113_the_summary_is_the_last_line_and_names_the_not_applicable_ids` (asserts `10/10` appears nowhere) |
| "There is no flag that folds one into the count" | There is no such parameter on `run()` (§9.1 freezes the signature) and no such option on the CLI | `test_T101b_zero_applicable_guarantees_is_not_a_pass` — `0/0` exits **2** |
| "The badge means the **declared guarantees pass**" | `badge_from_document` — `verify/report.py`; the phrase is the first sentence under `docs/verify.md#what-the-badge-means` | `test_T119_the_rendered_badge_text_is_exactly_CTRLRun_verified_N_over_M`, `test_T119_the_link_target_carries_the_exact_phrase` |
| "It does not mean secure, safe, compliant, certified or audited" | Those words appear in `docs/verify.md` only inside the sentence that refuses them, and nowhere in the badge, the summary, `action.yml` or the workflow | `test_T119_no_claim_uses_the_forbidden_vocabulary`, `test_T119_the_action_and_the_workflow_make_no_forbidden_claim` |
| "There is a GitHub Action" | `action.yml` at the repository root — composite, one verify run, summary and badge rendered from its JSON | `test_T118_the_action_is_a_composite_action_at_the_repository_root`, and CI's own `verify` job against both example configurations |

And the four things this section deliberately does **not** claim, each with the test that keeps
it honest:

| Not claimed | Why | Where the limit is asserted |
|---|---|---|
| That verify checks the operator's executors | It never calls the function behind `@protect` and never imports the module it lives in | `docs/verify.md`, "What it does not mean"; `THREAT_MODEL.md`, "Known v0.4 limitations" |
| That a green badge means the configuration is a good one | The guarantees are about the kernel doing what it says *under* that configuration | `test_T119_no_claim_uses_the_forbidden_vocabulary` |
| That a guarantee reported N/A was checked | It was not, and the reason is on the line | `test_T113_every_not_applicable_line_carries_its_reason` |
| That a partial run means anything about the whole | `--only` writes no badge at all | `test_T120_a_partial_run_writes_no_badge` |

## Two claims stated as limits

The README also makes two negative claims. They matter as much as the positive ones.

| Claim | Where it holds |
|---|---|
| "CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control." | Stated, not implemented — see `THREAT_MODEL.md`, "Out of scope". CTRLRun never asserts what a remote did; only `NotExecuted`, raised by the executor, claims that. |
| "It will not *knowingly* execute the same logical effect twice, and will never treat an unknown outcome as a failure." | `effect.py:191` (refuse retry on `AMBIGUOUS`) and `control.py:959` (only `NotExecuted` → `FAILED`) |

## Demo output

The README quotes `ctrlrun demo` verbatim.
`test_the_readme_demo_section_quotes_the_demo_output_verbatim` runs the demo and asserts every
line it prints appears in the README, masking only the generated approval ids. A change to the
demo's output that nobody carried across fails there rather than shipping a README that lies.

## How these line numbers are kept honest

They are not, automatically — a citation is prose, and prose drifts. Every row above was
re-derived against the tree at the tag named at the top of this file by reading the line each
one names.

This pass moved **fifteen** of them, and none for an interesting reason: the v0.1 and v0.2
rows were written against v0.3.0 and the files have grown since. The two that had drifted
*semantically* were fixed in the previous pass and still point where their sentences say —
the `BEGIN IMMEDIATE` citation at the reservation path rather than `grant_approval`'s, and the
"no principal" claim at the public `Policy.evaluate` rather than the private
`_ActionPolicy.evaluate`. One row moved between files: "blocks duplicate execution attempts"
cited `state.py` for the `AMBIGUOUS` refusal, which now lives in `effect.py`'s
`plan_reservation`, decided once for both stores.

If you are regenerating this file, re-derive every row. Do not carry one forward on trust.

**And from v0.5, you do not have to take that on trust either.**
`test_the_claims_table_line_numbers_point_at_what_they_name` resolves every `file.py:NNN` in
this document against the line it cites and fails if the symbol the cell names is not on it.
It found **nine** stale references the first time it ran, four of which pointed at a string
literal, a comment or the middle of another function. The instruction above had been followed
by hand at three releases and the table had drifted anyway, which is the argument for the test
rather than against the instruction.

## What v0.5 adds to the README

The adapter section. Every sentence, mapped the same way.

| Claim | Code | Proof |
|---|---|---|
| "You probably do not need an adapter" | Three ways in, and `@protect` (`control.py:1807`) covers this process while the gateway covers MCP — an adapter buys only the interrupt | `test_T139_the_adapter_section_says_when_you_do_not_need_one_up_front` |
| "the adapter reuses it and reimplements nothing" | `FrameworkInterrupt` — `adapter.py:180` — is a Protocol with one method returning a value; it holds no state and writes nothing | `test_T135b_the_adapter_reuses_the_sdks_primitive_and_reimplements_nothing` |
| "One core provider writes the grant" | `InterruptApprovalProvider.wait` — `adapter.py:254` — calls `grant_approval` / `deny_approval`, and an adapter calls neither | `test_T130_each_broken_fixture_fails_the_suite_named_for_it` |
| "An adapter never constructs one, and never supplies a principal" | `needs_approval` — `adapter.py:408` — resolves the principal from the `Control` so no adapter builds an `Action` | `test_T129_no_public_callable_takes_a_principal`, `test_T129_the_module_exposes_no_way_to_construct_a_control` |
| "prevention" / "attribution" | `carries_approved_arguments` gates §3.4's rebuild in `_check_answer` — `adapter.py:320` | `test_T137b_the_readme_says_the_binding_is_attribution_and_why` |
| "Adapters ship on their own version line" | `adapters/*/pyproject.toml`, never in the `ctrlrun` wheel or sdist | `test_T136_the_ctrlrun_distributions_contain_no_adapter` |
