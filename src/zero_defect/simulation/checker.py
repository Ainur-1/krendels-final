"""
Прогон сценария в чистой системе и сверка итога с ожидаемым.

Ожидание — частичное: в expected.json перечислено только то, что сценарий обязан
показать, и сверяется ровно это. Ключ со суффиксом _include сверяется как «содержит»,
а не «равно»: например, среди оснований разбора должно быть отклонение станка, но
кроме него там могут быть и другие.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from zero_defect.config import SCENARIOS_DIR, Settings, load_settings
from zero_defect.service import QualitySystem
from zero_defect.simulation.replay import load_steps, replay
from zero_defect.simulation.tamper import update_is_prevented


def snapshot(system: QualitySystem, run: dict) -> dict:
    """Наблюдаемый итог в той же форме, в какой записаны ожидания."""

    state = system.state()
    history = state.history
    cards = {}
    for card in state.cards.values():
        assessment = card.assessment or {}
        cards[f"{card.item_id}/{card.defect_type}"] = {
            "nc_id": card.nc_id,
            "status": card.status,
            "signals": len(card.signals),
            "severity": card.severity,
            "stage": assessment.get("stage"),
            "stage_confidence": assessment.get("stage_confidence"),
            "presumed_cause": assessment.get("presumed_cause"),
            "confirmed_cause": card.confirmed_cause,
            "operator_blamed": assessment.get("operator_blamed"),
            "candidate_operations": [
                history.runs[run_id].operation_id
                for run_id in assessment.get("candidate_run_ids", [])
            ],
            "participants": sorted(
                {item["operator_id"] for item in assessment.get("participants", [])}
            ),
            "evidence_kinds": sorted({item["kind"] for item in assessment.get("evidence", [])}),
            "alternatives_count": len(assessment.get("alternatives", [])),
            "missing_count": len(assessment.get("missing", [])),
            "decisions": [decision.action for decision in card.decisions],
            "decision_authors": sorted({decision.author_id for decision in card.decisions}),
            "evidence_refs": sum(len(signal.observation.event.evidence) for signal in card.signals),
        }
    runs = {
        f"{item.operation_id}/{item.item_id}" + ("/rework" if item.is_rework else ""): {
            "status": item.status,
            "machine_deviations": sum(
                1
                for event_id in item.machine_event_ids
                if history.events_by_id[event_id].machine_state
                in ("warning", "deviation", "stopped")
            ),
            "station_time_s": item.station_time_s,
            "active_time_s": item.active_time_s,
            "reported_duration_s": item.reported_duration_s,
            "shift_origin": item.shift_origin,
        }
        for item in history.runs.values()
    }
    statuses = Counter(result.status for result in run["results"])
    error_codes = sorted({error["code"] for result in run["results"] for error in result.errors})
    warning_codes = sorted(
        {warning["code"] for result in run["results"] for warning in result.warnings}
    )
    report = system.verify_integrity()
    return {
        "ingest": {
            **system.ingest_summary(),
            "responses": dict(sorted(statuses.items())),
            "error_codes": error_codes,
            "warning_codes": warning_codes,
        },
        "items": {item_id: {"status": status} for item_id, status in state.statuses.items()},
        "nonconformances": cards,
        "runs": runs,
        "metrics": state.metrics,
        "source_gaps": history.source_gaps(),
        "source_gap_sources": sorted(history.source_gaps()),
        "integrity": {
            "ok": report.ok,
            "problems": sorted({problem["problem"] for problem in report.problems}),
            "update_prevented": update_is_prevented(system.database),
        },
        "decision_errors": run["decision_errors"],
    }


def _match(expected, observed, path: str, mismatches: list[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            mismatches.append(f"{path}: ожидался объект, получено {observed!r}")
            return
        for key, value in expected.items():
            if key.endswith("_include"):
                base = key[: -len("_include")]
                actual = observed.get(base, [])
                missing = [item for item in value if item not in actual]
                if missing:
                    mismatches.append(f"{path}.{base}: нет {missing}, есть {actual}")
                continue
            if key not in observed:
                mismatches.append(f"{path}.{key}: отсутствует")
                continue
            _match(value, observed[key], f"{path}.{key}", mismatches)
    elif expected != observed:
        mismatches.append(f"{path}: ожидалось {expected!r}, получено {observed!r}")


def check(name: str, scenarios_dir: Path = SCENARIOS_DIR) -> tuple[list[str], dict]:
    """Прогоняет сценарий в пустой системе; возвращает расхождения и наблюдение."""

    folder = scenarios_dir / name
    steps = load_steps(folder / "input.jsonl")
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        settings = fresh_settings(Path(tmp))
        system = QualitySystem(settings)
        try:
            run = replay(system, steps)
            observed = snapshot(system, run)
        finally:
            system.close()
        # Повторное открытие показывает, что изменённая запись не расшифровывается:
        # подмена видна не только проверкой цепочки, но и при обычном чтении.
        reopened = QualitySystem(settings)
        try:
            observed["reopened"] = {"unreadable_records": len(reopened.state().unreadable)}
        finally:
            reopened.close()
    mismatches: list[str] = []
    _match(expected.get("expect", {}), observed, name, mismatches)
    return mismatches, observed


def fresh_settings(root: Path, **overrides) -> Settings:
    """Настройки пустой системы во временном каталоге; по умолчанию база — SQLite."""

    values = {
        "storage_url": f"sqlite:///{root / 'ledger.sqlite3'}",
        "keys_dir": root / "keys",
        "integrations": (),
        "adapters": {},
    }
    values.update(overrides)
    return load_settings(**values)
