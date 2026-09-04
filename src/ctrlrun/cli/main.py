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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import click

from ..action import Principal
from ..approval import ApprovalRecord
from ..control import DEFAULT_STATE_DIR, state_path
from ..effect import RESOLVED_BY_HUMAN, EffectRecord, EffectState
from ..errors import CTRLRunError
from ..policy import DEFAULT_POLICY_FILENAME
from ..receipt import Event, EventType, Receipt, iso_timestamp
from ..state import RESOLUTIONS, SQLiteStateStore
from .demo import run_demo

#: Who the CLI records as the answer's author. Free text in v0.1 (SPEC-v0.1 §4.1).
CLI_APPROVER: Final = "cli:local"

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


def _store() -> SQLiteStateStore:
    """Open the store this working tree's agents use (SPEC-v0.1 §8)."""
    return SQLiteStateStore(state_path())


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
    """Run the four failure scenarios, in process, with no network."""
    run_demo(Path.cwd())


@main.command()
@click.argument("request_id")
def approve(request_id: str) -> None:
    """Grant a pending approval request."""
    store = _store()
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
def deny(request_id: str) -> None:
    """Refuse a pending approval request."""
    store = _store()
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
def receipts(last: int | None, as_json: bool) -> None:
    """Show the receipts this store holds."""
    try:
        found = _store().receipts()
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
def effects(state: str | None) -> None:
    """Show the logical effects this store knows about."""
    try:
        found = _store().list_effects(None if state is None else EffectState(state))
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
def resolve(effect_key: str, committed: bool, failed: bool) -> None:
    """Say what actually happened to an effect with an unknown outcome."""
    if committed == failed:
        # SPEC §5.2 — a resolution is a human's claim about the real world, and the two
        # claims are opposites. Neither flag says nothing; both say nothing twice.
        raise click.UsageError(
            f"say which it was: exactly one of --committed or --failed "
            f"({'|'.join(sorted(RESOLUTIONS))} are the only answers)"
        )
    outcome = EffectState.COMMITTED if committed else EffectState.FAILED
    store = _store()
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
def inspect(action_id: str, as_json: bool) -> None:
    """Show one action's whole history: proposal, decision, approval, effect, receipt."""
    store = _store()
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
    store: SQLiteStateStore, receipt: Receipt | None, events: tuple[Event, ...]
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
    store: SQLiteStateStore, receipt: Receipt | None, events: tuple[Event, ...]
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
        lines.append(f"effect      {effect.effect_key}  {effect.state}  attempt {effect.attempt}")
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

        _announce_actions_without_an_effect()
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
            otel=otel,
            otel_arguments=otel_arguments,
        )
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    except KeyboardInterrupt:  # pragma: no cover - an operator pressing ctrl-c
        click.echo("")


def _announce_actions_without_an_effect() -> None:
    """SPEC-v0.2 §3.2 — print the actions with no `effect:` at startup.

    A write with no effect key is exactly the configuration this product exists to prevent,
    and it should be visible on the line that starts the process rather than discovered in a
    receipt three weeks later.
    """
    from ..policy import Policy

    policy = Policy.from_file()
    keyless = sorted(name for name in policy.actions if policy.effect_template(name) is None)
    if not keyless:
        return
    click.echo(f"{len(keyless)} action(s) have no effect: template and get no reservation:")
    for name in keyless:
        click.echo(f"  {name}")
    click.echo("That is right for a read, and wrong for anything that changes the world.")


def _receipt_line(receipt: Receipt) -> str:
    return (
        f"{iso_timestamp(receipt.finished_at)}  {receipt.receipt_id}  {receipt.action}  "
        f"{receipt.decision}/{receipt.result}  {receipt.effect_key or '-'}  "
        f"{receipt.principal.agent}"
    )


def _effect_line(record: EffectRecord) -> str:
    return (
        f"{record.effect_key}  {record.state}  attempt {record.attempt}  "
        f"{record.action_id}  {iso_timestamp(record.updated_at)}"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
