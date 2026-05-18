"""Load/save the tradeoff ledger at `.jig/spec/tradeoffs.yaml`."""

from __future__ import annotations

from pathlib import Path

import yaml

from jig.schemas.tradeoffs import Tradeoff, TradeoffLedger

_LEDGER_PATH = Path(".jig") / "spec" / "tradeoffs.yaml"


def ledger_path(project_root: Path) -> Path:
    return project_root / _LEDGER_PATH


def load_ledger(project_root: Path) -> TradeoffLedger:
    path = ledger_path(project_root)
    if not path.exists():
        return TradeoffLedger()
    data = yaml.safe_load(path.read_text()) or {}
    return TradeoffLedger.model_validate(data)


def save_ledger(project_root: Path, ledger: TradeoffLedger) -> None:
    path = ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            ledger.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )
    )


def add_tradeoff(project_root: Path, tradeoff: Tradeoff) -> TradeoffLedger:
    """Upsert a tradeoff (replace by id if already present) and persist."""
    ledger = load_ledger(project_root)
    updated = [t for t in ledger.tradeoffs if t.id != tradeoff.id]
    updated.append(tradeoff)
    new_ledger = ledger.model_copy(update={"tradeoffs": updated})
    save_ledger(project_root, new_ledger)
    return new_ledger
