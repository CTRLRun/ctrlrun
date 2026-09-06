# Fuzzing

Two properties are fuzzed, chosen because each is a promise the project has written down.

**Canonicalization** (`SPEC-v0.1.md` §2.3, which calls it security-critical and requires a
test proving old hashes still verify before anything here changes). Two distinct actions sharing a canonical form share an approval, and
nothing in a receipt would look wrong. This repository has already had one bug of exactly that
shape — `str(key)` folded `{"1": "a", 1: "b"}` into a single key, with the survivor decided by
insertion order — and a review found it. These are the properties that would have found it
without one.

**`Policy.from_yaml`**, whose docstring says *anything malformed raises `PolicyError`*. That is
the fail-closed rule in a sentence. A loader raising `KeyError` still denies, but through a
crash nobody classified, and a caller catching `PolicyError` would not catch it.

## Layout

```
properties.py       the invariants and the decoders — no Atheris import
fuzz_canonical.py   Atheris entry point
fuzz_policy.py      Atheris entry point
corpus/             seed inputs, including a reproducer for every recorded finding
```

`properties.py` deliberately does not import Atheris. The fuzzer is one driver for these
invariants and `tests/test_fuzzing.py` is another, so they run on every commit whether or not
anybody has a fuzzing toolchain — and a contributor can reproduce a finding with nothing but
the standard library.

## Running

```sh
python fuzz/fuzz_canonical.py --corpus                                  # seeds only, no Atheris
pip install atheris
python fuzz/fuzz_canonical.py -max_total_time=120 fuzz/corpus/canonical  # a campaign
```

CI runs the first form as a gate and the second for two minutes per target on every pull
request, weekly on a schedule. A failure to install Atheris is a red build and not a skip: a
fuzzing job that quietly stops fuzzing is a false green.

## The invariants

For canonicalization, over every generated document:

1. **Refusal is stated.** Nothing but `InvalidArgument` escapes — checked on *every* call, not
   just the first. A positive control found that one: an encoder broken only on its second call
   raised `TypeError` straight out of the property.
2. **Floats and non-string keys are refused**, asserted *before* the call. Checking only what
   comes back cannot tell "refused correctly" from "never refused at all".
3. **Deterministic** — the same document twice gives the same bytes.
4. **Order-independent** — two mappings that compare equal encode identically, or the hash
   depends on how the caller happened to build the dict.
5. **Stable through a round trip** — `json.loads` then re-encode gives the same bytes. A lossy
   encoding shows up here.
6. **Keys sorted at every level**, which is what makes the form canonical and not merely
   consistent.

For the policy loader: nothing but `PolicyError` escapes, and a document that loads hashes the
same way on a second parse. (`RecursionError` on a document nested past the interpreter's limit
is out of scope, and is returned rather than swallowed silently — it is a stack-depth property,
not a policy one.)

Every invariant has a positive control in `tests/test_fuzzing.py` that breaks it deliberately
and requires the check to notice. A property that cannot fail proves nothing.

## Findings

`properties.KNOWN_FINDINGS` records defects that are real, reported, and not yet fixed. They
are recorded rather than worked around in the decoder, because a fuzzer whose corpus is pruned
to avoid its own findings reports zero forever.

`test_the_known_findings_still_reproduce` asserts each one **still happens**. The day it is
fixed that test goes red and the entry must be deleted — which is the point. A recorded limit
that quietly starts passing is the failure mode this directory is about.

### `lone-surrogate-in-a-string`

`canonical_bytes` raises `UnicodeEncodeError`, not `InvalidArgument`, for a string holding an
unpaired UTF-16 surrogate.

Reachable: `json.loads('"\ud800"')` produces one, so an MCP tool call can carry it into the
action path. `Action(...)` accepts it and `action_hash` is where it fails.

Fail-closed **holds** — both the gateway and `Control` wrap the action path in
`except Exception` — so this is a contract violation and a crash, not an authorization bypass.
What it breaks is the closed error set in `errors.py` and `InvalidArgument`'s documented
promise: a caller catching `CTRLRunError` does not catch this.

The fix is narrow and belongs to whoever owns `src/`: reject unencodable strings in
`_no_floats` alongside the float and non-string-key checks. It changes no hash that previously
succeeded, so `v0.1 §2.3`'s "old hashes still verify" rule is satisfied without a schema bump.
