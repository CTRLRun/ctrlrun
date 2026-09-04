"""`ctrlrun verify` — the operator's own configuration, against the kernel's own refusals.

SPEC-v0.4 §1, §9.1. Core: stdlib, `pyyaml` and `click`, and **not** re-exported from
`ctrlrun`. A verification tool behind an extra is one half the deployments never run, so this
is not optional; but it is an operator's tool and not part of the action path, so `import
ctrlrun` must not import it and nothing in the kernel may come to depend on it (T125b).

`verify/` sits **above** `control.py`, beside `cli/`: it composes `Control`, `Policy` and
`Authority` the way an application does, and it proposes no action of its own — it drives the
entry points `v0.3 §4.3.1` already enumerates and asserts that each applies the checks that
table requires (§3.9).

There is **no flag that relaxes a check**. No argument, no environment variable, nothing that
makes verify's `Control` behave differently from the operator's. The moment one exists, the
thing being verified is not the thing that ships.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..policy import OBSERVE
from . import guarantees as reg
from .report import Counterexample, GuaranteeResult, Report, Status
from .scenarios import (
    SQLITE_STORE_URL,
    Engine,
    VerifyInternalError,
    VerifyRefused,
    load,
)

__all__ = [
    "Counterexample",
    "GuaranteeResult",
    "Report",
    "Status",
    "VerifyInternalError",
    "VerifyRefused",
    "run",
]


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ctrlrun")
    except PackageNotFoundError:  # pragma: no cover - a source tree with no install
        return "0.0.0"


def _selected(only: Sequence[str]) -> tuple[str, ...] | None:
    """§4.6 — the ids `--only` names, or `None` for the whole catalogue.

    An id outside the registry exits 2 naming the id and the registry: silently ignoring one
    would run fewer guarantees than the operator asked for and report success.
    """
    if not only:
        return None
    chosen: list[str] = []
    for raw in only:
        for part in str(raw).split(","):
            name = part.strip()
            if not name:
                continue
            if name not in reg.BY_ID:
                raise VerifyRefused(
                    f"--only names {name!r}, which is not in {reg.CATALOGUE}. The registry is "
                    f"{', '.join(reg.BY_ID)}"
                )
            if name not in chosen:
                chosen.append(name)
    if not chosen:
        raise VerifyRefused("--only was given no guarantee id")
    return tuple(chosen)


def run(
    config: str | os.PathLike[str] | None = None,
    *,
    authority: str | os.PathLike[str] | None = None,
    only: Sequence[str] = (),
    store_url: str | None = None,
) -> Report:
    """Run the applicable guarantees against this configuration and report (§9.1).

    Raises `VerifyRefused` where the configuration is refused or unusable (exit 2) and
    `VerifyInternalError` where verify itself is at fault (exit 3). Everything else — a
    guarantee that failed, a guarantee that could not be exercised — is in the `Report`.

    The scratch directory is removed when the run ends, **including when it ends by
    exception**. The operator's store is not opened, not read and not created (§3.5, T103).
    """
    selection = _selected(only)
    if store_url is not None and store_url.strip() not in ("", SQLITE_STORE_URL):
        raise VerifyRefused(
            f"--store-url {store_url!r} names a backend v0.4 does not have. The only value is "
            f"{SQLITE_STORE_URL!r}; a second store backend is v0.6 (SPEC-v0.4 §3.1)"
        )
    loaded = load(config, authority)
    if loaded.policy.mode == OBSERVE:
        # §3.8 — running the scenarios and reporting ten failures would be true and useless;
        # running them in a synthetic enforce mode would report guarantees about a
        # configuration nobody deployed. The message names the one-line edit.
        raise VerifyRefused(
            f"{loaded.policy_path} declares 'mode: observe'. Observe mode enforces nothing, so "
            "there is nothing to verify: every refusal these guarantees assert would be "
            "recorded rather than made. Change the top-level 'mode:' to 'enforce' and run "
            "again (SPEC-v0.4 §3.8)"
        )

    started_at = datetime.now(UTC)
    scratch = Path(tempfile.mkdtemp(prefix="ctrlrun-verify-"))
    results: list[GuaranteeResult] = []
    try:
        engine = Engine(loaded, scratch)
        for guarantee in reg.GUARANTEES:
            if selection is not None and guarantee.id not in selection:
                results.append(
                    GuaranteeResult(
                        id=guarantee.id,
                        title=guarantee.title,
                        status=Status.SKIPPED,
                        reason=reg.NOT_SELECTED,
                        descends_from=guarantee.descends_from,
                    )
                )
                continue
            scenario = getattr(engine, guarantee.id.lower())
            results.append(scenario())
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    finished_at = datetime.now(UTC)

    return Report(
        guarantees=tuple(results),
        policy={
            "path": str(loaded.policy_path),
            "sha256": loaded.policy_sha,
            "schema": loaded.policy.schema,
            "mode": loaded.policy.mode,
            "actions": len(loaded.policy.actions),
        },
        authority=(
            None
            if loaded.authority is None
            else {
                "path": str(loaded.authority_path),
                "sha256": loaded.authority_sha,
                "grants": len(loaded.authority.grants),
                "max_delegation_depth": loaded.authority.max_delegation_depth,
            }
        ),
        store={"backend": SQLITE_STORE_URL, "scratch": True},
        started_at=started_at,
        finished_at=finished_at,
        ctrlrun_version=_version(),
        partial=selection is not None,
    )
