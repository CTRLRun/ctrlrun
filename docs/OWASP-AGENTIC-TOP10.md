# The OWASP Top 10 for Agentic Applications, read against CTRLRun's guarantees

This is a **reading** of somebody else's taxonomy against the guarantees CTRLRun tests. It is
not a compliance claim, a conformance claim, a certification, or a statement that CTRLRun
covers the OWASP Top 10 for Agentic Applications. Four of the ten entries are not addressed by
CTRLRun at all, and they are listed by name below.

Every row maps a guarantee to an entry, and every guarantee is backed by a passing acceptance
test — so each row points at code and at a test. A row whose test disappears is a row that
comes out.

---

## The edition this was written against

| | |
|---|---|
| **Document** | OWASP Top 10 for Agentic Applications |
| **Edition** | 2026 |
| **Publisher** | OWASP GenAI Security Project, OWASP Foundation |
| **Announced** | 2025-12-09 |
| **Entry codes** | `ASI01:2026` – `ASI10:2026` |
| **Landing page** | <https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/> |
| **Read on** | 2026-09-04 |

**How the codes and titles below were derived, stated plainly because it matters.** The
published document itself is a PDF behind a download form on the landing page above and could
not be retrieved. The ten codes and titles were taken from
[`OWASP/secure-agent-playbook`](https://github.com/OWASP/secure-agent-playbook/blob/main/plugins/ai-security-skills/plays/agentic-ai-risk-assess.md),
an OWASP-owned repository that enumerates them, and corroborated against two independent
third-party summaries that agree with it in every entry. Where a third summary disagreed —
`ASI02` as "Tool Misuse & Exploitation", `ASI04` as "…Compromise", `ASI08` as "Cascading Agent
Failures" — the OWASP-owned repository's wording is the one used here.

### The ten entries

| Code | Title |
|---|---|
| `ASI01:2026` | Agent Goal Hijack |
| `ASI02:2026` | Tool Misuse |
| `ASI03:2026` | Identity & Privilege Abuse |
| `ASI04:2026` | Agentic Supply Chain Vulnerabilities |
| `ASI05:2026` | Unexpected Code Execution |
| `ASI06:2026` | Memory & Context Poisoning |
| `ASI07:2026` | Insecure Inter-Agent Communication |
| `ASI08:2026` | Cascading Failures |
| `ASI09:2026` | Human-Agent Trust Exploitation |
| `ASI10:2026` | Rogue Agents |

Anyone with the published PDF in front of them should check these ten strings against it. If
one differs, this table is what is wrong, not the mapping.

---

## Guarantee → entries mitigated

Each guarantee is one sentence about what the kernel **refuses**. The "how" column names the
mechanism, not the entry.

| Guarantee | Invariant | Entries | How |
|---|---|---|---|
| **G1** mutated approval refused | An approval is bound to one `action_hash`; presenting it for any other action is refused, and the approval is not consumed. | `ASI09:2026`, `ASI01:2026` (partly) | The approval a human granted is bound to the exact canonical form of the action they were shown, so an action that changed after the approval — by a hijacked goal or by anything else — has no approval to present. |
| **G2** replayed approval refused | An approval is single-use; the second presentation is refused and does not execute. | `ASI09:2026` | The approval record is consumed in the same transaction that admits it, so one human decision authorizes exactly one execution and a loop cannot spend it twice. |
| **G3** duplicate effect refused | A second attempt on an effect key whose record is `COMMITTED` is refused, and the remote is not called. | `ASI08:2026`, `ASI02:2026` | Effects are identified by a key derived from the action's own arguments, and a committed key is refused rather than retried — so a retry loop cannot turn one intended effect into several. |
| **G4** one winner under concurrency | Reservation is atomic across processes, not merely across threads. | `ASI08:2026` | The reservation is taken inside a `BEGIN IMMEDIATE` against a unique constraint on the effect key, so two agents that picked up the same task produce one effect and one refusal. |
| **G5** ambiguous blocks a blind retry | An executor that raises anything other than `NotExecuted` leaves the effect `AMBIGUOUS`, and the retry is refused rather than executed. | `ASI08:2026` | A lost response is recorded as an *unknown* outcome rather than a failure, and an unknown outcome is a state only a human or a reconciliation hook may leave — so the failure does not cascade into a second execution of something that may already have happened. |
| **G6** unknown action refused | Unknown action → DENY. There is no default-allow. | `ASI02:2026`, `ASI01:2026` (partly) | The policy is the list of what an agent may do; anything not written in it is refused, so a tool an agent was talked into reaching for is refused whether or not the reasoning that reached for it was sound. |
| **G7** no principal refused | An action proposed with no principal is refused, and no receipt and no events are written. | `ASI03:2026` | Every action carries a principal or it does not run, so there is no path on which an action executes with nobody attributable to it. |
| **G8** expired authority refused | A grant is authority only until its `expires_at`; after that the action it covered is denied, by name. | `ASI03:2026`, `ASI10:2026` | Authority is evaluated on every action against the clock, not at the start of a session, so an agent still running after its grant lapsed is denied on its next proposal. |
| **G9** delegation cannot escalate | A delegated grant is valid only if it is provably a subset of its parent on every dimension — and a child that **drops** a dimension its parent constrains is rejected rather than treated as unconstrained. | `ASI03:2026`, `ASI10:2026` | Containment is checked at creation and again on every evaluation by walking the chain to its root, and omission is never inheritance — so an agent handed authority cannot mint itself more of it, and a revocation anywhere in the chain cuts everything beneath it. |
| **G10** unknown exception is ambiguous | `NotExecuted` is the only outcome that means "the remote did nothing". Everything else, timeouts included, is `AMBIGUOUS`. | `ASI08:2026` | The mapping from an executor's exception to an outcome is asymmetric on purpose: a timeout is not a failure, so a framework's retry-on-error cannot be the thing that decides whether money moved twice. |
| **G11** an altered receipt is detected | Each receipt carries the hash of the one before it. Altering, deleting or reordering one breaks the chain, and the break is reported by name — `content_altered`, `link_broken`, `missing`, `head_mismatch` — and by `seq`. | `ASI06:2026`, `ASI09:2026` (partly) | The evidence an operator reads after an incident is the thing an attacker who got that far has the most reason to edit. This does not stop them: it makes a **partial** edit — one row, a deletion from the middle, a reordering — cost a rewrite of every receipt after it plus the head, rather than one statement. Two cases stay cheap and are named rather than glossed: the **last** receipt costs two writes, and a well-formed receipt **appended** at the end is not detectable at all without an external anchor, which v0.6 does not have. It is **not** a signature and says nothing about who wrote the log; somebody who can rewrite every row including the head recomputes the chain and it verifies, and `THREAT_MODEL.md` still lists a malicious administrator as out of scope. |

---

## Not covered by CTRLRun

The half that makes the table above credible. One honest sentence each; nothing aspirational.

| Entry | Title | Why not |
|---|---|---|
| `ASI04:2026` | Agentic Supply Chain Vulnerabilities | Out of scope. CTRLRun never inspects a package, a model, a tool registry or an MCP server's provenance; it decides actions, and a poisoned dependency reaches it as an ordinary caller. |
| `ASI05:2026` | Unexpected Code Execution | Out of scope. Nothing here sandboxes an interpreter or constrains what a process may run. CTRLRun sits between an agent and one remote effect, not between an agent and its own runtime. |
| `ASI06:2026` | Memory & Context Poisoning | Out of scope, and deliberately so: CTRLRun never reads a model's memory, its context or its prompt. It sees a proposed action and its arguments, which is the point at which a poisoned context has already become a concrete request. |
| `ASI07:2026` | Insecure Inter-Agent Communication | Not yet. Authority does not propagate across agent hops in this release — a grant is evaluated where the action is proposed, and there is no A2A model. `docs/ROADMAP.md` puts that in v0.7; until then, an agent handing work to another agent is outside what these guarantees say anything about. |

And the two entries where the mapping above is **partial**, with the part that is not covered
stated here rather than left implied:

| Entry | Title | Covered | Not covered |
|---|---|---|---|
| `ASI01:2026` | Agent Goal Hijack | G1 and G6 constrain what a hijacked agent can *do*: it still meets the policy, and it still cannot present an approval granted for a different action. | CTRLRun does not detect or prevent the hijack. It never sees the prompt, the plan or the reasoning, so an agent whose goal was replaced proposes actions exactly as a healthy one would — and every action inside its policy and its grants will run. |
| `ASI09:2026` | Human-Agent Trust Exploitation | G1 and G2 close the shape where an approval a human gave for one action is spent on another, or spent twice. | CTRLRun does not authenticate the *approver*, does not model separation of duties, and has no opinion on whether the human was misled into approving. A human persuaded to approve the right action for the wrong reason gets a valid approval, and the receipt records it as one. |

---

## Where the guarantees are actually checked

The mapping is only worth what the tests behind it are worth. `ctrlrun verify` runs these ten
against a configuration and reports which of them that configuration can exercise at all —
**not applicable is not a pass**, so a mapping row whose guarantee your policy cannot exercise
shows up as `N/A` with the reason rather than as a green tick. See
[`docs/verify.md`](verify.md).

Each guarantee also descends from an acceptance test in `docs/SPEC-v0.1.md §7`,
`docs/SPEC-v0.2.md §10` or `docs/SPEC-v0.3.md §10`, named in the registry and carried into
every report as `descends_from`.

---

This document is regenerated when the guarantee catalogue changes, and when OWASP publishes a
new edition. It was written against `ctrlrun.guarantees/v2` and the **2026** edition of the
OWASP Top 10 for Agentic Applications.
