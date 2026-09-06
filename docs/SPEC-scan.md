# CTRLRun — `ctrlrun scan`

This is a **delta over [`SPEC-v0.1.md`](SPEC-v0.1.md), [`SPEC-v0.2.md`](SPEC-v0.2.md),
[`SPEC-v0.3.md`](SPEC-v0.3.md), [`SPEC-v0.4.md`](SPEC-v0.4.md), [`SPEC-v0.5.md`](SPEC-v0.5.md)
and [`SPEC-v0.6.md`](SPEC-v0.6.md)**. Everything in all six still holds; this document states
only what `ctrlrun scan` adds. A reference to an earlier contract is written `v0.1 §4.2`,
`v0.4 §3.9` or `v0.6 §7.1`; a bare `§4` is a section of this document.

Tests are derived from §8. Public names added here are frozen in §9. Anything not in this
document or in v0.1–v0.6 is out of scope.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

**It is not a kernel milestone.** It reads source text and a policy document and writes a
report. It adds no table, no column, no event, no error type and no policy key, and it changes
nothing about what an action *is*. So it gates no kernel release and none gates it, and it lands
in whichever release comes next — the argument `SPEC-mcp-operator.md` makes for a subcommand,
including the part about carrying no version line of its own.

**Status: implemented.** The document was written first and its §8 tests were red before any
of it existed. Five sections carry a paragraph beginning *Found by* — each is a defect the
implementation or a run against this repository's own trees found in the design, corrected here
rather than worked around in the code.

## 1. Scope

One sentence: **`ctrlrun scan` reports the consequential call sites and policy entries in a
project that CTRLRun is not covering, so that an operator can see the gap between *installed*
and *in the path*.**

It answers one question — *what is not protected here?* — and it answers it by reading text.

### 1.1 What it is not

- **It is not a check, and a clean scan is not a verdict.** `ctrlrun verify` (`v0.4 §1`) answers
  *does the guarantee hold in my setup*, against a real store, by making the kernel refuse
  things. Scan answers *is the kernel on this path at all*, statically, and it is capable of
  missing things by construction. §4 states which things, and the report says so on every run.
- **It is not enforcement.** Nothing it prints changes what runs. An unprotected call it names
  is unprotected before and after.
- **It is not a policy checker.** Whether a policy's decisions are right is the operator's
  judgement; whether a policy loads is `Policy.from_file`'s answer and already an error.
- **It does not fix anything.** It never edits a file, never inserts a decorator and never
  writes a policy entry. A tool that rewrote the call sites it flagged would be making
  consequential changes to prove a point about consequential changes.
- **It sends nothing anywhere.** No upload, no telemetry, no service, no identifier. The report
  is printed, and `--json` writes it to standard output.
- **It does not scan a running system.** Not an MCP server's tool list, not a process, not a
  container image, not a dependency tree. §10 says why each of those is excluded rather than
  merely absent.

### 1.2 Why it exists

`@protect` is a convention, and a convention is only as good as every call site that remembers
it. The gateway (`v0.2 §6`) is a choke point and covers what reaches it; the decorator covers
what somebody decorated. The thing an operator cannot see today is **the call nobody decorated**,
and there is no command that will tell them.

This is also the smallest possible first contact with the project. Adopting CTRLRun is a
decision; running a read-only command that lists what a stack already risks is not.

## 2. What it reads

Three sources. Each is independent, each may be absent, and the report says which ran.

### 2.1 A Python source tree

`--path <dir>` (default: the working directory). Every `*.py` file under it that git tracks,
or every `*.py` file if the tree is not a checkout, excluding paths matched by `--exclude`
(repeatable glob) and, by default, `.venv/`, `venv/`, `site-packages/`, `build/`, `dist/` and
`node_modules/`.

**Scan MUST NOT import the tree it reads.** `ast.parse` on the file's bytes and nothing else.
Importing a module executes it at module scope, and a tool whose purpose is to find calls that
move money must not be the thing that makes one. This is not a performance choice and no flag
changes it; §7 says what happens to a file that will not parse.

### 2.2 A policy document

`--policy <file>` (default: `ctrlrun.yaml` if present). Loaded with the real loader, and a
document that does not load is an error (§6), not a skipped source.

Scan reads the action names, and for each the presence of an `effect:` template. It does not
call `Policy.evaluate`, and §9.2 says why that is a rule and not an implementation detail.

### 2.3 Nothing else

No network, no store, no environment. Scan opens no database: it neither reads receipts nor
writes them, so its findings are about the code as written and never about what has run.
`ctrlrun stats` is the tool that reads what has run.

## 3. What counts as a finding

Every finding names its **kind**, its **location**, and the **rule that matched**, so that a
reader can disagree with it specifically. A finding that could not be attributed to a rule is a
finding this document does not permit.

### 3.1 `unprotected_call`

A call whose target path matches the consequence vocabulary (§3.2) and which is not covered
(§3.3).

The target path of a call is the dotted name of its function: `stripe.refunds.create`,
`client.delete_namespace`, `session.execute`. A call whose function is not a name or an
attribute chain — `getattr(client, verb)()`, `handlers[name]()` — has no target path, and §4.1
says what becomes of it.

### 3.2 The vocabulary is declared, not inferred

The consequence vocabulary is a list of verbs, shipped in the distribution and printed by
`ctrlrun scan --vocabulary`. A verb matches when it appears as a whole `_`-separated word in
any segment of the target path: `create_refund` matches `refund`, `delete_namespace` matches
`delete`, `deleted_at` does not match `delete`.

A verb matches in one of two shapes, and they are separate because one rule cannot tell apart
the two things a plural means:

- **A whole segment** matches a verb or its plural. `stripe.refunds.create` is a resource
  namespace and a refund is what it creates.
- **A word inside a compound segment** matches the verb in the singular only. `create_refund` is
  a refund; `refunds_report` is a noun phrase about refunds and moves no money.

**Found by writing the tests.** A single rule made these two indistinguishable — both contain
the word `refunds` — and whichever way it fell, one of the Stripe SDK's own call shape and every
reporting function beside it was wrong.

The initial list is the README's own sentence, which is the list this project already stands
behind: **send · pay · refund · delete · deploy · grant · revoke · approve · submit · purchase
· cancel**, plus **charge · transfer · payout · merge · push · terminate · destroy · drop ·
truncate · rotate · issue · disable · remove · publish**.

**`execute` was in the first draft and is not in the list.** `cursor.execute` appears in every
project that touches a database, and a verb that matches thousands of lines buries the ones that
matter: measured against this repository's own `src/`, it was 90 of 208 findings. Found by
running the command.

`--vocabulary <file>` replaces the list with the operator's own, one verb per line. Replacing
it is not relaxing a check: scan has no check to relax, and the report names the vocabulary in
force and its length on every run, so a shortened list is visible in the output rather than in
somebody's shell history.

### 3.3 What covered means

A call is covered when any of:

1. it is lexically inside a function or method whose decorators include `protect(...)`,
   `ctrlrun.protect(...)`, or an attribute chain ending in `.protect`;
2. its target path is a bare name bound in the same module by a `def` that is itself covered by
   (1) — calling a protected function is not a finding;
3. it is inside a file the operator excluded (§2.1), which is reported as an exclusion and not
   as a clean file.

Nothing else is coverage. In particular, being inside a `with ctrlrun.context(...)` block is
**not** coverage: `context` names who is acting and decides nothing (`v0.3 §3`), and treating it
as protection would be the most dangerous false negative this tool could ship.

### 3.4 `action_without_effect`

A policy action with a `decision:` of `allow` or `approve`, no `effect:` template, **and no
`@protect(..., effect=…)` in the tree that declares it**. The gateway already prints this on the
line that starts it (`v0.2 §6.7`); scan says it statically, and for a policy nobody has started a
gateway with.

**Found by running the command.** The policy half alone flagged `examples/double-refund`, whose
decorator passes `effect="refund:{payment_id}"`. The policy's `effect:` is what the *gateway*
reads, because a tool call has no decorator to carry one; a decorated call site carries its own.
A finding that teaches a reader to add a key they already have is worse than no finding, and
T207 is the case.

### 3.5 `protected_action_not_in_policy`

A `@protect("name", …)` in the tree whose action name the policy does not list. At runtime this
is a denial — `unknown_action`, and fail-closed is working — but a call that is always denied is
automation that silently does nothing, and finding it in a scan is cheaper than finding it in an
incident. It is reported as a finding of its own kind and never as an `unprotected_call`.

### 3.6 Suppression is attribution, not relaxation

A line may carry `# ctrlrun: not-consequential — <reason>`, and the reason is required: a
suppression with no reason is a parse error for that line and the finding stands.

A suppressed finding is **not removed from the report**. It moves to a suppressed count that is
printed with the totals, and `--json` carries each suppressed finding with its reason. The rule
in `v0.4 §3.9` — no flag that relaxes a check — is about a tool whose answer is a guarantee.
Scan's answer is a list, and a list that cannot be annotated is a list that gets deleted. What
that rule forbids, and what this keeps, is a way to make the output *quieter than the truth*.

## 4. What a clean scan does not mean

**This section is the one that makes the tool honest, and its content MUST appear in the
report itself**, not only here. A run that printed `0 unprotected` and nothing else would be the
false-green problem in a new costume: a reassuring line, produced by a check that never looked.

Scan misses, by construction:

### 4.1 Calls with no target path

`getattr(client, name)()`, `handlers[key]()`, a call through a subscript. These are **counted
and reported as `undetermined`**, with their locations. They are not silently dropped, and they
are not findings either: scan does not know what they call.

A call on an expression is **not** in this category. `hashlib.sha256(text).hexdigest()` and
`"\n".join(parts)` name the method being called even though the receiver is not a name, so they
are matched against the vocabulary like any other call, and a finding says the target was
reached on an expression. `.delete()` on an expression is still a delete.

**Found by running the command.** Treating every unresolved receiver as undetermined produced
216 entries against this repository's own `src/`, of which **two** were the dynamic dispatch the
category exists for. A list that long is a list nobody reads, which makes it the same failure as
not printing one. T208 is the case.

### 4.2 Reachability

Scan reports call *sites*, not what actually runs. A flagged call in dead code is a false
positive; a protected wrapper called from a path that also calls the raw client is two separate
sites and scan sees both, which is right, but it cannot tell you which one production takes.

### 4.3 Anything outside the tree

A dependency's code, another service, another language, a shell command, an MCP tool server, a
database trigger, a scheduled job defined in a control panel. Scan reads `*.py` files under one
path. Everything a system does that is not in those files is invisible to it.

### 4.4 The gateway's own coverage

An action reaching its tools over MCP is covered by the gateway and has no decorator to find.
Scan reports the policy's actions for such a deployment (§3.4, §3.5) and cannot see the tool
server at all. A project whose protection is entirely the gateway will scan as though nothing
were protected, and the report MUST say so where a reader will meet it.

### 4.5 The sentence

Every human-readable run ends with, verbatim:

```text
This is a finder, not a proof. It read <n> files and could not determine <u> calls;
it did not read your dependencies, your other services, or any tool server behind a
gateway. A clean scan means nothing was found where it looked.
```

`--json` carries the same statement in a `limits` object. Removing it is a change to this
document.

## 5. The report

### 5.1 Human output

Grouped by kind, most severe first: `unprotected_call`, then `protected_action_not_in_policy`,
then `action_without_effect`, then `undetermined`. Each line names the file, the 1-indexed line,
the target path, and the rule. Then a totals block: files read, files that would not parse,
findings by kind, suppressed, excluded, and the vocabulary in force. Then §4.5's sentence.

### 5.2 `--json`

One object, `schema: "ctrlrun.scan/v1"`. Frozen in §9.1. It carries every finding, every
suppression with its reason, every undetermined call, every unparseable file, the totals, the
vocabulary, and the `limits` object. Nothing in the JSON is absent from the human output except
where the human output says *and N more*.

### 5.3 One producer

The human output and the JSON are two renderings of one document produced by one function, on
the rule `SPEC-mcp-operator.md §9.1` set: two producers drift, and the one that drifts is always
the one nobody reads.

## 6. Exit codes

| Code | When |
|---|---|
| `0` | The scan ran and found nothing of any kind |
| `1` | The scan ran and found at least one finding, a suppression, or an undetermined call. A suppressed finding does not move the code, and an undetermined call is a place scan could not look; neither is a clean result |
| `2` | The scan could not run: the path does not exist, the policy will not load, `--vocabulary` names a file that is not readable |

A file that will not parse is **not** exit `2` — the scan ran. It is a finding, and §7 says so.

There is no flag that turns `1` into `0`. An operator who wants a subset of kinds to fail a
build filters the JSON, where the thing being filtered is visible in the filter.

## 7. Fail-closed table

Scan makes no decision an agent depends on, so *fail closed* here means **fail loud**: every
case where it does not know is counted and printed, never skipped.

| Case | Behaviour |
|---|---|
| A file will not parse | Finding `unparseable`, with the syntax error's line. Exit `1`. Never skipped: a file scan cannot read is exactly where something would hide. |
| A file cannot be read (permissions, encoding) | Finding `unparseable`, reason recorded. Exit `1`. |
| A call has no target path | Counted as `undetermined` (§4.1), with its location. Exit `1`. |
| A decorator scan does not recognise | The function is **not** treated as covered. An unrecognised decorator is not `@protect`. |
| A decorator is `protect` with no action name literal | Covered for (1), and the action name is `undetermined` for §3.5. |
| `--policy` names a file that does not exist and was passed explicitly | Exit `2`. |
| No policy file and none passed | The policy sources are skipped, and the report says the two policy kinds were not run and why. Not a clean result for those kinds. |
| The tree contains no `*.py` file | Exit `2`, with a message naming the path and saying **no Python file was found under it**. Reporting `0 findings` for an empty scan is the false green this project keeps finding, and the message is required because an exit code alone is indistinguishable from the shell's answer to a command that does not exist — which is how T200 first passed against a tree with no `scan` in it. |
| A suppression comment has no reason | The suppression does not apply and the finding stands. |

## 8. Acceptance tests

Derived from the sections above; each names the section it enforces.

### T194 — Scan never imports the tree it reads

A fixture module whose import writes a sentinel file at module scope. After a full scan, the
sentinel does not exist. Asserts §2.1 against the one failure that would make this tool worse
than nothing.

### T195 — A protected call is not a finding, and its caller is not either

A module with `@protect("stripe.refund")` on `refund()`, calling `stripe.refunds.create`
inside, and a second function calling `refund(...)`. No `unprotected_call` is reported for
either. Asserts §3.3 (1) and (2).

### T196 — `with ctrlrun.context(...)` is not coverage

The same raw call inside a `with ctrlrun.context(agent="a"):` block and in no decorated
function is reported. Asserts §3.3's last paragraph — the false negative most likely to be
written by somebody being helpful.

### T197 — The vocabulary matches whole words in segments, and only those

`create_refund`, `delete_namespace` and `send_email` are found; `deleted_at`, `undeleted` and
`refunds_report` are not. Asserts §3.2.

### T198 — A call with no target path is undetermined, not absent and not a finding

`getattr(client, verb)()` and `handlers[key]()` appear in the `undetermined` list with their
line numbers, and in neither the findings nor the clean count. Asserts §4.1.

### T199 — A file that will not parse is a finding

A file containing a syntax error produces an `unparseable` finding naming its line, the scan
still reports on every other file, and the exit code is `1` and not `2`. Asserts §7's first row
and §6's last line.

### T200 — An empty tree is exit 2

A directory with no `*.py` file exits `2` and does not print a clean result. Asserts §7's
penultimate row.

### T201 — Suppression requires a reason, and a suppressed finding is still in the report

Three call sites: one unsuppressed, one suppressed with a reason, one with a bare
`# ctrlrun: not-consequential`. The first and third are findings; the second is in the
suppressed list with its reason, the totals name it, and the exit code is `1` in every case.
Asserts §3.6.

### T202 — Both policy-side kinds are found, and neither is an unprotected call

One policy with `refunds.create: {decision: allow}` and no `effect:`, and a tree with
`@protect("nothing.listed")`. Both kinds appear, and neither is reported as an
`unprotected_call`. Asserts §3.4 and §3.5.

### T203 — The limits sentence is in every human run and every JSON document

Including a run with zero findings, which is the run where it matters. Asserts §4.5.

### T204 — The human output and the JSON come from one producer

Every finding in the JSON has a line in the human output and the reverse, for a fixture
exercising all four kinds. Asserts §5.3.

### T205 — Scan resolves no principal, evaluates no policy and opens no store

Run against a tree and a policy with a `Control`, `StateStore` and `IdentityProvider` patched to
raise on any use. The scan completes. Asserts §9.2 — that this is not an entry point in
`v0.3 §4.3.1`'s sense, by making the entry points unusable.

### T206 — `import ctrlrun` does not import `ctrlrun.scan`

The subprocess assertion T30, T92, T125b and T134 already make, extended by one module name. It
asserts the module **exists** first: `find_spec` returning `None` would otherwise make it pass on
a tree where `ctrlrun.scan` was never written.

### T207 — A decorator that supplies the effect is not a missing effect

A policy listing `stripe.refund` with no `effect:` and a tree whose `@protect` passes one
produces no `action_without_effect`. Asserts §3.4's second paragraph.

### T208 — A call on an expression is a finding, not an undetermined call

`factory(key).delete_account(1)` is an `unprotected_call` whose detail says the receiver was an
expression; `handlers[key]()` is undetermined. Asserts §4.1's second and third paragraphs.

## 9. Public API and CLI additions (frozen)

### 9.1 The names

```text
# ctrlrun.scan — core, stdlib, NOT re-exported at package import
#   ctrlrun.scan.scan(...)            -> ScanReport      the one producer (§5.3)
#   ctrlrun.scan.ScanReport
#   ctrlrun.scan.Finding
#   ctrlrun.scan.FindingKind          unprotected_call | protected_action_not_in_policy
#                                     | action_without_effect | unparseable
#   ctrlrun.scan.UndeterminedCall     not a FindingKind -- see below
#   ctrlrun.scan.ScanError            the scan could not run at all (§6, exit 2)
#   ctrlrun.scan.Suppression
#   ctrlrun.scan.VOCABULARY
#   ctrlrun.scan.SCAN_SCHEMA          "ctrlrun.scan/v1"
#   ctrlrun.scan.report_lines(...)    -> list[str]       the human rendering
#   ctrlrun.scan.report_document(...) -> dict            the JSON rendering
```

**`undetermined` is not a `FindingKind`.** The first draft of §9.1 listed it as one and §4.1
said in the same document that these are not findings, which T198 and T204 then contradicted
each other about. It is its own list on the report and its own array in the document. Found by
writing the tests.

`ctrlrun/scan.py` sits **above** `control.py`, beside `verify/` and `cli/`, on `v0.4 §1`'s
argument: it composes nothing and the kernel does not import it. It is core and stdlib —
`ast`, `pathlib`, `tokenize` — because a tool that needed an extra installed is a tool half the
deployments never run, which is the same sentence `v0.4 §1` uses about verify.

### 9.2 It adds no entry point

`SPEC-v0.3.md §4.3.1` lists every path that evaluates, grants, delegates, resumes or resolves
identity, and **this document adds no row to it**, because scan does none of those five. It
constructs no `Action`, resolves no `Principal`, builds no `Control` and opens no store.

That is stated as a rule rather than left as a fact about the first implementation, because the
tempting version of this tool is the one that *does* build an action for each call site it finds
and asks the policy what would happen to it. That would be a fourth costume for
`--principal-from-client-info` (`v0.3 §1.3`, `v0.5 §2.3`): a principal invented by a tool from
something that is not a credential — here, from a source file. T205 makes the rule executable.

### 9.3 No new error type, event type, table, column or policy key

Nothing in §9.1 is an exception. The one new schema string is a document scan produces and
nothing consumes.

### 9.4 The CLI

```text
ctrlrun scan [--path DIR] [--policy FILE] [--exclude GLOB]... [--vocabulary FILE] [--json]
```

`--vocabulary` with no argument prints the list in force and exits `0`.

## 10. Explicitly out of scope

Each of these is excluded for a reason, not merely absent:

- **Scanning a running MCP server's tool list.** It needs the network and therefore
  `ctrlrun[gateway]`, and §9.1 puts scan in core. When it is built it is a separate command or a
  flag on the gateway, and it is a different kind of evidence: what a server advertises today,
  not what a tree contains.
- **Scanning anything but Python.** The decorator is Python. A scanner for a language with no
  decorator to find would be reporting on a vocabulary and nothing else.
- **Scanning dependencies.** A finding an operator cannot act on without forking a library is
  noise, and the honest answer for a dependency is the gateway.
- **Inserting decorators, writing policy entries, or any `--fix`.** §1.1.
- **A score, a grade, a percentage or a badge.** `251 of 284` invites a target, and a number
  that can be improved by shortening the vocabulary is a number that will be.
- **Uploading, aggregating, or a hosted service.** §1.1.
- **A watch mode, a daemon, an editor plugin, a pre-commit hook shipped by this project.** The
  command exits; anything that wants it on every commit can call it.

## 11. What is still owed

- The vocabulary in §3.2 is a first list and will be wrong in both directions. It is versioned
  with the distribution, and a change to it is a change a release note names.
- The false-positive rate is unmeasured against anything but this repository, where the first
  run reported 208 findings and 216 undetermined calls across 44 files and two corrections took
  those to 77 and 10. A library is not an agent, so even 77 is not a number to read as risk.
  Until it is measured against real application trees, §4.5's sentence is doing the work.
- A scan of a project that uses the gateway exclusively reads as a project with no protection
  (§4.4). The report says so; that is mitigation, not a fix.
