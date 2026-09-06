---
title: "Claims"
description: "Every sentence of the README mapped to the code that implements it and the test that proves it, re-derived at every release."
---

Every sentence of the README that asserts something about the shipped code, mapped to the code
that implements it and the test that proves it.

**This table is regenerated at every release.** A claim that loses its code or its test is
removed from the README in the same commit — the README is not allowed to describe behaviour
that no longer ships. If you find a row here that does not hold against the version you
installed, that is a bug: please open an issue.

Regenerated for: **v0.6.0**. Line numbers refer to the commit this file was regenerated on, and `scripts/repoint-claims.py` re-derives them for the commits in between — it **refuses** rather than guessing when a symbol has no definition, because a row pointing at a docstring that happens to contain the right word makes the guard green and the claim false. `test_the_claims_table_line_numbers_point_at_what_they_name` resolves every one against the line it cites.

The rows follow the README's sections. `docs/capabilities.yaml` names one row per capability
by its quoted claim, and `tests/test_docs_audit.py` fails if a named row is not here.

## The header

> The last check before an AI agent does something it can't undo. Autonomy belongs to the
> action, not the agent. A consequential action happens at most once, exactly as approved, and
> leaves a receipt — and when the outcome is unknown, CTRLRun says so instead of guessing.
> A Python library that sits between the decision to act and the call that acts.

| Claim | Code | Proof |
|---|---|---|
| "The last check before an AI agent does something it can't undo." | `Control.execute` — `control.py:542` — resolves the principal, evaluates authority and policy, consumes the approval and reserves the effect key **before** the executor runs; nothing in the wrapper calls the function first | `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote`, `test_T3_the_fake_remote_is_called_exactly_once` |
| "Autonomy belongs to the action, not the agent." | `Policy.evaluate(action)` — `policy.py:678` — passes only the action's **name and arguments** to `_ActionPolicy.evaluate` (`policy.py:495`), whose signature has no principal in it. A rule cannot read who is acting even by accident. `agent_eq` and `user_eq` are refused at load by `RESERVED_ARGUMENTS` (`policy.py:150`) rather than silently matching nothing. | `test_T6_an_action_name_is_matched_exactly`, `test_a_condition_naming_an_action_field_is_refused_at_load` |
| "A consequential action happens at most once, exactly as approved, and leaves a receipt — and when the outcome is unknown, CTRLRun says so instead of guessing." | At most once: `plan_reservation` — `effect.py:163`. Exactly as approved: the approval is bound to `action_hash` and consumed with the reservation — `_authorize_and_reserve` — `state.py:780`. Or not at all: a refusal raises before the executor — `Control.execute` — `control.py:542`. Says so instead of guessing: only `NotExecuted` maps to `FAILED` — `control.py:1036` — and everything else is `AMBIGUOUS`. A receipt: `Receipt` — `receipt.py:245`. **This sentence read *happens once … or not at all* until 0.6**, a two-way disjunction that excluded the third outcome the product exists for: a lost reply is neither, and the README's own first section says so. | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked`, `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch`, `test_T1_a_lost_response_leaves_the_effect_ambiguous`, `test_T11_every_demo_receipt_carries_every_field_in_the_spec` |
| "A Python library that sits between the decision to act and the call that acts." | `@protect` — `control.py` — wraps the callable that acts, and `Control.execute` runs every check before invoking it. The category noun was on `docs/index.mdx` and in `pyproject.toml`'s `description` and nowhere in the README until 0.6, so a reader had to infer what CTRLRun **is** from three slogans. | `test_the_header_carries_the_fixed_copy_and_the_five_badges`, `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote` |
| "Runs in production on a single file, or on Postgres across hosts" | SQLite: `SQLiteStateStore` reserves inside the `BEGIN IMMEDIATE` of `_authorize_and_reserve` — `state.py:780` — which is a write lock on the file and holds across OS processes. Postgres: `PostgresStateStore` over `UNIQUE(effect_key)` with `INSERT … ON CONFLICT DO NOTHING` and checked row counts (SPEC-v0.6 §4.2), the same `StateStore` protocol, extended by nothing | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` (8 OS processes, both backends), `test_T141_the_shipped_backends_pass`, `test_T154_postgres_passes_the_store_conformance_suite` |

## The refund that happened twice

| Claim | Code | Proof |
|---|---|---|
| "A lost reply is `AMBIGUOUS`, never `FAILED`, and a retry against an `AMBIGUOUS` effect is refused — until a human, or a `reconcile` hook, says what happened." | Only `NotExecuted` maps to `FAILED` — `control.py:1036`; a retry against an `AMBIGUOUS` key is refused by `plan_reservation` — `effect.py:163`; the two things permitted to move the record on and nothing else — `resolve` — `cli/main.py:453` — and `Control._reconciled` — `control.py:1456` | `test_T1_a_lost_response_leaves_the_effect_ambiguous`, `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote`, `test_T160_there_is_no_reaper`, `test_T13_a_hook_answering_not_executed_moves_the_record_to_failed` |
| "The customer is refunded twice, and nothing in the stack noticed." — said of a stack without CTRLRun; the demo runs the same sequence with it, and counts the calls the remote received | `ctrlrun demo` scenario 1, which retries against a fake remote that counts its calls and prints the count | `test_T3_the_fake_remote_is_called_exactly_once`, `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote` |

## Protect your first action

| Claim | Code | Proof |
|---|---|---|
| the starter says a namespace delete needs a human, and holds a refund by amount | `EXAMPLE_POLICY` — `cli/main.py` — is `ctrlrun.example.yaml`, byte for byte | `test_the_shipped_example_policy_is_the_one_in_the_repository`, `test_the_shipped_example_policy_loads_and_evaluates` |
| the `ctrlrun init` and decorator blocks, and the `else:` that fails if the delete ran without a human | the blocks are `runnable` and run in one temporary directory, in document order, offline | `test_the_readme_and_docs_snippets_run` |
| the six-row table: allowed, `ApprovalRequired`, `ApprovalMismatch`, `ActionDenied`, `AmbiguousEffect`, `DuplicateEffect` | one exception per row — `errors.py`; each is the matrix row that names it, below | the "What it guarantees" rows, and `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch`, `test_T6_unknown_action_is_denied_with_reason_unknown_action`, `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote`, `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` |

## The problem, and how it works

| Claim | Code | Proof |
|---|---|---|
| "A lost reply is `AMBIGUOUS`, never `FAILED`, and a retry against an `AMBIGUOUS` effect is refused." | Only `NotExecuted` maps to `FAILED` — `control.py:1036`; `plan_reservation` — `effect.py:163` | `test_T1_a_lost_response_leaves_the_effect_ambiguous`, `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote` |
| "reserved atomically across processes and hosts; one worker wins" | `reserve_effect` — `state.py:353`; `PostgresStateStore.reserve_effect` — `postgres.py:528` | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked`, `test_T154_postgres_passes_the_store_conformance_suite` |
| "bound to the hash of the exact action a human saw, used once, and refused for anything else" | `_authorize_and_reserve` — `state.py:780` | `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch`, `test_T4_replaying_the_approval_raises_ApprovalMismatch_with_reason_consumed` |
| "An action the policy does not list is denied" | `Policy.evaluate` — `policy.py:678` | `test_T6_unknown_action_is_denied_with_reason_unknown_action` |
| "Authority first ... then policy" / "authority first" | `Control.execute` evaluates authority before policy and a denial appends `AUTHORITY_DENIED` and never `POLICY_EVALUATED` — `control.py:542` | `test_T74_a_denial_leaves_no_pending_approval_request` |
| "Neither axis reads the agent's instructions" | `Policy.evaluate` — `policy.py:678` — sees the action's name and arguments; `Authority.evaluate` — `authority.py:807` — sees the action and the principal; neither is handed a prompt, a message or a tool result | `test_T6_an_action_name_is_matched_exactly`, `test_T67_a_principal_with_no_grant_is_denied` |
| "canonical arguments (sorted keys, no floats) ... Its SHA-256 is the action hash" | `canonicalize` / `action_hash` — `action.py`; `float` refused at any depth — `action.py:71` | `test_T7_canonical_form_is_exactly_the_specified_serialization`, `test_T7_nested_dicts_are_sorted_recursively` |
| "The approval is single-use, expires, and matches nothing but that exact action." | `_authorize_and_reserve` — `state.py:780` — checks expiry at consumption | `test_T5_expiry_is_checked_at_consumption_not_only_at_grant`, `test_T4_replaying_the_approval_raises_ApprovalMismatch_with_reason_consumed` |
| "Only `NotExecuted`, raised by you, means `FAILED`." | `control.py:1036`; `NotExecuted` — `errors.py:157` | `test_T1_a_lost_response_leaves_the_effect_ambiguous` |
| "the hash of the policy that decided it, chained to the receipt before it" | `Policy.policy_hash` — `policy.py:559`; `prev_hash`, `GENESIS_HASH` for the first — `receipt.py:284` | `test_T172_every_receipt_carries_the_hash_and_the_declared_version`, `test_T164_an_altered_receipt_is_content_altered_at_its_seq` |

## Three ways to use it

| Claim | Code | Proof |
|---|---|---|
| "You probably do not need an adapter" | Three ways in, and `@protect` (`control.py:1967`) covers this process while the gateway covers MCP — an adapter buys only the interrupt | `test_T139_the_adapter_section_says_when_you_do_not_need_one_up_front` |
| "`ctrlrun init` writes a starter" | `init` — `cli/main.py:275` | CI's `package` job runs `ctrlrun init` from the wheel and asserts `ctrlrun.yaml` exists |
| "The human runs `ctrlrun approve <request id>` and the agent calls again inside `ctrlrun.with_approval(request_id)`" | `approve` — `cli/main.py:299`; `with_approval` — `control.py:164`; `ApprovalRequired` (`errors.py:88`) carries `request_id` | `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch` (the granted path first), `test_T4_replaying_the_approval_raises_ApprovalMismatch_with_reason_consumed` |
| "No agent changes" | `INTERCEPTED_METHOD` is `tools/call` and every other method is relayed unchanged — `gateway/mcp.py:40` | `test_a_non_intercepted_method_is_relayed_with_no_ctrlrun_outcome` |
| "Point the MCP client at the gateway instead of at the tool server" | `Gateway.handle` — `gateway/server.py:369`; `serve` — `gateway/__init__.py:40` | `test_T19_the_upstream_receives_the_canonical_arguments` |
| "Tools become actions named `mcp.<alias>.<tool>`" | `Gateway._intercept` — `gateway/server.py:459` | `test_T19_the_action_is_named_for_the_alias_and_the_tool` |
| "they are declared in the policy" (effect and resource templates for a tool call) | `Policy.effect_template` / `resource_template` — `policy.py:668`; `McpOptions` — `policy.py:668` | `test_T16_a_v2_document_loads_and_exposes_its_templates`, `test_T16_a_decorator_and_a_policy_template_produce_the_same_action_hash` |
| "Everything but `tools/call` is relayed untouched" | `parse_request(...).intercept` — `gateway/mcp.py:84` | `test_every_other_method_is_relayed_not_intercepted` |
| "A lost response over the wire blocks the retry exactly as it does in process" | `classify` — `gateway/outcome.py:144`, translated into v0.1 §5.5's own vocabulary by the gateway's executor | `test_T23_the_identical_call_sent_again_is_refused_and_the_upstream_called_once` |
| "the gateway prints, on the line that starts it, every action in your policy that has no `effect:` template" | `_announce` — `gateway/__init__.py:145` | `test_the_startup_block_names_the_environment_identity_and_authority`, `test_the_startup_block_says_so_when_there_is_no_authority_section` |
| "route an `approve` decision through **the framework's own interrupt**" | `FrameworkInterrupt` — `adapter.py:180` — is a Protocol with one method returning a value; it holds no state and writes nothing | `test_T135b_the_adapter_reuses_the_sdks_primitive_and_reimplements_nothing` |
| "one core provider writes the grant through the same calls `ctrlrun approve` makes" / "There is never a second place to say yes" | `InterruptApprovalProvider.wait` — `adapter.py:254` — calls `grant_approval` / `deny_approval`, and an adapter calls neither | `test_T130_each_broken_fixture_fails_the_suite_named_for_it` |
| "an adapter never constructs one and never supplies a principal" | `needs_approval` — `adapter.py:408` — resolves the principal from the `Control` so no adapter builds an `Action` | `test_T129_no_public_callable_takes_a_principal`, `test_T129_the_module_exposes_no_way_to_construct_a_control` |
| "prevention" / "attribution" | `carries_approved_arguments` gates §3.4's rebuild in `_check_answer` — `adapter.py:320` | `test_T137b_the_readme_says_the_binding_is_attribution_and_why` |
| "Adapters ship on their own version line" | `adapters/*/pyproject.toml`, never in the `ctrlrun` wheel or sdist | `test_T136_the_ctrlrun_distributions_contain_no_adapter` |

## Write down what the agent may do

| Claim | Code | Proof |
|---|---|---|
| "cheap to undo is autonomous, anything that leaves the building needs a human, money is by amount with both ends bound" | `Decision` — `policy.py:234` — is exactly `allow`, `approve`, `deny`; rules match first-wins over `Condition` (`policy.py:298`) with the operators `eq`, `neq`, `in`, `lt`, `lte`, `gt`, `gte` — `_OPERATORS` — `policy.py:83` | `test_T6_an_action_name_is_matched_exactly`, `test_T176_the_operators_behave_as_they_do_everywhere_else` |
| "Unknown actions are denied; there is no default-allow." | `Policy.evaluate` — `policy.py:678` | `test_T6_unknown_action_is_denied_with_reason_unknown_action` |
| "Amounts are integer minor units; floats are rejected outright" | `float` refused at any depth — `action.py:71` | `test_T7_canonical_form_is_exactly_the_specified_serialization` |
| "The policy cannot see who is asking — deliberately, since v0.1" | `Policy.evaluate` still takes only the action's name and arguments; `RESERVED_ARGUMENTS` — `policy.py:150` — refuses `agent_eq` and every other principal-addressing condition at load, in a document of **every** schema version | `test_T74b_a_reserved_name_in_a_policy_rule_is_a_load_error`, `test_T74b_a_reserved_name_in_a_grant_constraint_is_a_load_error` |
| "the second axis, `authority:`" | `Authority.evaluate` — `authority.py:807`; `Control._authority_result` — `control.py:408` | `test_T67_a_principal_with_no_grant_is_denied` |
| "opt-in, and then fail-closed" | `_optional_authority` returns `None` for a document with no section — `control.py`; `Control.authority is None` is v0.2 behaviour exactly | `test_T66_a_document_with_no_authority_section_leaves_control_authority_none`, `test_T66_no_authority_event_is_appended_without_a_section`, and T66's session-wide guard in `tests/conftest.py` |
| "every principal needs a grant and no grant means denied" | `NO_AUTHORITY` — the fail-closed default of `Authority.evaluate` (`authority.py:807`), reached for reads and for actions with no effect key alike | `test_T67_an_action_the_policy_allows_outright_still_needs_a_grant` |
| "A grant carries no `decision:`" | `_GRANT_KEYS` — `authority.py` — is a closed set that does not contain `decision` | `test_T73b_grant_refuses_what_the_loader_refuses` |
| "combine as the **stricter of the two**" | `Control.evaluate` returns the combined result — `control.py`; a denial on either axis is a denial | `test_T70_the_stricter_of_the_two_wins` |
| "narrow it at runtime with `ctrlrun delegate`" | `Control.delegate` — `control.py:1634`; `Authority.plan_delegation` — `authority.py:878`; `ctrlrun delegate` — `cli/main.py:826` | `test_t75_the_delegation_authorizes_an_action_within_its_limits` |
| "provably a subset of its parent on every dimension, at creation and again at every evaluation" | `contained_dimension` — `authority.py:604` — runs from `plan_delegation` (`authority.py:878`) **and** from the chain walk in `Authority.evaluate` (`authority.py:807`) | `test_t76_each_dimension_violated_alone`, `test_t77b_a_narrowed_parent_narrows_its_children` |
| "omitting a dimension the parent constrains is rejected rather than inherited" | `contained_dimension` treats an absent child dimension as unconstrained and therefore wider — `authority.py:604`; the subject half is `_subject_contained` (`authority.py:635`) | `test_t81_omission_is_not_unlimited`, `test_T73b_a_subject_addressed_to_every_principal_is_refused`, `test_t76_each_dimension_violated_alone` |
| "`ctrlrun revoke` cuts a chain of any depth with one write" | `Control.revoke` — `control.py:1650` — writes one row — `revoke_delegation` — `state.py:1166` and visits no children; every evaluation walks to the root | `test_t78_a_revoked_parent_denies_its_grandchild`, `test_put_delegation_is_never_an_upsert` |
| "`mode: observe` … records what *would* have been blocked, without blocking anything" | `_parse_mode` — `policy.py:895`; `Control._observed` — `control.py:738`; `_WouldHave` — `receipt.py:191`; `ReceiptResult.OBSERVED` — `receipt.py:191` | `test_T82_observe_executes_what_enforce_would_deny`, `test_T83_a_duplicate_is_recorded_and_still_runs` |
| "One top-level line" | `mode:` is refused anywhere but the top level — `reject_nested_mode`, `policy.py:914` | `test_T84_mode_is_refused_anywhere_but_the_top_level` |
| "`ctrlrun stats` gives you the numbers" | `stats` — `cli/main.py:662`; counted from `would_have.blocked_reason` and nothing else | `test_T86_stats_counts_what_observe_mode_recorded`, `test_T86_stats_reaches_no_network` |
| "It is not a dry run: it executes" | `_observed` runs the executor on every path, including the ones enforce mode would have refused — `control.py:738` | `test_T82_observe_executes_what_enforce_would_deny`, `test_T83_an_executor_that_fails_on_a_held_key_still_writes_the_record` |

## Prove it holds in your setup

| Claim | Code | Proof |
|---|---|---|
| "runs the kernel's own failure scenarios against the configuration in front of it" | `ctrlrun.verify.run` — `verify/__init__.py:112`; the eleven guarantees — `GUARANTEES` — `verify/guarantees.py:39`; the scenarios — `verify/scenarios.py` | `test_T100_the_authority_example_passes_every_non_authority_guarantee` (11/11), `test_T100_a_v1_document_with_no_templates_and_no_grants` |
| "in a scratch store, with fake executors, and no network" | One scratch store per guarantee under a temporary directory — `verify/scenarios.py`, `Engine.control`; `state_path()` is never called and `Control.from_file()` is never used | `test_T103_the_operators_store_is_byte_identical_before_and_after`, `test_T103_a_store_that_does_not_exist_is_not_created`, `test_T107_a_full_run_completes_with_no_network` |
| "Your `.ctrlrun/state.db` is byte-identical before and after" | The scratch path is a `tempfile.mkdtemp` removed in a `finally` — `verify/__init__.py` | `test_T103_the_operators_store_is_byte_identical_before_and_after` (SHA-256 and `st_mtime_ns`), `test_T103_CTRLRUN_STATE_is_not_read_and_not_created` |
| "Not applicable is not a pass" | `Report.applicable` is passes plus failures — `verify/report.py`; every N/A reason is a statement about the document — `verify/guarantees.py` | `test_T101_a_policy_with_no_approve_rule_makes_G1_and_G2_not_applicable`, `test_T102_a_policy_with_no_effect_templates_makes_G3_G4_and_G5_not_applicable` |
| "`6/6 (5 not applicable)`, never `11/11`" | `Report.summary_line` — the N/A ids are a separate sentence, never a parenthesis inside the fraction | `test_T113_the_summary_is_the_last_line_and_names_the_not_applicable_ids` (asserts `11/11` appears nowhere in an N/A run) |
| "There is no flag that folds one into the count" | There is no such parameter on `run()` (§9.1 freezes the signature) and no such option on the CLI | `test_T101b_zero_applicable_guarantees_is_not_a_pass` — `0/0` exits **2** |
| The two quoted reports | Both are real runs; the first is asserted line by line against `run(examples/authority/payments.yaml)` and against `docs/verify.md`'s copy | `test_the_readme_quotes_the_real_verify_output`, `test_the_readme_and_the_verify_page_quote_the_same_report` |
| "means the **declared guarantees pass**" | `badge_from_document` — `verify/report.py`; the phrase is the first sentence under `docs/verify.md#what-the-badge-means` | `test_T119_the_rendered_badge_text_is_exactly_CTRLRun_verified_N_over_M`, `test_T119_the_link_target_carries_the_exact_phrase` |
| "It does not mean secure, safe, compliant, certified or audited" | Those words appear in `docs/verify.md` only inside the sentence that refuses them, and nowhere in the badge, the summary, `action.yml` or the workflow | `test_T119_no_claim_uses_the_forbidden_vocabulary`, `test_T119_the_action_and_the_workflow_make_no_forbidden_claim` |
| "There is a GitHub Action" | `action.yml` at the repository root — composite, one verify run, summary and badge rendered from its JSON | `test_T118_the_action_is_a_composite_action_at_the_repository_root`, and CI's own `verify` job against both example configurations |
| "verify has no flag that relaxes a check" | No argument and no environment variable changes what `verify` builds — SPEC-v0.4 §3.9 | `test_T101b_zero_applicable_guarantees_is_not_a_pass`, `test_T107_a_full_run_completes_with_no_network` |

And the four things the verify section deliberately does **not** claim, each with the test that
keeps it honest:

| Not claimed | Why | Where the limit is asserted |
|---|---|---|
| That verify checks the operator's executors | It never calls the function behind `@protect` and never imports the module it lives in | `docs/verify.md`, "What it does not mean"; `THREAT_MODEL.md`, "Known v0.4 limitations" |
| That a green badge means the configuration is a good one | The guarantees are about the kernel doing what it says *under* that configuration | `test_T119_no_claim_uses_the_forbidden_vocabulary` |
| That a guarantee reported N/A was checked | It was not, and the reason is on the line | `test_T113_every_not_applicable_line_carries_its_reason` |
| That a partial run means anything about the whole | `--only` writes no badge at all | `test_T120_a_partial_run_writes_no_badge` |

## The capability matrix

Rendered from `docs/capabilities.yaml`; the six rows are the six groups of the verify
catalogue, `GUARANTEES` (`verify/guarantees.py:39`).

| Claim | Code | Proof |
|---|---|---|
| "An approval is bound to the exact action; a mutated or replayed one is refused." | `action_hash` — `action.py`; the approval record stores it and `_authorize_and_reserve` compares it — `state.py:780`; single use is the `granted → consumed` transition in the same `BEGIN IMMEDIATE` | `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch`, `test_T4_replaying_the_approval_raises_ApprovalMismatch_with_reason_consumed`, `test_T5_expiry_is_checked_at_consumption_not_only_at_grant` |
| "One logical effect happens at most once, across threads, processes and hosts." | `reserve_effect` — `state.py:353`, decided inside the `BEGIN IMMEDIATE` of `_authorize_and_reserve` (`state.py:780`) against `effect_key TEXT PRIMARY KEY` (`migrations.py:107`; `COLLATE "C"` on Postgres, §4.4) | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` (8 OS processes, both backends), `test_T3_the_fake_remote_is_called_exactly_once` |
| "An unknown outcome is AMBIGUOUS, never FAILED, and blocks a blind retry." | Only `NotExecuted` maps to `FAILED` — `control.py:1036`. Every other exception, timeouts included, yields `AMBIGUOUS`. A retry against an `AMBIGUOUS` key is refused — `effect.py:163`, the one place `plan_reservation` decides it for every store | `test_T1_a_blind_retry_writes_a_blocked_receipt`, `test_T1_the_ambiguous_record_survives_the_blocked_retry`, `test_T1_a_lost_response_leaves_the_effect_ambiguous` |
| "An unknown action, a missing policy or a missing principal is denied." | Unknown action: `Policy.evaluate` — `policy.py:678` — answers `deny` for a name the document does not list. Missing or malformed policy: `Policy.from_file` — `policy.py:578` — raises `PolicyError`, and there is no `Control` without a policy. Missing principal: `_refuse_no_principal` — `control.py:1849` | `test_T6_unknown_action_raises_ActionDenied_with_reason_unknown_action`, `test_missing_policy_file_is_a_policy_error`, `test_malformed_policy_document_is_a_policy_error`, `test_T62_a_declining_provider_with_no_context_is_no_principal` |
| "With authority on, every principal needs a grant, and delegation cannot widen one." | `NO_AUTHORITY` — the fail-closed default of `Authority.evaluate` (`authority.py:807`); `contained_dimension` — `authority.py:604` — runs from `plan_delegation` (`authority.py:878`) and from the chain walk in `Authority.evaluate` | `test_T67_a_principal_with_no_grant_is_denied`, `test_t76_each_dimension_violated_alone` |
| "Every executed action leaves a portable JSON receipt" | `ReceiptResult` — `receipt.py:429`; `Event` — `receipt.py:429`; the store is authoritative — `append_event` — `state.py:513`; the JSONL export — `JSONLEventSink` — `receipt.py:429` | `test_T11_every_demo_receipt_carries_every_field_in_the_spec`, `test_T11_every_demo_receipt_parses_back_into_a_Receipt` |

## What it guarantees

| Claim | Code | Proof |
|---|---|---|
| "On SQLite that is `BEGIN IMMEDIATE`" | `_authorize_and_reserve` — `state.py:780` | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` |
| "a unique index on the effect key and compare-and-set updates whose row counts are checked" | `reserve_effect` — `postgres.py:528` — `INSERT … ON CONFLICT DO NOTHING` against `effect_key TEXT PRIMARY KEY COLLATE "C"` (`migrations.py:107`) | `test_T3_exactly_one_agent_reserves_and_seven_are_blocked` (8 OS processes, both backends) |
| "Same `StateStore` protocol, extended by nothing" | `PostgresStateStore.reserve_effect` — `postgres.py:528` — and every other method implement `v0.1 §5.3`'s frozen protocol; the decisions stay in `plan_reservation` (`effect.py:163`) | `test_T154_postgres_passes_the_store_conformance_suite` |
| "graded by the suite written for SQLite" | `ctrlrun.conformance.store.run` — `conformance/store/__init__.py:52` | `test_T140_every_fixture_fails_the_suite_named_for_it` |
| "It will not *knowingly* execute the same logical effect twice, and will never treat an unknown outcome as a failure." | `plan_reservation` — `effect.py:163` (refuse retry on `AMBIGUOUS`) and `control.py:1036` (only `NotExecuted` → `FAILED`) | `test_T1_a_blind_retry_is_refused_and_never_reaches_the_remote`, `test_T1_a_lost_response_leaves_the_effect_ambiguous` |
| "a lost connection during `COMMIT` ... are `AMBIGUOUS`" | `_resolve_lost_insert` — `postgres.py:648`; `_resolve_lost_update` — `postgres.py:995`; only `NotExecuted` maps to `FAILED` — `control.py:1036` | `test_T155_a_connection_killed_during_commit_is_resolved_by_the_re_read`, `test_T155_no_effect_is_ever_recorded_failed_by_a_lost_commit` |
| "the store re-reads the row to find out which" | The six branches, named and logged — `A2_LANDED` — `postgres.py:127` | `test_T155b_a_landed_commit_on_a_transition_is_seen_as_landed`, `test_T155d_a_commit_the_server_never_received_retries_the_insert` |
| "A crashed worker's effect stays `AMBIGUOUS` until a human runs `ctrlrun resolve` or a `reconcile` hook asks the remote what happened" | An expired lease is `AMBIGUOUS` and nothing sweeps it — `LEASE_EXPIRED` — `effect.py:63`; who resolved it — `resolved_by` — `effect.py:63`; `resolve` — `cli/main.py:453` | `test_T159_ambiguous_survives_a_restart_and_still_refuses_a_blind_retry`, `test_T160_there_is_no_reaper`, `test_T161_a_human_resolution_records_who` |
| "the only thing besides a human permitted to move a record out of `AMBIGUOUS`" | `Control._reconciled` — `control.py:1456`; `RECONCILED_STATES` — `effect.py` | `test_T13_a_hook_answering_not_executed_moves_the_record_to_failed`, `test_T14_a_hook_answering_committed_refuses_the_retry_as_a_duplicate` |
| "and only in the direction its answer points" | `"unknown"` is absent from `RECONCILED_STATES` — `effect.py` | `test_T15_a_hook_that_cannot_answer_leaves_the_record_ambiguous` |
| "Unknown action, missing policy, malformed policy, missing principal, missing or mismatched approval and inconsistent state are all `deny`." | `Policy.evaluate` — `policy.py:678`; `Policy.from_file` — `policy.py:578`; `_refuse_no_principal` — `control.py:1849`; `_authorize_and_reserve` — `state.py:780` | `test_T6_unknown_action_raises_ActionDenied_with_reason_unknown_action`, `test_malformed_policy_document_is_a_policy_error`, `test_T62_a_declining_provider_with_no_context_is_no_principal`, `test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch` |
| "No flag makes a consequential action permissive by default" | There is no such option on `Control`, on `@protect`, on the CLI or in the policy schema's closed key sets — `_TOP_LEVEL_KEYS` — `policy.py:87` | `test_T84_mode_is_refused_anywhere_but_the_top_level`, `test_T101b_zero_applicable_guarantees_is_not_a_pass` |
| "With `authority:` on, every principal needs a grant, delegation cannot widen one, and `ctrlrun revoke` cuts a chain with one write." | `Authority.evaluate` — `authority.py:807`; `contained_dimension` — `authority.py:604`; `Control.revoke` — `control.py:1650` | `test_T67_a_principal_with_no_grant_is_denied`, `test_t76_each_dimension_violated_alone`, `test_t78_a_revoked_parent_denies_its_grandchild` |
| "verifies a bearer token against a JWKS or a pinned key" | `JWTIdentityProvider._verified` — `jwt_identity.py:175`; the algorithm comes from the configured list and never from the token | `test_T88_a_valid_token_becomes_a_principal`, `test_T89_every_invalid_token_is_refused_by_cause` |
| "maps the verified claims onto a principal" | `_principal` — `jwt_identity.py` — copies only the claims named in `claim_names` | `test_T88_only_the_named_claims_reach_the_principal` |
| "`pip install \"ctrlrun[identity]\"`" | `identity = ["pyjwt[crypto]>=2.8"]` in `pyproject.toml`; imported lazily by `_jwt()` — `jwt_identity.py` | `test_T92_constructing_without_the_extra_names_the_install_command`, `test_T92_importing_ctrlrun_pulls_in_no_jwt_module` |
| "CTRLRun issues no credential and defines no identity format" | There is no minting, signing or issuing code path in the package: `jwt_identity.py` calls `decode` and never `encode` | `test_the_package_never_encodes_a_token` |
| "every receipt records which policy decided it" | `Policy.policy_hash` — `policy.py:559`, over `_canonical_policy` — `policy.py:698`; carried into the receipt by `policy_in_force` — `control.py:1595` | `test_T172_every_receipt_carries_the_hash_and_the_declared_version`, `test_T172_two_policies_sharing_a_version_string_are_told_apart_by_the_hash` |
| "the policy's declared `version:` and a hash of its canonical content" | `version:` is recorded and never authoritative; `policy_hash` is what tells two documents apart — `policy.py:559` | `test_T171_the_declared_version_alone_does_not_change_the_hash`, `test_T171_comments_key_order_and_whitespace_do_not_change_the_hash` |
| "the approval is re-checked against the policy in force at execution" | `policy_in_force` — `control.py:1595` | `test_T173_the_DENY_row_refuses_and_leaves_the_approval_granted`, `test_T173_the_ALLOW_row_invalidates_the_approval_it_did_not_need` |
| "Each receipt carries the hash of the one before it" | `Receipt.chain_hash` — `receipt.py:284`; `prev_hash` — `receipt.py:41`; `GENESIS_HASH` — `receipt.py:41`; `put_receipt` takes the head row's lock first — `postgres.py:1464` | `test_T164_an_altered_receipt_is_content_altered_at_its_seq`, `test_T164_reordering_two_receipts_is_detected_either_way` |
| "`ctrlrun receipts --verify-chain` reports it by `seq`" | `verify_chain` — `receipt.py:479`; the six names — `CHAIN_BREAKS` — `receipt.py:479` | `test_the_verify_chain_flag_reports_a_break_by_seq_and_by_name`, `test_verify_chain_reads_a_postgres_store_through_store_url` |
| "migrations are automatic at open, forward-only" | `migrate` — `migrations.py:525`, called from both stores' constructors; `HEAD` — `migrations.py:306` | `test_T147_a_v05_database_migrates_and_keeps_every_row`, `test_T150_reopening_does_not_rerun` |
| "An older binary against a newer schema refuses immediately" | `_refuse` — `migrations.py:451`; `SchemaMismatch` — `errors.py` | `test_T148_an_older_binary_refuses_a_newer_database`, `test_T148_no_other_table_is_read_before_the_refusal` |
| "Releases carry PyPI provenance attestations from GitHub Actions" | `.github/workflows/publish.yml` — `pypa/gh-action-pypi-publish` pinned at v1.14.2, which generates and uploads PEP 740 attestations by default since v1.11.0 (its release notes, read 2026-09-06), with no `attestations: false`; the `pypi` job's only permission is `id-token: write` | `test_the_publish_workflow_attests_through_trusted_publishing`, `test_every_action_is_pinned_to_a_commit` |
| "`ctrlrun approve`, `deny`, `resolve`, `inspect`, `receipts` and `stats` work from the shell against any store" | `approve` — `cli/main.py:299`; `receipts` — `cli/main.py:361`; `effects` — `cli/main.py:435`; `resolve` — `cli/main.py:453`; `inspect` — `cli/main.py:487`; `stats` — `cli/main.py:662`; every one takes `--store-url` (SPEC-v0.6 §9.4) | `test_T10_resolve_failed_permits_a_retry`, `test_T18_inspect_json_emits_the_inspection_schema`, `test_T86_stats_counts_what_observe_mode_recorded`, `test_verify_chain_reads_a_postgres_store_through_store_url` |
| "`WebhookApprovalProvider` sends an approval request to a webhook, such as Slack, and takes the answer back through the same grant calls" | `WebhookApprovalProvider` — `webhook.py:135` — one signed POST on `APPROVAL_REQUESTED`; the inbound answer lands through `grant_approval` / `deny_approval` like the CLI's | `test_T27_the_outbound_post_carries_a_signature_over_the_exact_bytes_sent`, `test_T27_the_payload_carries_what_the_spec_names` |
| "one OpenTelemetry span per action, one span event per step" | `OTelEventSink` — `otel.py:45` | `test_T29_one_action_produces_one_span_named_for_the_action`, `test_T29_every_event_becomes_a_span_event_named_by_its_type` |
| "argument values stay out of it unless you ask for them" | `OTelEventSink(arguments=...)` — `otel.py:45` | `test_T29_argument_values_are_not_attributes_by_default` |
| "Receipts in a `ctrlrun.policy/v4` document can cite the `controls:` an action satisfies" | `PolicyControl` — `policy.py:420`; `Receipt` — `receipt.py:245` — carries `controls`; attribution only, never a decision | `test_T175_the_receipt_carries_the_union_of_the_action_and_the_matched_rule`, `test_T175_a_control_is_attribution_and_changes_no_decision` |
| "a rule can condition on the `data:` labels present in an action's arguments" | `DataLabel` — `policy.py:403`; `Policy.data_scope` — `policy.py:654`; `data_scope_in` in `v0.1 §3.2`'s grammar with no new operator | `test_T176_the_derived_set_is_the_labels_of_the_arguments_actually_supplied`, `test_T176_the_derived_set_drives_a_decision` |

## What it can't, stated as limits

The README also makes negative claims. They matter as much as the positive ones.

| Claim | Where it holds |
|---|---|
| "CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control." | Stated, not implemented — see `THREAT_MODEL.md`, "Out of scope". CTRLRun never asserts what a remote did; only `NotExecuted`, raised by the executor, claims that. |
| "CTRLRun is not a transaction manager: it rolls nothing back" | There is no compensation, saga or rollback code path in the package; an `AMBIGUOUS` effect is resolved by a human or a reconcile hook and never undone — `RECONCILED_STATES` — `effect.py` |
| "The receipt chain detects alteration, and alteration is not authorship." | n/a — a disclaimer, and the scan that keeps it one: `test_T180_the_release_documents_do_not_blur_alteration_and_authorship` |
| "erasing the end of the log costs two statements" | No code — this is what the chain does **not** cover, and it is asserted rather than argued: `test_erasing_a_suffix_and_rewinding_the_head_is_two_statements_and_undetected` |
| "CTRLRun does not detect prompt injection" | No code — and that is the point. Nothing in the package reads the agent's instructions: `Policy.evaluate` takes the action's name and arguments (`policy.py:678`) and `Authority` matches a grant against the action, so neither axis has the prompt to inspect. The README's problem table claims containment of the consequence, and this row is the sentence that stops it being read as detection. | `test_T6_an_action_name_is_matched_exactly`, `test_a_condition_naming_an_action_field_is_refused_at_load` |
| "`ctrlrun verify` cannot see your executors" | `docs/verify.md`, "What it does not mean"; `THREAT_MODEL.md`, "Known v0.4 limitations" |
| "`ctrlrun scan` … reports the consequential call sites and policy entries CTRLRun is **not** covering" and "has no score, no percentage and no badge" | `ctrlrun/scan/` reads the tree with `ast` and never imports it, resolves no principal, evaluates no policy and opens no store (SPEC-scan §9.2); the limits sentence is emitted on every run including a clean one, and no percentage is computed anywhere | `test_T194_scan_never_imports_the_tree_it_reads`, `test_T205_scan_resolves_no_principal_evaluates_no_policy_and_opens_no_store`, `test_T203_the_limits_sentence_is_in_every_run_including_a_clean_one` |
| "`ctrlrun mcp-operator` … It authenticates who answered and records it; it does not check that they were entitled to." | the write tools refuse without a principal and attribute the answer to the verified one; there is no entitlement check, and `docs/SPEC-mcp-operator.md` §10 says so | `test_T184_approve_refuses_without_a_principal`, `test_T184_approve_succeeds_with_one_and_is_attributed`, `test_T183_there_is_no_flag_that_permits_a_remote_bind` |
| "it makes no claim about any standard" | No standards vocabulary outside a sentence that negates it, in the README, in a docstring or in CLI output: `test_T139_the_readme_makes_no_conformance_claim`, and `tools/docs_audit/lint.py` on every document |

## Running it in production: beyond one process

| Claim | Code | Proof |
|---|---|---|
| "the same `StateStore` protocol, extended by nothing, graded by the suite written for SQLite rather than one written for it" | `PostgresStateStore` — `postgres.py` — satisfies `StateStore` and adds no method (SPEC-v0.6 §9.1); `ctrlrun.conformance.store.SUITES` is the SQLite suite, run against both | `test_T141_the_shipped_backends_pass`, `test_T154_postgres_passes_the_store_conformance_suite` |
| "automatic at open and forward-only, with no flag that opens a database un-migrated. An older binary against a newer schema refuses immediately." | `migrate` — `migrations.py:525` — called from both stores' constructors; `_refuse` — `migrations.py:451` — raises `SchemaMismatch` on a newer `user_version` | `test_T147_a_v05_database_migrates_and_keeps_every_row`, `test_T148_an_older_binary_refuses_a_newer_database`, `test_T152b_no_flag_opens_a_database_without_migrating` |
| "an edit, a deletion from the middle or a reordering is detected and named by `seq`" | `verify_chain` — `receipt.py:479` — and the six break names in `CHAIN_BREAKS` | `test_T164_an_altered_receipt_is_content_altered_at_its_seq`, `test_T164_reordering_two_receipts_is_detected_either_way`, `test_the_verify_chain_flag_reports_a_break_by_seq_and_by_name` |
| "It detects **alteration**, which is not authorship: receipts are not signed." | No signing code, and a release scan keeps the vocabulary out | `test_T180_the_release_documents_do_not_blur_alteration_and_authorship` |
| "every receipt records the policy that decided it, so a receipt from six months ago says what the rules were" | `Policy.policy_hash` — `policy.py:559` — over the parsed decision inputs, recorded on the receipt | `test_T172_every_receipt_carries_the_hash_and_the_declared_version`, `test_T171_any_decision_input_changes_the_hash`, `test_T171_the_declared_version_alone_does_not_change_the_hash` |

## The docs site: Home and Concepts

Every claim on the docs site's Home page and Concepts pages, mapped the same way. Most of them
are the README's claims in a second place, so the rows point at the rows above rather than
restating the code; the ones that are new to the site carry their own code and proof.

| Page | Claim | Proved by |
|---|---|---|
| `index` | the hero, the promise, the demo transcript and the capability grid | the header rows above; the grid is the generator's output for `docs/capabilities.yaml`, checked by `test_the_generated_copies_match_the_generator` |
| `index` | "This site is an MCP server" | Mintlify hosts one at `/mcp` for every site (its documentation, read 2026-09-06); the URL is the site's and changes with the domain, and `test_the_home_page_carries_the_fixed_copy_and_the_generated_grid` asserts the configuration line is present |
| `get-started/install` | "installs the kernel and exactly two dependencies, `pyyaml` and `click`" | `test_core_declares_only_pyyaml_and_click`, `test_the_core_dependencies_have_not_grown` |
| `get-started/install` | "importing `ctrlrun` imports nothing from an extra" | `test_T30_a_subprocess_importing_ctrlrun_pulls_in_no_module_from_an_extra` |
| `get-started/install` | "raises `MissingDependency` with the install command in the message" | `test_a_missing_extra_raises_MissingDependency_naming_the_install_command` |
| `get-started/quickstart` | every block on the page, and the outputs shown | the blocks are `runnable` and pass `tools/docs_audit/snippets.py` in one temporary directory, in order; the outputs are pasted from one run of the same blocks |
| `concepts/action-and-hash` | "The action hash is the SHA-256 of that canonical form"; sorted keys, no whitespace, UTF-8, `float` rejected; `action_id` excluded | `canonicalize` / `action_hash` — `action.py`; `float` refused — `action.py:71`; `test_T7_canonical_form_is_exactly_the_specified_serialization`, `test_T7_nested_dicts_are_sorted_recursively`, `test_T60_claims_do_not_change_the_action_hash` |
| `concepts/decisions` | three decisions, first match wins, unknown denied, principal-addressing conditions refused at load | the "Write down what the agent may do" rows above |
| `concepts/approval-binding` | A1–A4, the mismatch leaving the approval granted, one core provider writing every grant | the matrix row "An approval is bound to the exact action…", the "Three ways to use it" adapter rows, and `test_T2_a_mutated_action_leaves_the_approval_granted` |
| `concepts/approval-binding` | the `DENY` and `ALLOW` rows when the policy changed between grant and consumption | "the approval is re-checked against the policy in force at execution" above |
| `concepts/effect-keys` | reservation atomic across threads, processes and hosts; an expired lease is `AMBIGUOUS`, never free; `COMMITTED` refuses, `FAILED` permits, `AMBIGUOUS` refuses a blind retry | the matrix row "One logical effect happens at most once…"; `LEASE_EXPIRED` — `effect.py:63`; `test_T160_an_expired_lease_frees_nothing_and_no_read_transitions_it`, `test_T8_a_failed_attempt_permits_a_retry_that_commits` |
| `concepts/outcomes-and-ambiguous` | the outcome table; only a human or a reconcile hook moves a record on, and only in the direction the answer points; nothing sweeps; a lost `COMMIT` on Postgres is `AMBIGUOUS` | the matrix row "An unknown outcome is AMBIGUOUS…", the reconciliation rows, "A crashed worker's effect stays `AMBIGUOUS`…" and the Postgres rows above; `test_T160_there_is_no_reaper` |
| `concepts/receipts-and-evidence` | the receipt's fields, the JSONL sink, the policy hash and version, the chain and what it does not prove | the matrix row "Every executed action leaves a portable JSON receipt", the receipt-chain and policy-versioning rows above, and `test_T11_every_demo_receipt_carries_every_field_in_the_spec` |
| `concepts/authority-and-delegation` | opt-in then fail-closed, no `decision:` on a grant, stricter of the two, containment at creation and at every evaluation, omission rejected, one-write revocation, identity consumed | the authority rows under "Write down what the agent may do" and "What it guarantees" above |
| `concepts/observe-mode` | executes, records `would_have`, one top-level line, counted by `ctrlrun stats`, never asks a human | the observe-mode rows above; `_observed` — `control.py:738` |
| `concepts/fail-closed` | the refusal table, one exception per row | the matrix row "An unknown action, a missing policy or a missing principal is denied.", `ActionDenied` — `errors.py:29`, `DuplicateEffect` — `errors.py:126`, `AmbiguousEffect` — `errors.py:141`, and `test_a_policy_deny_is_denied_the_same_way_as_an_unknown_action` |

## The docs site: Production

The section a stranger reads to decide whether to adopt. Two things on it are generated and one
is a statement about what has **not** happened; all three are here because each is the kind of
sentence that rots quietly.

| Page | Claim | Proved by |
|---|---|---|
| `production/index` | the readiness block — version, test count, guarantee count, the two stores, the soak, the chain, the licence | rendered by `tools/docs_audit/render_readiness.py` from `pyproject.toml`, `pytest --collect-only`, the `GUARANTEES` catalogue — `verify/guarantees.py:39` — and `research/soak/results/`; `test_the_readiness_block_is_the_generators_in_every_place_it_appears` asserts the same block in the README, the docs home and this page, and `test_the_readiness_block_refuses_a_shrunken_suite_and_accepts_a_grown_one` makes the count a floor |
| `production/index` | the **Not yet** list: no external security audit, no third-party review of the kernel, no sector packs | stated rather than measured, because nothing in a repository can measure an absence. A fourth line — *no soak of the length the roadmap asks for* — was **derived** from the published run until `SPEC-v0.6.md` §8.1 removed the duration from the criterion on 2026-09-07, which removed the thing being derived; the run's own duration is still printed on the soak line above the list. The list lives inside the generated block so it cannot be scrolled past. `test_the_not_yet_list_is_inside_the_block_and_not_below_it`, `test_the_not_yet_list_is_the_constant_and_derives_nothing_from_the_soak` and `test_the_readiness_block_does_not_report_the_soak_as_an_unmet_gate` assert all of it; removing a stated line is its own pull request with the row that makes the new sentence true |
| `production/index` | "SQLite is the default and it is production-grade on one host… Postgres is for many hosts" | the header row above; `test_the_first_line_of_the_section_says_which_store_and_why` asserts the order, because Postgres first would tell a reader with one host something false |
| `production/how-reservation-works` | the two rows: an exception before `COMMIT` is a failed write; one during it is unknown and is re-read | SPEC-v0.6 §4.3 Tables A, A1 and A2; `test_T155_a_connection_killed_during_commit_is_resolved_by_the_re_read`, `test_T155e_a_commit_the_server_never_received_re_issues_the_update`, `test_T155c_the_re_read_identity_check_is_not_an_action_id_match`, `test_T156_a_failed_re_read_refuses_to_proceed`; `test_the_two_rows_of_the_lost_commit_are_not_merged` asserts the page keeps them apart |
| `production/migrations` | five shapes, three refusals, nothing half-applies, no flag that opens a database un-migrated | the "migrations are automatic at open, forward-only" row above; `test_T149_a_half_applied_migration_rolls_back`, `test_T152b_no_flag_opens_a_database_without_migrating` |
| `production/recovery` | nothing sweeps; an expired lease is a refusal and not a reclaim; no process identity is inferable | the "A crashed worker's effect stays `AMBIGUOUS`…" row above; `test_T160_an_expired_lease_frees_nothing_and_no_read_transitions_it`, `test_T162_no_process_identity_is_inferable_from_a_record`, `test_T177d_an_expired_lease_is_displayed_as_expired_and_the_display_transitions_nothing` |
| `production/receipt-integrity` | the six names, `unchained` never a pass, a failed receipt write raises and leaves no gap | the receipt-chain rows above; `test_T164_an_altered_receipt_is_content_altered_at_its_seq`, `test_T168_pre_chain_receipts_are_unchained_and_never_a_pass`, `test_T170_a_failed_receipt_write_raises_and_leaves_no_gap` |
| `production/soak` | every number on the page, the measured duration, and what a run of that length does **not** establish | rendered by `tools/docs_audit/render_soak.py` from `research/soak/results/*.json`, which **recomputes** the exit criterion from the published counts rather than reading `exit_criterion_met` out of the same file — a weaker gate checked twice; `test_the_soak_page_is_the_render_of_the_published_results`, `test_the_soak_page_states_the_measured_duration_and_what_it_does_not_establish` and `test_the_soak_page_derives_the_criterion_and_agrees_with_the_harness` |
| `production/operations` | the signals table, and that there is nothing to run | the recovery rows above and `reference/cli`; `test_T177c_the_command_list_is_exactly_the_one_the_spec_froze` asserts the command list these are drawn from |
| the badge row | the test-count badge | written by `tools/docs_audit/render_badges.py --write-count` in CI **after** `scripts/check.sh` has passed, published to the `badges` branch only on a push to `main`; `test_ci_publishes_the_test_count_badge_after_the_suite_has_passed` asserts the order |

## Demo output

The README quotes `ctrlrun demo` verbatim.
`test_the_readme_demo_section_quotes_the_demo_output_verbatim` runs the demo and asserts every
line it prints appears in the README, masking only the generated approval and delegation ids.
The animation at the top of the README ends on lines `docs/assets/demo.expected.txt` lists, and
`tests/test_readme_assets.py` asserts each is a line the demo prints and the README quotes.

## How these line numbers are kept honest

They are not, automatically — a citation is prose, and prose drifts. Every row above was
re-derived against the tree at the tag named at the top of this file by reading the line each
one names.

The v0.6 pass moved **fifteen** of them, and none for an interesting reason: the v0.1 and v0.2
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
