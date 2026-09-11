"""The provider idempotency token. Build-list item 3 of v0.7; SPEC-v0.7 §4, §8.3 T232-T239.

**The token is derived from `(effect_key, attempt)`, never from the effect key alone** (§4.1).
A token stable across `v0.1 §5.4`'s renewal would be answered by the provider with its cached
failure, so the one retry the kernel permits because the executor proved nothing happened would
never reach the provider. T232 is the test this item exists for.

Nothing here says the token makes a retry safe. After `AMBIGUOUS` the kernel still refuses a
blind retry; the token is a deterministic handle for reconciliation to observe *with* (§4.7).

The tests that need a server skip without `CTRLRUN_TEST_POSTGRES`. The derivation, the accessor's
refusals, the renewal on the two shipped backends, the resumed leg and G14 all run without one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from ctrlrun import (
    Action,
    Control,
    InMemoryStateStore,
    InvalidArgument,
    NotExecuted,
    Policy,
    Principal,
    SQLiteStateStore,
    Suspended,
    context,
    idempotency_token,
    protect,
)
from ctrlrun.effect import EffectState, idempotency_token_for
from ctrlrun.receipt import ReceiptResult
from ctrlrun.verify import Status
from ctrlrun.verify import guarantees as reg
from ctrlrun.verify import run as run_verify

URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)

#: §4.2's worked example, pinned so a change to the derivation is a red test (T235).
PINNED_KEY = "refund:txn_1"
PINNED_TOKEN = "382ee448-97da-8107-b674-8c253650d93f"
PINNED_ATTEMPT_2 = "28bb40af-814c-8fb6-ba63-e8663b1c036d"
PINNED_OTHER_KEY = "89977bc9-d128-8ae8-871f-f5265155e60f"

ALLOW = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: allow
"""

OBSERVE = """
schema: ctrlrun.policy/v3
mode: observe
actions:
  stripe.refund:
    decision: allow
"""

CONTINUATION = "opaque-state-from-the-server"


def an_action(payment_id: str = "txn_1") -> Action:
    return Action(
        "stripe.refund", {"payment_id": payment_id, "amount": 200}, Principal("refund-agent")
    )


@pytest.fixture
def sqlite_store(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    yield store
    store.close()


@pytest.fixture(params=["in-memory", "sqlite"])
def store(request, tmp_path):
    opened = (
        InMemoryStateStore()
        if request.param == "in-memory"
        else SQLiteStateStore(tmp_path / "state.db")
    )
    yield opened
    opened.close()


@pytest.fixture
def pg_schema():
    """A schema of this test's own, dropped afterwards."""
    from ctrlrun.postgres import PostgresStateStore

    assert URL
    name = f"token_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(URL, name)
    opened: list = []
    yield name, opened
    for store in opened:
        store.close()
    PostgresStateStore.drop_schema(URL, name)


def _pg_store(pg_schema):
    from ctrlrun.postgres import PostgresStateStore

    name, opened = pg_schema
    store = PostgresStateStore(URL, schema=name)
    opened.append(store)
    return store


def _control(store, policy: str = ALLOW) -> Control:
    return Control(Policy.from_yaml(policy), store)


def _receipts_for(store, key: str) -> list:
    return [receipt for receipt in store.receipts() if receipt.effect_key == key]


# --- T232: a FAILED renewal changes the token -------------------------------------------------


def _renewal(control, key: str = PINNED_KEY) -> list[str]:
    """Attempt 1 reads the token and reports nothing happened; attempt 2 reads it and commits."""
    read: list[str] = []

    def failing():
        read.append(idempotency_token())
        raise NotExecuted("the remote did nothing")

    def committing():
        read.append(idempotency_token())
        return "ok"

    with pytest.raises(NotExecuted):
        control.execute(an_action(), failing, key)
    control.execute(an_action(), committing, key)
    return read


def test_T232_a_failed_renewal_changes_the_token(store):
    """**The test item 3 exists for.** SPEC-v0.7 §4.1, §8.3.

    A token stable here is a token the provider answers with the cached failure of the attempt
    that failed, for the one retry `v0.1 §5.4` permits *because the executor proved nothing
    happened*.
    """
    control = _control(store)

    read = _renewal(control)

    assert len(read) == 2, "both attempts ran and both read a token"
    assert read[0] != read[1], "the renewal is a new attempt and must carry a new token"
    assert store.get_effect(PINNED_KEY).state is EffectState.COMMITTED
    receipts = _receipts_for(store, PINNED_KEY)
    assert [receipt.attempt for receipt in receipts] == [1, 2]
    assert [idempotency_token_for(r.effect_key, r.attempt) for r in receipts] == read


@postgres
def test_T232_a_failed_renewal_changes_the_token_on_postgres(pg_schema):
    store = _pg_store(pg_schema)
    control = _control(store)

    read = _renewal(control)

    assert read[0] != read[1]
    receipts = _receipts_for(store, PINNED_KEY)
    assert [receipt.attempt for receipt in receipts] == [1, 2]
    assert [idempotency_token_for(r.effect_key, r.attempt) for r in receipts] == read


def test_T232_the_two_attempts_are_the_derivation_of_their_own_numbers(store):
    """The mutant this catches: a kernel returning a fresh random string on every read."""
    control = _control(store)

    read = _renewal(control)

    assert read == [
        idempotency_token_for(PINNED_KEY, 1),
        idempotency_token_for(PINNED_KEY, 2),
    ]
    assert read == [PINNED_TOKEN, PINNED_ATTEMPT_2]


# --- T233: stable within one attempt, across Control.resume -----------------------------------


def test_T233_two_reads_in_one_executor_are_equal(store):
    control = _control(store)
    read: list[str] = []

    control.execute(
        an_action(), lambda: read.extend([idempotency_token(), idempotency_token()]), PINNED_KEY
    )

    assert read[0] == read[1]


def test_T233_a_resumed_leg_reads_the_same_token(store):
    """`Control.resume` does not change `attempt` (§4.4), so it does not change the token.

    If resume ever changes the attempt, this is the test that says so.
    """
    control = _control(store)
    read: list[str] = []

    def suspending():
        read.append(idempotency_token())
        raise Suspended(CONTINUATION)

    with pytest.raises(Suspended):
        control.execute(an_action(), suspending, PINNED_KEY)
    before = store.get_effect(PINNED_KEY).attempt

    def resumed():
        read.append(idempotency_token())
        return "ok"

    receipt = control.resume(CONTINUATION, resumed)

    assert read[0] == read[1], "a resumed leg is the same attempt with the same token"
    assert store.get_effect(PINNED_KEY).attempt == before == receipt.attempt == 1
    assert receipt.result is ReceiptResult.COMMITTED


# --- T234: two keys at one attempt differ -----------------------------------------------------


def test_T234_two_keys_at_one_attempt_differ():
    assert idempotency_token_for("refund:txn_1", 1) != idempotency_token_for("refund:txn_2", 1)


def test_T234_two_keys_at_one_attempt_differ_through_the_accessor(store):
    control = _control(store)
    read: list[str] = []

    for key in ("refund:txn_1", "refund:txn_2"):
        control.execute(an_action(), lambda: read.append(idempotency_token()), key)

    assert read[0] != read[1]
    assert read == [PINNED_TOKEN, PINNED_OTHER_KEY]


# --- T235: a pure function, pinned ------------------------------------------------------------


def test_T235_the_derivation_is_pinned_as_a_literal():
    """§4.2's worked example. A change to the derivation is a red test, not a silent change."""
    assert idempotency_token_for("refund:txn_1", 1) == "382ee448-97da-8107-b674-8c253650d93f"
    assert idempotency_token_for("refund:txn_1", 2) == "28bb40af-814c-8fb6-ba63-e8663b1c036d"
    assert idempotency_token_for("refund:txn_2", 1) == "89977bc9-d128-8ae8-871f-f5265155e60f"


def test_T235_the_token_is_a_uuid_of_version_8_and_the_rfc_9562_variant():
    parsed = uuid.UUID(PINNED_TOKEN)

    assert parsed.version == 8, "RFC 9562: a name-based UUID from SHA-256 is in the v8 space"
    assert (parsed.int >> 62) & 0b11 == 0b10, "variant 10"
    assert len(PINNED_TOKEN) == 36
    assert PINNED_TOKEN.isascii() and PINNED_TOKEN.lower() == PINNED_TOKEN


def test_T235_another_process_derives_the_same_string():
    """A pure function of two values: another process, with no store and no `Control`, agrees."""
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ctrlrun.effect import idempotency_token_for as t;"
            "print(t('refund:txn_1', 1), t('refund:txn_1', 2))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.split() == [PINNED_TOKEN, PINNED_ATTEMPT_2]


def test_T235_the_same_pair_against_sqlite_gives_the_pinned_string(sqlite_store):
    control = _control(sqlite_store)
    read: list[str] = []

    control.execute(an_action(), lambda: read.append(idempotency_token()), PINNED_KEY)

    assert read == [PINNED_TOKEN]


@postgres
def test_T235_the_same_pair_against_postgres_gives_the_pinned_string(pg_schema):
    control = _control(_pg_store(pg_schema))
    read: list[str] = []

    control.execute(an_action(), lambda: read.append(idempotency_token()), PINNED_KEY)

    assert read == [PINNED_TOKEN]


@pytest.mark.parametrize(
    ("key", "attempt", "names"),
    [
        (PINNED_KEY, 1.0, "attempt=1.0"),
        (PINNED_KEY, True, "attempt=True"),
        (PINNED_KEY, 0, "attempt=0"),
        (PINNED_KEY, -1, "attempt=-1"),
        ("", 1, "effect_key=''"),
        (None, 1, "effect_key=None"),
    ],
)
def test_T235_the_derivation_refuses_what_it_cannot_name(key, attempt, names):
    """**The `bool` row is load-bearing**: `canonical_bytes` accepts `True`, since `bool` is a
    subclass of `int` and JSON has `true`, so without the function's own check
    `idempotency_token_for(key, True)` would return a token nobody's attempt has (§4.2).

    The message is asserted and not only the type. Three guards refuse here, and a test that read
    the type alone could not tell you which one ran, or notice a guard that had stopped running
    because an earlier one happened to cover its case.
    """
    with pytest.raises(InvalidArgument) as raised:
        idempotency_token_for(key, attempt)

    assert names in str(raised.value)


def test_T235_the_bool_refusal_is_the_functions_own():
    """Without this the `bool` row above is a negative test against behaviour the library
    refuses anyway. `canonical_bytes` **accepts** a `bool`, so the refusal has to be the
    function's own check and the row is load-bearing (§4.2)."""
    from ctrlrun.action import canonical_bytes

    assert canonical_bytes({"attempt": True}) == b'{"attempt":true}'


# --- T236: outside an executor it fails closed ------------------------------------------------


def test_T236_at_the_top_level_it_fails_closed():
    with pytest.raises(InvalidArgument) as raised:
        idempotency_token()

    assert not isinstance(raised.value, NotExecuted), (
        "NotExecuted would be turned into a FAILED record and a permitted retry (§4.3)"
    )


def test_T236_inside_evaluate_it_fails_closed(store):
    """`evaluate` runs no executor and holds no attempt, so there is no token to name."""
    control = _control(store)
    control.evaluate(an_action())

    with pytest.raises(InvalidArgument):
        idempotency_token()

    assert list(store.receipts()) == []
    assert store.get_effect(PINNED_KEY) is None


def test_T236_an_action_with_no_effect_key_fails_closed(store):
    control = _control(store)
    raised: list[BaseException] = []

    def executor():
        try:
            idempotency_token()
        except BaseException as exc:
            raised.append(exc)
        return "ok"

    receipt = control.execute(an_action(), executor)

    assert len(raised) == 1
    assert isinstance(raised[0], InvalidArgument)
    assert not isinstance(raised[0], NotExecuted)
    assert receipt.result is ReceiptResult.COMMITTED


def test_T236_an_observe_mode_attempt_that_holds_nothing_fails_closed(store):
    """§4.3. Handing it attempt 1's token would name the real holder's attempt."""
    store.reserve_effect(PINNED_KEY, "someone-else", timedelta(minutes=5))
    store.begin_execution(PINNED_KEY, "someone-else")
    before = store.get_effect(PINNED_KEY)
    control = _control(store, OBSERVE)
    raised: list[BaseException] = []

    def executor():
        try:
            idempotency_token()
        except BaseException as exc:
            raised.append(exc)
        return "ok"

    control.execute(an_action(), executor, PINNED_KEY)

    assert len(raised) == 1, "the observe-mode executor ran and asked"
    assert isinstance(raised[0], InvalidArgument)
    assert not isinstance(raised[0], NotExecuted)
    assert store.get_effect(PINNED_KEY) == before, "the real holder's record is untouched"


def test_T236_an_observe_mode_attempt_that_holds_its_key_is_answered(store):
    """The control for the test above: observe mode reserves, and a held attempt has a token."""
    control = _control(store, OBSERVE)
    read: list[str] = []

    control.execute(an_action(), lambda: read.append(idempotency_token()), PINNED_KEY)

    assert read == [PINNED_TOKEN]


def test_T236_a_thread_without_the_context_fails_closed(store):
    """A context variable does not cross a thread unless the caller copies it, and a missing
    value is refused rather than guessed (§4.3)."""
    control = _control(store)
    raised: list[BaseException] = []
    read: list[str] = []

    def executor():
        read.append(idempotency_token())

        def elsewhere():
            try:
                idempotency_token()
            except BaseException as exc:
                raised.append(exc)

        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), "the thread finished rather than hanging the suite"
        return "ok"

    control.execute(an_action(), executor, PINNED_KEY)

    assert read == [PINNED_TOKEN], "the executor's own read still answers"
    assert len(raised) == 1
    assert isinstance(raised[0], InvalidArgument)
    assert not isinstance(raised[0], NotExecuted)
    assert store.get_effect(PINNED_KEY).state is EffectState.COMMITTED


def test_T236_after_an_executor_returns_the_token_is_gone(store):
    """The leak this closes: a context variable set and never reset hands the next caller the
    last attempt's token. G14's control catches the same thing (§8.9)."""
    control = _control(store)

    control.execute(an_action(), lambda: idempotency_token(), PINNED_KEY)

    with pytest.raises(InvalidArgument):
        idempotency_token()


def test_T236_after_an_executor_raised_the_token_is_gone(store):
    control = _control(store)

    with pytest.raises(NotExecuted):
        control.execute(
            an_action(),
            lambda: (_ for _ in ()).throw(NotExecuted("nothing ran")),
            PINNED_KEY,
        )

    with pytest.raises(InvalidArgument):
        idempotency_token()


# --- T237: the zero-argument executor is unchanged --------------------------------------------


def test_T237_a_0_6_1_executor_runs_untouched_through_protect(store):
    control = _control(store)
    calls: list[tuple] = []

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append((payment_id, amount))
        return "re_1"

    with context(agent="refund-agent"):
        assert issue_refund(payment_id="txn_1", amount=200) == "re_1"

    assert calls == [("txn_1", 200)]
    assert store.get_effect(PINNED_KEY).state is EffectState.COMMITTED


def test_T237_a_0_6_1_executor_runs_untouched_through_execute(store):
    control = _control(store)

    receipt = control.execute(an_action(), lambda: "re_1", PINNED_KEY)

    assert receipt.result is ReceiptResult.COMMITTED
    assert receipt.attempt == 1


def test_T237_a_0_6_1_executor_runs_untouched_through_an_adapter(store):
    """The adapter surface of `v0.5`: an interrupt provider answering a `wait=True` tool."""
    from ctrlrun.adapter import ApprovalAnswer, InterruptApprovalProvider, PendingApproval

    class Interrupt:
        framework = "double"
        carries_approved_arguments = True

        def __init__(self) -> None:
            self.calls: list[PendingApproval] = []

        def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
            self.calls.append(pending)
            return ApprovalAnswer(
                granted=True,
                approver="double:interrupt",
                approved_arguments=dict(pending.arguments),
            )

    interrupt = Interrupt()
    control = Control(
        Policy.from_yaml(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: approve\n"
        ),
        store,
        InterruptApprovalProvider(store, interrupt),
    )
    calls: list[tuple] = []

    @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append((payment_id, amount))
        return "re_1"

    with context(agent="refund-agent"):
        assert issue_refund(payment_id="txn_1", amount=200) == "re_1"

    assert calls == [("txn_1", 200)]
    assert len(interrupt.calls) == 1


def test_T237_a_0_6_1_executor_runs_untouched_through_the_gateway(sqlite_store):
    """The gateway builds its own zero-argument executor; nothing about it changes (§4.3)."""
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.outcome import UpstreamResult
    from ctrlrun.gateway.server import Gateway, GatewayConfig

    policy = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    decision: allow
"""
    reply = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"resultType": "complete", "content": []}}
    ).encode()

    def forwarder(body, headers, *, fresh):
        return (
            UpstreamResult(result_type="complete"),
            reply,
            200,
            {"Content-Type": "application/json"},
        )

    gateway = Gateway(
        GatewayConfig(
            upstream="http://127.0.0.1:1/mcp", alias="acme", principal_header="X-Agent", port=0
        ),
        Control(Policy.from_yaml(policy), sqlite_store),
        forwarder,
    )
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"payment_id": "txn_1", "amount": 200}},
    }

    response = gateway.handle(
        json.dumps(call).encode(),
        {
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "create_refund",
            "X-Agent": "refund-agent",
        },
    )

    assert response.status == 200, response
    assert sqlite_store.get_effect(PINNED_KEY).state is EffectState.COMMITTED
    assert [receipt.result for receipt in sqlite_store.receipts()] == [ReceiptResult.COMMITTED]


# --- T238: a receipt re-derives its token -----------------------------------------------------


def test_T238_every_receipt_of_an_attempt_that_ran_re_derives_its_token(store):
    """§4.5. The token is not a receipt field because it is derivable from two that are."""
    control = _control(store)
    renewal = _renewal(control)

    resumed: list[str] = []

    def suspending():
        resumed.append(idempotency_token())
        raise Suspended(CONTINUATION)

    with pytest.raises(Suspended):
        control.execute(an_action("txn_9"), suspending, "refund:txn_9")
    control.resume(CONTINUATION, lambda: resumed.append(idempotency_token()) or "ok")

    read = {PINNED_KEY: renewal, "refund:txn_9": resumed}
    ran = [
        receipt
        for receipt in store.receipts()
        if receipt.result in (ReceiptResult.COMMITTED, ReceiptResult.FAILED)
    ]
    assert len(ran) == 3
    for receipt in ran:
        derived = idempotency_token_for(receipt.effect_key, receipt.attempt)
        assert derived in read[receipt.effect_key], (
            f"{receipt.effect_key} attempt {receipt.attempt} re-derives a token no executor read"
        )
    assert set(resumed) == {idempotency_token_for("refund:txn_9", 1)}


# --- T239: G14 in verify ----------------------------------------------------------------------

WITH_EFFECTS = """
schema: ctrlrun.policy/v3
mode: enforce
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - decision: deny
  acme.read:
    decision: allow
"""

NO_EFFECTS = """
schema: ctrlrun.policy/v3
mode: enforce
actions:
  acme.read:
    decision: allow
"""


def _write(directory: Path, document: str) -> Path:
    path = directory / "ctrlrun.yaml"
    path.write_text(document, encoding="utf-8")
    return path


def _by_id(report):
    return {result.id: result for result in report.guarantees}


def _g14(tmp_path, document: str = WITH_EFFECTS):
    report = run_verify(_write(tmp_path, document), only=("G14",))
    return next(result for result in report.guarantees if result.id == "G14")


def test_T239_G14_is_in_the_catalogue():
    assert reg.CATALOGUE == "ctrlrun.guarantees/v3"
    assert "G14" in reg.BY_ID
    assert reg.BY_ID["G14"].descends_from, "a guarantee names the tests it is the deployed form of"


def test_T239_G14_passes_against_a_document_with_an_effect_template(tmp_path):
    result = _g14(tmp_path)

    assert result.status is Status.PASS, (result.reason, result.counterexample)


def test_T239_G14_is_not_applicable_where_no_action_declares_an_effect(tmp_path):
    result = _g14(tmp_path, NO_EFFECTS)

    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == reg.NO_EFFECT_TEMPLATE
    assert result.status is not Status.PASS


def _leak_the_context(monkeypatch):
    """A kernel that sets the token and never resets it (§8.9).

    It passes the observable: each executor still reads its own attempt's value. What it fails
    is the control, where a caller outside any attempt is handed the last attempt's token.
    """
    from contextlib import contextmanager

    from ctrlrun import control as control_module

    @contextmanager
    def leaking(held_key, attempt):
        if held_key is not None:
            control_module._IDEMPOTENCY_TOKEN.set(idempotency_token_for(held_key, attempt))
        yield

    monkeypatch.setattr(control_module, "_attempt_token", leaking)


def _move_within_the_attempt(monkeypatch):
    """An accessor whose answer moves within one attempt: the two reads differ.

    Patched where the scenario reads it, as `_break_the_executor` patches what G1 to G5 run,
    because the control's claim is about what the scenario observes.
    """
    from ctrlrun.verify import scenarios

    counter = [0]

    def moving() -> str:
        counter[0] += 1
        return f"ctrlrun-verify-moving-{counter[0]}"

    monkeypatch.setattr(scenarios, "idempotency_token", moving)


@pytest.mark.parametrize(
    "break_it", [_leak_the_context, _move_within_the_attempt], ids=["leak", "moves"]
)
def test_T239_a_broken_positive_control_is_a_failure(tmp_path, monkeypatch, break_it):
    """§1.3's guard, for G14. A control that does not behave as specified is FAIL with
    `reason: "control failed"`, never PASS and never N/A."""
    from ctrlrun.control import _IDEMPOTENCY_TOKEN

    break_it(monkeypatch)
    try:
        result = _g14(tmp_path)
    finally:
        # The leak is the point of the mutant, so this test undoes it rather than leaving the
        # next test in this process holding an attempt's token.
        _IDEMPOTENCY_TOKEN.set(None)

    if result.status is not Status.FAIL:
        raise AssertionError(f"G14 reported {result.status} with a broken control")
    assert result.reason == reg.CONTROL_FAILED
    assert result.counterexample is not None


def _flat(text: str) -> str:
    """The report wraps its notes, so a phrase is matched against one line of words."""
    return " ".join(text.split())


def test_T239_the_effect_key_scope_note_is_printed_once(tmp_path):
    """§4.6. The kernel sees one store and cannot check that two stores sharing a provider
    account never produce one effect-key string for two different effects. Verify says so."""
    report = run_verify(_write(tmp_path, WITH_EFFECTS))

    flat = _flat(report.to_text())

    assert flat.count("a token is unique only as far as your effect keys are") == 1
    assert "nothing here can check that" in flat


def test_T239_the_note_is_absent_where_G14_is_not_graded(tmp_path):
    report = run_verify(_write(tmp_path, NO_EFFECTS))

    assert "a token is unique only as far as" not in _flat(report.to_text())


def test_T239_the_note_is_absent_where_G14_was_not_selected(tmp_path):
    """`--only G1` grades no token, so the note is about nothing that ran (§4.6)."""
    report = run_verify(_write(tmp_path, WITH_EFFECTS), only=("G1",))

    assert _by_id(report)["G14"].status is Status.SKIPPED
    assert "a token is unique only as far as" not in _flat(report.to_text())


# --- the surface (§9.1, §9.2) -----------------------------------------------------------------


def test_the_accessor_is_re_exported_at_package_import():
    import ctrlrun

    assert ctrlrun.idempotency_token is idempotency_token
    assert "idempotency_token" in ctrlrun.__all__


def test_the_token_is_not_a_receipt_field(store):
    """§4.5. Storing it would be a second copy of a fact that could disagree with the first."""
    control = _control(store)
    control.execute(an_action(), lambda: "ok", PINNED_KEY)

    (receipt,) = store.receipts()

    assert "idempotency" not in json.dumps(receipt.to_dict())
    assert idempotency_token_for(receipt.effect_key, receipt.attempt) == PINNED_TOKEN


def test_the_domain_tag_is_versioned():
    """§4.2. A later derivation cannot collide with this one, and a token can never equal a
    hash computed over the same pair for another purpose."""
    import hashlib

    from ctrlrun.action import canonical_bytes

    undomained = hashlib.sha256(
        canonical_bytes({"effect_key": PINNED_KEY, "attempt": 1})
    ).hexdigest()

    assert PINNED_TOKEN.replace("-", "") != undomained[:32]


def test_nothing_but_the_pair_reaches_the_derivation(store):
    """§4.6. The token is a function of `(effect_key, attempt)` and nothing else, which is why
    two stores that share a provider account derive identical tokens wherever their effect-key
    strings coincide, and why the effect key must name its effect uniquely across them."""
    first = _control(store)
    read: list[str] = []
    first.execute(an_action(), lambda: read.append(idempotency_token()), PINNED_KEY)

    second = Control(Policy.from_yaml(ALLOW), InMemoryStateStore())
    second.execute(
        Action("stripe.refund", {"payment_id": "txn_1", "amount": 999}, Principal("other-agent")),
        lambda: read.append(idempotency_token()),
        PINNED_KEY,
    )

    assert read[0] == read[1] == PINNED_TOKEN
