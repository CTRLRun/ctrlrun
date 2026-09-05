"""click command group for the ctrlrun CLI. Build-list item 8; SPEC-v0.1 §8.

The CLI is the human end of the kernel: it answers approval requests, shows the evidence,
and resolves the one state no machine may resolve for itself (§5.2). It is also the only
place in CTRLRun that prints.

Every command works on the store an agent is already using — `.ctrlrun/state.db` beside the
policy, or wherever `$CTRLRUN_STATE` says (§8) — so approving here answers the request an
agent is waiting on in another shell.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import click

from ..action import Principal
from ..approval import ApprovalRecord
from ..authority import Delegation, grant_from_yaml
from ..control import DEFAULT_STATE_DIR, Control, state_path
from ..effect import RESOLVED_BY_HUMAN, EffectRecord, EffectState
from ..errors import AuthorityEscalation, CTRLRunError, PolicyError
from ..policy import DEFAULT_POLICY_FILENAME, OBSERVE, Decision, Policy
from ..receipt import (
    BLOCKED_APPROVAL_REQUIRED,
    BLOCKED_BY_STATE,
    Event,
    EventType,
    Receipt,
    ReceiptResult,
    iso_timestamp,
)
from ..state import RESOLUTIONS, SQLiteStateStore, StateStore
from .demo import run_demo

#: Who the CLI records as the answer's author. Free text in v0.1 (SPEC-v0.1 §4.1).
CLI_APPROVER: Final = "cli:local"

#: The two states that hold a lease, and so the two whose lease can lapse (v0.1 §5.3 E3).
_LEASED: Final = frozenset({EffectState.RESERVED, EffectState.EXECUTING})

#: SPEC-v0.3 §6.5 — printed to stderr, before anything else, by every command that loads the
#: operator's policy, on every invocation. To stderr so a `--json` stdout stays
#: machine-readable and a pipeline cannot silently swallow it; on every invocation because a
#: deployment that has been observing for six months is exactly the one this line is for.
OBSERVE_BANNER: Final = "OBSERVE MODE — nothing is enforced"

#: SPEC-v0.3 §6.4 — one `ctrlrun stats --json` document.
STATS_SCHEMA: Final = "ctrlrun.stats/v1"

#: SPEC-v0.2 §5 — the schema of one `ctrlrun inspect --json` document.
#: SPEC-v0.3 §12.2 — v2 where the header block gained the principal's issuer, expiry and
#: claim names. The values reach `--json`; the human block shows only the names, because a
#: claim can hold an employee number or a case id and this output is read over shoulders.
INSPECTION_SCHEMA: Final = "ctrlrun.inspection/v2"

#: `ctrlrun init` writes this. It is `ctrlrun.example.yaml` in the repository, and
#: `test_the_shipped_example_policy_is_the_one_in_the_repository` keeps the two identical.
EXAMPLE_POLICY: Final = """# ctrlrun.yaml — action-level autonomy policy (v0.1)
# Unknown actions are DENIED. There is no default-allow. List what is safe.
schema: ctrlrun.policy/v1

actions:
  # Reads: autonomous. Declare no effect key on these in code.
  customer.read:
    decision: allow
  invoice.read:
    decision: allow

  # External communication: autonomous in v0.1 (allow_with_log arrives later).
  email.send:
    decision: allow

  # Money: autonomy depends on the amount. First matching rule wins.
  # Amounts are integer minor units (cents). Floats are rejected.
  # Bound both ends: `amount_lte` alone lets a negative amount through, and a refund of
  # a negative amount is a charge. An upper bound is not a range.
  stripe.refund:
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }      # €0.00 to €500.00
        decision: allow
      - when: { amount_gte: 0, amount_lte: 500000 }     # €0.00 to €5,000.00
        decision: approve
      - decision: deny

  # Privilege changes: never autonomous.
  iam.grant_admin:
    decision: deny

  # Destructive infrastructure: human every time.
  k8s.delete_namespace:
    decision: approve
"""


#: `--store-url`, on every command that reads or resolves the operator's own store (§9.4).
STORE_URL_OPTION = click.option(
    "--store-url",
    "store_url",
    default=None,
    envvar="CTRLRUN_STORE_URL",
    help="The store to open. Default: the SQLite database under CTRLRUN_HOME.",
)

#: CTRLRun's own parameter on a Postgres URL, peeled off before the URL reaches the driver. The
#: same spelling `ctrlrun.conformance.store` uses, because an operator who has seen one should not
#: have to learn the other.
SCHEMA_PARAM: Final = "ctrlrun_schema"

SQLITE_SCHEME: Final = "sqlite://"
POSTGRES_SCHEMES: Final = ("postgres://", "postgresql://")


def _store(store_url: str | None = None) -> StateStore:
    """Open the store the operator names, or the one this working tree's agents use.

    Until v0.6 this returned `SQLiteStateStore(state_path())` unconditionally, which meant that
    on the backend this milestone exists for, `ctrlrun resolve` could not reach a record --
    §5.3's human authority had no route -- `ctrlrun effects` could not show a lapsed lease, and
    `resolved_by` had no read surface outside a Python session. A review found it.

    **It creates nothing.** `PostgresStateStore` creates no schema and this does not either: a
    command an operator runs to *read* evidence must not have a side effect on the database it
    reads, which is `v0.4 §3.5`'s argument for verify's scratch store pointed the other way. A
    schema that is not there is an error, not an invitation.
    """
    if store_url is None:
        return SQLiteStateStore(state_path())
    if store_url.startswith(SQLITE_SCHEME):
        return SQLiteStateStore(Path(store_url[len(SQLITE_SCHEME) :]))
    if store_url.startswith(POSTGRES_SCHEMES):
        from ..postgres import PostgresStateStore

        bare, schema = _peel_schema(store_url)
        return PostgresStateStore(bare, schema=schema)
    raise click.ClickException(
        f"no store backend for {store_url!r}; expected a 'sqlite://' path or a 'postgresql://' URL"
    )


def _peel_schema(url: str) -> tuple[str, str]:
    """Take CTRLRun's own schema parameter off a Postgres URL before the driver sees it."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query)
    schema = dict(pairs).get(SCHEMA_PARAM, "public")
    rest = [(key, value) for key, value in pairs if key != SCHEMA_PARAM]
    return urlunsplit(parts._replace(query=urlencode(rest))), schema


def _loaded_policy() -> Policy:
    """Load the operator's policy, printing the observe banner first (SPEC-v0.3 §6.5).

    Only the commands that already need the policy call this — `gateway`, `stats`,
    `delegate`, `revoke` and `verify`. `receipts`, `inspect` and the rest deliberately do
    not: `state_path()` finds the store without loading a policy, and making an evidence
    command load one would turn a malformed policy into a failure of the evidence.

    Where the policy cannot be loaded the `PolicyError` propagates and no banner is printed,
    because there is no mode to report.
    """
    policy = Policy.from_file()
    if policy.mode == OBSERVE:
        click.echo(OBSERVE_BANNER, err=True)
    return policy


def _fail(exc: CTRLRunError) -> click.ClickException:
    """Turn a kernel refusal into a non-zero exit with the reason, not a traceback."""
    return click.ClickException(str(exc))


def _event(
    type_: EventType,
    action_id: str,
    *,
    effect_key: str | None = None,
    approval_id: str | None = None,
    **data: object,
) -> Event:
    """One event for something a human did at the terminal (SPEC-v0.1 §6.2)."""
    return Event(
        type=type_,
        action_id=action_id,
        ts=datetime.now(UTC),
        data=data,
        effect_key=effect_key,
        approval_id=approval_id,
    )


@click.group()
@click.version_option(package_name="ctrlrun")
def main() -> None:
    """CTRLRun — transaction safety for AI-agent actions."""


@main.command()
def init() -> None:
    """Write a starter ctrlrun.yaml and create .ctrlrun/."""
    policy = Path.cwd() / DEFAULT_POLICY_FILENAME
    if policy.exists():
        # A policy is the thing that decides what an agent may do; overwriting one is not a
        # convenience. Refuse, and let the human choose.
        raise click.ClickException(f"{policy} already exists; delete it first to start over")
    policy.write_text(EXAMPLE_POLICY, encoding="utf-8")
    state = Path.cwd() / DEFAULT_STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    click.echo(f"wrote {policy}")
    click.echo(f"created {state}/")
    click.echo("edit the policy, then protect an action with @ctrlrun.protect(...)")


@main.command()
def demo() -> None:
    """Run the five scenarios, in process, with no network."""
    run_demo(Path.cwd())


@main.command()
@click.argument("request_id")
@STORE_URL_OPTION
def approve(request_id: str, store_url: str | None) -> None:
    """Grant a pending approval request."""
    store = _store(store_url)
    try:
        record = store.get_approval(request_id)
        approval = store.grant_approval(request_id, CLI_APPROVER)
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    if record is not None:
        store.append_event(
            _event(
                EventType.APPROVAL_GRANTED,
                record.request.action.action_id,
                approval_id=request_id,
                approver=approval.approver,
                action_hash=approval.action_hash,
            )
        )
    click.echo(f"granted {request_id} for {approval.action_hash}")
    click.echo(f"expires {iso_timestamp(approval.expires_at)}")


@main.command()
@click.argument("request_id")
@STORE_URL_OPTION
def deny(request_id: str, store_url: str | None) -> None:
    """Refuse a pending approval request."""
    store = _store(store_url)
    try:
        record = store.get_approval(request_id)
        store.deny_approval(request_id, CLI_APPROVER)
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    if record is not None:
        store.append_event(
            _event(
                EventType.APPROVAL_DENIED,
                record.request.action.action_id,
                approval_id=request_id,
                approver=CLI_APPROVER,
            )
        )
    click.echo(f"denied {request_id}")


@main.command()
@click.option("--last", type=click.IntRange(min=1), default=None, help="Show only the last N.")
@click.option("--json", "as_json", is_flag=True, help="Print the portable receipt JSON.")
@STORE_URL_OPTION
def receipts(last: int | None, as_json: bool, store_url: str | None) -> None:
    """Show the receipts this store holds."""
    try:
        found = _store(store_url).receipts()
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    if last is not None:
        found = found[-last:]
    if not found:
        click.echo("no receipts yet")
        return
    for receipt in found:
        click.echo(receipt.to_json() if as_json else _receipt_line(receipt))


@main.command()
@click.option(
    "--state",
    type=click.Choice([str(member) for member in EffectState]),
    default=None,
    help="Show only effects in this state.",
)
@STORE_URL_OPTION
def effects(state: str | None, store_url: str | None) -> None:
    """Show the logical effects this store knows about."""
    try:
        found = _store(store_url).list_effects(None if state is None else EffectState(state))
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    if not found:
        click.echo("no effects yet" if state is None else f"no effects are {state}")
        return
    for record in found:
        click.echo(_effect_line(record))


@main.command()
@click.argument("effect_key")
@click.option("--committed", is_flag=True, help="The effect did happen at the remote.")
@click.option("--failed", is_flag=True, help="The effect provably did not happen.")
@STORE_URL_OPTION
def resolve(effect_key: str, committed: bool, failed: bool, store_url: str | None) -> None:
    """Say what actually happened to an effect with an unknown outcome."""
    if committed == failed:
        # SPEC §5.2 — a resolution is a human's claim about the real world, and the two
        # claims are opposites. Neither flag says nothing; both say nothing twice.
        raise click.UsageError(
            f"say which it was: exactly one of --committed or --failed "
            f"({'|'.join(sorted(RESOLUTIONS))} are the only answers)"
        )
    outcome = EffectState.COMMITTED if committed else EffectState.FAILED
    store = _store(store_url)
    try:
        record = store.resolve_effect(effect_key, outcome, CLI_APPROVER)
        store.append_event(
            _event(
                EventType.EFFECT_RESOLVED,
                record.action_id,
                effect_key=effect_key,
                state=str(record.state),
                resolver=CLI_APPROVER,
                resolved_by=RESOLVED_BY_HUMAN,
            )
        )
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    click.echo(f"{effect_key} resolved {record.state} by {CLI_APPROVER}")
    if record.state is EffectState.FAILED:
        click.echo("a retry of this effect is now permitted")


@main.command()
@click.argument("action_id")
@click.option("--json", "as_json", is_flag=True, help="Emit one JSON object instead.")
@STORE_URL_OPTION
def inspect(action_id: str, as_json: bool, store_url: str | None) -> None:
    """Show one action's whole history: proposal, decision, approval, effect, receipt."""
    store = _store(store_url)
    try:
        events = tuple(event for event in store.events() if event.action_id == action_id)
        receipt = next((found for found in store.receipts() if found.action_id == action_id), None)
    except CTRLRunError as exc:
        raise _fail(exc) from exc

    if not events and receipt is None:
        # SPEC-v0.2 §5 — non-zero, on stderr, with nothing on stdout, so a script cannot
        # mistake "no such action" for "an action with no events". Matched exactly: no
        # prefixes and no globs, as v0.1 §3.1 matches an action name.
        raise click.ClickException(f"no action {action_id}")

    approvals = _approvals_for(store, receipt, events)
    effect = _effect_of(store, receipt, events)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "schema": INSPECTION_SCHEMA,
                    "action_id": action_id,
                    "receipt": None if receipt is None else receipt.to_dict(),
                    "effect": None if effect is None else _effect_dict(effect),
                    "approvals": [_approval_dict(record) for record in approvals],
                    "events": [event.to_dict() for event in events],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for line in _inspection_lines(action_id, receipt, effect, approvals, events):
        click.echo(line)


def _approvals_for(
    store: StateStore, receipt: Receipt | None, events: tuple[Event, ...]
) -> tuple[ApprovalRecord, ...]:
    """Every approval carrying this action's hash (SPEC-v0.2 §5).

    The hash comes from the receipt, or from `ACTION_PROPOSED` for an action still awaiting
    a human — which has no receipt yet (v0.1 §6.1) and is exactly the case where the pending
    request is the only thing there is to show.
    """
    action_hash = receipt.action_hash if receipt is not None else None
    if action_hash is None:
        action_hash = next(
            (
                str(event.data["action_hash"])
                for event in events
                if event.type is EventType.ACTION_PROPOSED and "action_hash" in event.data
            ),
            None,
        )
    if action_hash is None:
        return ()
    return store.approvals_for(action_hash)


def _effect_of(
    store: StateStore, receipt: Receipt | None, events: tuple[Event, ...]
) -> EffectRecord | None:
    """This action's effect record, or `None` where it has no effect key (SPEC-v0.2 §5)."""
    key = receipt.effect_key if receipt is not None else None
    if key is None:
        key = next((event.effect_key for event in events if event.effect_key), None)
    return None if key is None else store.get_effect(key)


def _inspection_lines(
    action_id: str,
    receipt: Receipt | None,
    effect: EffectRecord | None,
    approvals: tuple[ApprovalRecord, ...],
    events: tuple[Event, ...],
) -> list[str]:
    """The header block and the event timeline of SPEC-v0.2 §5."""
    proposed = _proposed_action(receipt, approvals)
    name = "-" if proposed is None else proposed.name
    lines = [f"{action_id}  {name}", ""]
    if proposed is not None:
        user = "" if proposed.principal.user is None else f" (user: {proposed.principal.user})"
        lines += [
            f"principal   {proposed.principal.agent}{user}",
        ]
        # SPEC-v0.3 §2.4 — the issuer and expiry in full, and the claim *names* only.
        if proposed.principal.issuer is not None:
            lines.append(f"issuer      {proposed.principal.issuer}")
        if proposed.principal.expires_at is not None:
            lines.append(f"expires     {iso_timestamp(proposed.principal.expires_at)}")
        if proposed.principal.claim_names:
            lines.append(f"claims      {', '.join(proposed.principal.claim_names)}")
        lines += [
            f"resource    {proposed.resource or '-'}",
            f"environment {proposed.environment}",
            f"arguments   {json.dumps(dict(proposed.arguments), ensure_ascii=False)}",
        ]
    if receipt is not None:
        lines.append(f"decision    {receipt.decision}  ({receipt.decision_reason})")
    for record in approvals:
        lines.append(f"approval    {_approval_summary(record)}")
    if effect is not None:
        effect_line = f"effect      {effect.effect_key}  {effect.state}  attempt {effect.attempt}"
        if effect.resolved_by is not None:
            # §5.3 names this command as a read surface for the resolver, beside `ctrlrun
            # effects`. Only one of the two had it, so on the command the section points an
            # operator at, the answer was not there in either form.
            effect_line += f"  resolved by {effect.resolved_by}"
        lines.append(effect_line)
    if receipt is not None:
        lines.append(f"receipt     {receipt.receipt_id}  {receipt.result}")
    else:
        # v0.1 §6.1 — an action suspended awaiting a human is not terminal, so there is no
        # receipt to show, and saying so beats an empty line the reader has to interpret.
        lines.append("receipt     -  (awaiting a human)")
    lines.append("")
    lines += [f"  {_event_line(event)}" for event in events]
    return lines


@dataclass(frozen=True)
class _Proposal:
    """The header fields of SPEC-v0.2 §5, from whichever record still holds them."""

    name: str
    principal: Principal
    resource: str | None
    environment: str
    arguments: Mapping[str, Any]


def _proposed_action(
    receipt: Receipt | None, approvals: tuple[ApprovalRecord, ...]
) -> _Proposal | None:
    """The action as proposed: from the receipt, else from an approval request.

    An action still awaiting a human has no receipt, and an `Event` carries neither the
    action's name nor its arguments — but the approval request stores the whole `Action`
    (v0.1 §4.1), which is the only reason a suspended action can be inspected at all. The two
    sources spell the same fields differently (`Receipt.action` is `Action.name`), so they
    are normalized here rather than duck-typed at the call site.
    """
    if receipt is not None:
        return _Proposal(
            name=receipt.action,
            principal=receipt.principal,
            resource=receipt.resource,
            environment=receipt.environment,
            arguments=receipt.arguments,
        )
    if not approvals:
        return None
    action = approvals[0].request.action
    return _Proposal(
        name=action.name,
        principal=action.principal,
        resource=action.resource,
        environment=action.environment,
        arguments=action.canonical_arguments,
    )


def _approval_summary(record: ApprovalRecord) -> str:
    answered = "" if record.approver is None else f" by {record.approver}"
    return f"{record.approval_id}  {record.status}{answered}"


def _event_line(event: Event) -> str:
    rendered = " ".join(f"{key}={value}" for key, value in event.data.items())
    return f"{event.event_id:>3}  {iso_timestamp(event.ts)}  {event.type:<28}{rendered}".rstrip()


def _effect_dict(record: EffectRecord) -> dict[str, Any]:
    """The effect record as plain JSON data, enums by value (v0.1 §6.1)."""
    return {
        "effect_key": record.effect_key,
        "state": str(record.state),
        "action_id": record.action_id,
        "attempt": record.attempt,
        "created_at": iso_timestamp(record.created_at),
        "updated_at": iso_timestamp(record.updated_at),
        "lease_expires_at": (
            None if record.lease_expires_at is None else iso_timestamp(record.lease_expires_at)
        ),
        "error": record.error,
        #: Which authority moved this out of `AMBIGUOUS`, or `None` (§5.3). Additive to
        #: `ctrlrun.inspection/v2`: a reader that does not know the key ignores it.
        "resolved_by": record.resolved_by,
    }


def _approval_dict(record: ApprovalRecord) -> dict[str, Any]:
    """One approval record as plain JSON data, enums by value (v0.1 §6.1)."""
    return {
        "approval_id": record.approval_id,
        "action_hash": record.action_hash,
        "status": str(record.status),
        "approver": record.approver,
        "created_at": iso_timestamp(record.request.created_at),
        "expires_at": iso_timestamp(record.expires_at),
        "granted_at": None if record.granted_at is None else iso_timestamp(record.granted_at),
        "consumed_at": None if record.consumed_at is None else iso_timestamp(record.consumed_at),
    }


@main.command()
@click.option(
    "--since",
    default=None,
    help="Count only receipts finished at or after this: an ISO-8601 timestamp with an "
    "offset, or <n>m / <n>h / <n>d.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit one JSON object instead.")
@STORE_URL_OPTION
def stats(since: str | None, as_json: bool, store_url: str | None) -> None:
    """Count what this store's receipts say, from the local store and nothing else.

    No network, no aggregation service, no upload: this reads the SQLite file the process it
    is diagnosing has been writing (SPEC-v0.3 §6.4).
    """
    policy = _loaded_policy()
    boundary = _since(since)
    try:
        counted = [
            receipt
            for receipt in _store(store_url).receipts()
            if boundary is None or receipt.finished_at >= boundary
        ]
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    document = _stats_document(counted, mode=policy.mode, boundary=boundary)
    if as_json:
        click.echo(json.dumps(document, ensure_ascii=False, indent=2))
        return
    for line in _stats_lines(document):
        click.echo(line)


def _since(argument: str | None) -> datetime | None:
    """Parse `--since` into an inclusive lower bound on `finished_at` (SPEC-v0.3 §6.4).

    Absolute ISO-8601 **with an offset**, or a relative `<n><unit>` where the unit is exactly
    one of `m`, `h` or `d`. No months, no weeks, no bare numbers: `2mo` and `1w` are ambiguous
    enough that guessing one would silently report the wrong window, which is worse than
    refusing.
    """
    if argument is None:
        return None
    text = argument.strip()
    if text and text[-1] in _RELATIVE_UNITS and text[:-1].isdigit() and int(text[:-1]) > 0:
        return datetime.now(UTC) - timedelta(**{_RELATIVE_UNITS[text[-1]]: int(text[:-1])})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise click.UsageError(
            f"--since {argument!r} is not a window this command accepts. Give an ISO-8601 "
            "timestamp with an offset (2026-09-01T00:00:00Z), or one of <n>m, <n>h, <n>d "
            "(30m, 24h, 7d). No months, no weeks, and no bare numbers"
        )
    return parsed


#: The three relative units of §6.4, and the `timedelta` keyword each names.
_RELATIVE_UNITS: Final[Mapping[str, str]] = {"m": "minutes", "h": "hours", "d": "days"}


def _stats_document(
    counted: list[Receipt], *, mode: str, boundary: datetime | None
) -> dict[str, Any]:
    """The numbers of §6.4, from `would_have` in observe mode and from `result` in enforce.

    The two are deliberately different shapes. An enforce-mode receipt carries no
    counterfactual and no structured `blocked_reason` — a `blocked` receipt keeps the
    duplicate/ambiguous distinction only inside `error` as exception text (v0.1 §6.1) — so
    the command reports what it can substantiate and says in its footer what it cannot.
    """
    finished = [receipt.finished_at for receipt in counted]
    document: dict[str, Any] = {
        "schema": STATS_SCHEMA,
        "mode": mode,
        "since": None if boundary is None else iso_timestamp(boundary),
        "from": iso_timestamp(min(finished)) if finished else None,
        "to": iso_timestamp(max(finished)) if finished else None,
        "actions": len(counted),
    }
    if mode != OBSERVE:
        refused = [r for r in counted if r.result is ReceiptResult.DENIED]
        document["denied"] = len(refused)
        document["denied_by_reason"] = _tally(r.decision_reason for r in refused)
        document["ambiguous_outcomes"] = len(
            [r for r in counted if r.result is ReceiptResult.AMBIGUOUS]
        )
        return document
    # Every observe-mode number comes off `would_have`, so the counterfactuals are pulled out
    # once. A receipt with none was written by one of §6.2's still-refuses rows: the action was
    # genuinely stopped and there is nothing counterfactual to count.
    counterfactuals = [r.would_have for r in counted if r.would_have is not None]
    denied = [w for w in counterfactuals if w.decision is Decision.DENY]
    blocked = [w for w in counterfactuals if w.blocked_reason in BLOCKED_BY_STATE]
    document["would_have_been_denied"] = len(denied)
    document["denied_by_reason"] = _tally(w.blocked_reason or w.reason for w in denied)
    document["would_have_needed_approval"] = len(
        [w for w in counterfactuals if w.blocked_reason == BLOCKED_APPROVAL_REQUIRED]
    )
    document["would_have_been_blocked"] = len(blocked)
    document["blocked_by_reason"] = _tally(w.blocked_reason for w in blocked)
    document["ambiguous_outcomes"] = len(
        [r for r in counted if r.execution is ReceiptResult.AMBIGUOUS]
    )
    return document


def _tally(reasons: Iterable[str | None]) -> dict[str, int]:
    """Counts by reason, largest first, then alphabetically so the output is stable."""
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def _stats_lines(document: Mapping[str, Any]) -> list[str]:
    """§6.4's report. Every number comes from the document, so `--json` cannot disagree."""
    window = f"{document['from'] or '-'} .. {document['to'] or '-'}"
    lines = [f"CTRLRun — {window}   ({document['mode']} mode)", ""]
    lines.append(_stat("actions", document["actions"]))
    if document["mode"] == OBSERVE:
        lines.append(_stat("would have been denied", document["would_have_been_denied"]))
        lines += _breakdown(document["denied_by_reason"])
        lines.append(_stat("would have needed approval", document["would_have_needed_approval"]))
        lines.append(_stat("would have been blocked", document["would_have_been_blocked"]))
        lines += _breakdown(document["blocked_by_reason"])
    else:
        lines.append(_stat("denied", document["denied"]))
        lines += _breakdown(document["denied_by_reason"])
    lines.append(_stat("ambiguous outcomes", document["ambiguous_outcomes"]))
    lines.append("")
    if document["mode"] != OBSERVE:
        # §6.4 — say what is missing rather than print a line the receipts cannot substantiate.
        lines.append(
            "An enforce-mode receipt carries no structured blocked_reason, so the duplicate "
            "and ambiguous breakdown is not reported."
        )
    lines.append("Actions still awaiting a human have no receipt yet and are not counted.")
    return lines


def _stat(label: str, count: int) -> str:
    return f"{label:<30}{count:>6}"


def _breakdown(counts: Mapping[str, int]) -> list[str]:
    return [f"   {reason:<27}{count:>6}" for reason, count in counts.items()]


@main.command()
@click.option(
    "--authority",
    "authority_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="A standalone authority document, as `ctrlrun gateway --authority` takes.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit one ctrlrun.verify/v1 document.")
@click.option(
    "--junit",
    "junit_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also write a JUnit XML file for CI.",
)
@click.option("--only", default="", help="Comma-separated guarantee ids, e.g. G1,G3.")
@click.option("--store-url", "store_url", default=None, help="Reserved; v0.4 accepts 'sqlite'.")
def verify(
    authority_path: Path | None,
    as_json: bool,
    junit_path: Path | None,
    only: str,
    store_url: str | None,
) -> None:
    """Run the declared guarantees against this configuration (SPEC-v0.4).

    Every scenario runs against a scratch store created and destroyed for the run. The store
    an agent is using is not opened, not read and not created.

    Exit codes: 0 every applicable guarantee passed and at least one was applicable; 1 a
    guarantee FAILED; 2 the configuration was refused or is unusable — which includes
    `mode: observe` and a configuration in which nothing could be exercised; 3 an internal
    error in verify itself.
    """
    # Imported here, not at module scope: verify is an operator's tool and not part of the
    # action path, and `import ctrlrun` must not reach it (SPEC-v0.4 §1, T125b).
    from ..verify import VerifyInternalError, VerifyRefused
    from ..verify import run as run_verify

    try:
        # The banner first, for every invocation, exactly as SPEC-v0.3 §6.5 requires — and
        # then, under observe mode, `run` refuses. `ctrlrun verify` stays in the left column.
        _loaded_policy()
    except PolicyError as exc:
        click.echo(f"ctrlrun verify: {exc}", err=True)
        raise SystemExit(2) from exc

    try:
        report = run_verify(
            None,
            authority=authority_path,
            only=(only,) if only else (),
            store_url=store_url,
        )
    except VerifyRefused as refused:
        click.echo(f"ctrlrun verify: {refused}", err=True)
        raise SystemExit(2) from refused
    except VerifyInternalError as internal:
        click.echo(f"ctrlrun verify: internal error: {internal}", err=True)
        raise SystemExit(3) from internal

    if junit_path is not None:
        junit_path.write_text(report.to_junit(), encoding="utf-8")
    click.echo(report.to_json() if as_json else report.to_text())
    if report.exit_code == 2:
        # §3.8 — the only exit-2 the report itself can produce. Said on stderr, so a `--json`
        # stdout stays parseable: `0/0` reported as success is the same false green as `8/8`
        # with five N/As.
        click.echo(
            "ctrlrun verify: no guarantee in the catalogue is applicable to this "
            "configuration, so nothing was checked and nothing is claimed.",
            err=True,
        )
    raise SystemExit(report.exit_code)


@main.command()
@click.option("--parent", required=True, help="The grant or delegation being narrowed.")
@click.option(
    "--file",
    "grant_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A one-grant YAML document, with the keys of SPEC-v0.3 §4.2 minus 'id'.",
)
@click.option(
    "--as",
    "as_who",
    required=True,
    help="The delegating principal: AGENT or AGENT/USER. Split on the first '/'.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit one JSON object instead.")
def delegate(parent: str, grant_file: Path, as_who: str, as_json: bool) -> None:
    """Create a delegated grant beneath an existing one.

    `--as` is an **assertion**, not an authentication: it supplies the creating principal for
    SPEC-v0.3 §5.3 rule 4, and it is free text typed by whoever runs the command. The record
    keeps `created_via="cli"` so a reader of the evidence can tell an act from an assertion.
    An agent name containing '/' cannot be written here, because `--as a/b` would otherwise be
    ambiguous between the agent `a/b` acting alone and the agent `a` acting for `b`.
    """
    agent, _, user = as_who.partition("/")
    if not agent:
        raise click.UsageError("--as needs an agent name: AGENT or AGENT/USER")
    try:
        _loaded_policy()
        control = Control.from_file()
        grant = grant_from_yaml(grant_file.read_text(encoding="utf-8"), source=str(grant_file))
        created = control._delegate(
            parent, grant, by=Principal(agent=agent, user=user or None), via="cli"
        )
    except AuthorityEscalation as exc:
        # §5.7 — a refusal here exits non-zero and **names the rule that failed**, which is the
        # §5.3 reason rather than the prose. The two vocabularies are disjoint, so an operator
        # reading `containment` knows to look at §5.4 and not at an evaluation-time denial.
        raise click.ClickException(f"{exc.reason}: {exc}") from exc
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    if as_json:
        click.echo(json.dumps(_delegation_dict(created), ensure_ascii=False))
        return
    click.echo(f"created {created.delegation_id}")
    click.echo(f"parent {created.parent_id} at depth {created.depth}")
    click.echo(f"revoke it with: ctrlrun revoke {created.delegation_id}")


@main.command()
@click.argument("delegation_id")
@click.option("--by", "by", default=CLI_APPROVER, show_default=True, help="Who revoked it.")
def revoke(delegation_id: str, by: str) -> None:
    """Revoke a delegation, and with it every delegation beneath it.

    Transitive by structure and not reversible: there is no `unrevoke`, because the operation
    whose safety matters is the one taken in a hurry (SPEC-v0.3 §5.7). Revoking an
    already-revoked delegation is idempotent and exits 0.
    """
    try:
        _loaded_policy()
        control = Control.from_file()
        before = control.store.get_delegation(delegation_id)
        control.revoke(delegation_id, by=by)
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    if before is not None and before.revoked_at is not None:
        click.echo(f"{delegation_id} was already revoked at {iso_timestamp(before.revoked_at)}")
        return
    click.echo(f"revoked {delegation_id} by {by}")
    click.echo("every delegation beneath it is denied from the next evaluation")


def _delegation_dict(delegation: Delegation) -> dict[str, Any]:
    """One delegation as portable JSON, for `ctrlrun delegate --json` (SPEC-v0.3 §5.2)."""
    return {
        "delegation_id": delegation.delegation_id,
        "parent_id": delegation.parent_id,
        "depth": delegation.depth,
        "created_by_agent": delegation.created_by.agent,
        "created_by_user": delegation.created_by.user,
        "created_via": delegation.created_via,
        "created_at": iso_timestamp(delegation.created_at),
    }


@main.command()
@click.option("--upstream", required=True, help="The MCP server this gateway fronts.")
@click.option("--alias", required=True, help="Names the upstream in 'mcp.<alias>.<tool>'.")
@click.option("--listen", default="127.0.0.1:8900", show_default=True, help="HOST:PORT.")
@click.option("--path", default="/mcp", show_default=True, help="The MCP endpoint path.")
@click.option("--principal", default=None, help="A fixed agent name, for one tenant.")
@click.option("--principal-header", default=None, help="Take the agent from this header.")
@click.option(
    "--principal-from-client-info",
    is_flag=True,
    hidden=True,
    help="Removed in 0.3. Use --principal-header.",
)
@click.option("--user-header", default=None, help="Take principal.user from this header.")
@click.option(
    "--environment",
    default=None,
    help="The deployment this gateway acts in. Default: $CTRLRUN_ENVIRONMENT, else the "
    "policy document, else production (SPEC-v0.3 §2.5).",
)
@click.option("--upstream-timeout", type=float, default=30.0, show_default=True)
@click.option("--max-body-bytes", type=int, default=1024 * 1024, show_default=True)
@click.option("--allow-origin", "allow_origins", multiple=True, help="Repeatable.")
@click.option("--allow-remote", is_flag=True, help="Permit a non-loopback --listen.")
@click.option("--public-url", default=None, help="Where the gateway is reachable, for respond_to.")
@click.option("--webhook-url", default=None, help="Notify this endpoint on APPROVAL_REQUESTED.")
@click.option(
    "--webhook-secret-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Read the shared secret from here instead of $CTRLRUN_WEBHOOK_SECRET.",
)
@click.option(
    "--allow-insecure-webhook",
    is_flag=True,
    help="Permit an http:// webhook url, loopback only.",
)
@click.option(
    "--authority",
    "authority_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Load the authority: section from a separate YAML document (SPEC-v0.3 §8.3).",
)
@click.option("--identity-jwt", is_flag=True, help="Verify a bearer JWT (ctrlrun[identity]).")
@click.option("--identity-jwt-jwks-url", default=None, help="Fetch keys from this JWKS (HTTPS).")
@click.option(
    "--identity-jwt-public-key",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="A PEM public key file.",
)
@click.option(
    "--identity-jwt-secret-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Read the HS* shared secret from here. Never a flag value: a secret on a command "
    "line is in every process listing on the host.",
)
@click.option(
    "--identity-jwt-algorithms",
    "identity_jwt_algorithms",
    multiple=True,
    help="Repeatable, required. There is no default and no wildcard.",
)
@click.option("--identity-jwt-issuer", default=None, help="Matched exactly. Required.")
@click.option("--identity-jwt-audience", default=None, help="Matched by membership. Required.")
@click.option(
    "--identity-jwt-token-type",
    default=None,
    help='Required. The token\'s typ, e.g. at+jwt. Pass "" for "this issuer sets no typ".',
)
@click.option("--identity-jwt-header", default="authorization", show_default=True)
@click.option("--identity-jwt-agent-claim", default="sub", show_default=True)
@click.option("--identity-jwt-user-claim", default=None, help="Which claim is principal.user.")
@click.option(
    "--identity-jwt-claim",
    "identity_jwt_claims",
    multiple=True,
    help="Repeatable: which verified claims reach the receipt. An allow-list.",
)
@click.option("--identity-jwt-leeway", type=float, default=60.0, show_default=True)
@click.option("--identity-jwt-jwks-min-refresh", type=float, default=30.0, show_default=True)
@click.option(
    "--identity-jwt-http-timeout",
    type=float,
    default=5.0,
    show_default=True,
    help="Bounds the JWKS fetch. Deliberately not --upstream-timeout: the fetch runs on the "
    "request thread before any decision, so the two must not be one knob.",
)
@click.option("--otel", is_flag=True, help="Export one span per action (ctrlrun[otel]).")
@click.option(
    "--otel-arguments",
    is_flag=True,
    help="Include argument values as span attributes. Off by default: arguments carry "
    "customer identifiers and amounts, and a trace backend is not the receipt store.",
)
def gateway(
    upstream: str,
    alias: str,
    listen: str,
    path: str,
    principal: str | None,
    principal_header: str | None,
    principal_from_client_info: bool,
    user_header: str | None,
    environment: str,
    upstream_timeout: float,
    max_body_bytes: int,
    allow_origins: tuple[str, ...],
    allow_remote: bool,
    public_url: str | None,
    webhook_url: str | None,
    webhook_secret_file: str | None,
    allow_insecure_webhook: bool,
    authority_path: str | None,
    identity_jwt: bool,
    identity_jwt_jwks_url: str | None,
    identity_jwt_public_key: str | None,
    identity_jwt_secret_file: str | None,
    identity_jwt_algorithms: tuple[str, ...],
    identity_jwt_issuer: str | None,
    identity_jwt_audience: str | None,
    identity_jwt_token_type: str | None,
    identity_jwt_header: str,
    identity_jwt_agent_claim: str,
    identity_jwt_user_claim: str | None,
    identity_jwt_claims: tuple[str, ...],
    identity_jwt_leeway: float,
    identity_jwt_jwks_min_refresh: float,
    identity_jwt_http_timeout: float,
    otel: bool,
    otel_arguments: bool,
) -> None:
    """Front an MCP server, applying this directory's policy to every tools/call."""
    if principal_from_client_info:
        # SPEC-v0.3 §8.1 — a hard error, not a silent ignore and not a warning-and-continue.
        # The flag chose the gateway's principal; a gateway that started anyway would run with
        # a different principal than the operator asked for, and under v0.3 the principal is an
        # authorization input.
        raise click.ClickException(
            "--principal-from-client-info was removed in 0.3. Use --principal-header NAME, set "
            "by a proxy that authenticates the caller and overwrites the header on every "
            "request. clientInfo is self-reported and the MCP specification says implementations "
            "SHOULD NOT rely on it for security decisions (SPEC-v0.3 §8.1)."
        )
    host, _, port = listen.rpartition(":")
    try:
        from ..gateway import serve

        # §6.5 — before anything else this command prints. The removed-flag guard above is a
        # usage error and runs first deliberately: it must not depend on a policy being
        # loadable, or a gateway started in a directory with no policy would report the wrong
        # problem. The rest of §8.4's startup block is printed by `serve`, which is where the
        # identity provider and the authority section are actually resolved.
        _loaded_policy()
        serve(
            upstream=upstream,
            alias=alias,
            host=host or "127.0.0.1",
            port=int(port),
            path=path,
            principal=principal,
            principal_header=principal_header,
            user_header=user_header,
            environment=environment,
            upstream_timeout=upstream_timeout,
            max_body_bytes=max_body_bytes,
            allow_origins=tuple(allow_origins),
            allow_remote=allow_remote,
            public_url=public_url,
            webhook_url=webhook_url,
            webhook_secret_file=webhook_secret_file,
            allow_insecure_webhook=allow_insecure_webhook,
            authority=authority_path,
            identity_jwt=identity_jwt,
            identity_jwt_jwks_url=identity_jwt_jwks_url,
            identity_jwt_public_key=identity_jwt_public_key,
            identity_jwt_secret_file=identity_jwt_secret_file,
            identity_jwt_algorithms=tuple(identity_jwt_algorithms),
            identity_jwt_issuer=identity_jwt_issuer,
            identity_jwt_audience=identity_jwt_audience,
            identity_jwt_token_type=identity_jwt_token_type,
            identity_jwt_header=identity_jwt_header,
            identity_jwt_agent_claim=identity_jwt_agent_claim,
            identity_jwt_user_claim=identity_jwt_user_claim,
            identity_jwt_claims=tuple(identity_jwt_claims),
            identity_jwt_leeway=identity_jwt_leeway,
            identity_jwt_jwks_min_refresh=identity_jwt_jwks_min_refresh,
            identity_jwt_http_timeout=identity_jwt_http_timeout,
            otel=otel,
            otel_arguments=otel_arguments,
        )
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    except KeyboardInterrupt:  # pragma: no cover - an operator pressing ctrl-c
        click.echo("")


def _receipt_line(receipt: Receipt) -> str:
    return (
        f"{iso_timestamp(receipt.finished_at)}  {receipt.receipt_id}  {receipt.action}  "
        f"{receipt.decision}/{receipt.result}  {receipt.effect_key or '-'}  "
        f"{receipt.principal.agent}"
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _effect_line(record: EffectRecord) -> str:
    """One effect, with the two things the state alone does not say (SPEC-v0.6 §5.2, §5.3).

    **An expired lease is shown as expired.** Nothing sweeps, so a lease that lapses and is never
    contended stays `EXECUTING` in the table indefinitely -- and an operator reading `executing`
    cannot tell it from live work. `EffectRecord.lease_is_live` already answers the question; the
    line just stopped hiding it. This is a display change and not a transition: `list_effects` is
    a read and reads never move anything.

    **And who resolved it**, where somebody did, because a human overriding the kernel and a
    reconcile hook answering are different authorities (§5.3).
    """
    line = (
        f"{record.effect_key}  {record.state}  attempt {record.attempt}  "
        f"{record.action_id}  {iso_timestamp(record.updated_at)}"
    )
    if record.state in _LEASED and not record.lease_is_live(_utc_now()):
        line += "  (lease expired)"
    if record.resolved_by is not None:
        line += f"  resolved by {record.resolved_by}"
    return line


if __name__ == "__main__":  # pragma: no cover
    main()
