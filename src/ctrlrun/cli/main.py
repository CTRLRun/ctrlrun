"""click command group for the ctrlrun CLI. Build-list item 8; SPEC-v0.1 §8.

The CLI is the human end of the kernel: it answers approval requests, shows the evidence,
and resolves the one state no machine may resolve for itself (§5.2). It is also the only
place in CTRLRun that prints.

Every command works on the store an agent is already using — `.ctrlrun/state.db` beside the
policy, or wherever `$CTRLRUN_STATE` says (§8) — so approving here answers the request an
agent is waiting on in another shell.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import click

from ..control import DEFAULT_STATE_DIR, state_path
from ..effect import EffectRecord, EffectState
from ..errors import CTRLRunError
from ..policy import DEFAULT_POLICY_FILENAME
from ..receipt import Event, EventType, Receipt, iso_timestamp
from ..state import RESOLUTIONS, SQLiteStateStore
from .demo import run_demo

#: Who the CLI records as the answer's author. Free text in v0.1 (SPEC-v0.1 §4.1).
CLI_APPROVER: Final = "cli:local"

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
  stripe.refund:
    rules:
      - when: { amount_lte: 50000 }        # ≤ €500.00
        decision: allow
      - when: { amount_lte: 500000 }       # ≤ €5,000.00
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
            )
        )
    except CTRLRunError as exc:
        raise _fail(exc) from exc
    click.echo(f"{effect_key} resolved {record.state} by {CLI_APPROVER}")
    if record.state is EffectState.FAILED:
        click.echo("a retry of this effect is now permitted")


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
