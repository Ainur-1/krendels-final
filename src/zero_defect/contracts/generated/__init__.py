# Сгенерировано scripts/generate_contracts.py (datamodel-code-generator 0.83.0).
# Не править руками.
"""Модели контракта событий по версиям и типам событий."""

from __future__ import annotations

from pydantic import BaseModel

from . import v1_0
from . import v1_1

MODELS: dict[str, dict[str, type[BaseModel]]] = {
    "1.0": {
        "component_linked": v1_0.ComponentLinked,
        "inspection_reported": v1_0.InspectionReported,
        "item_registered": v1_0.ItemRegistered,
        "machine_state": v1_0.MachineState,
        "operation_finished": v1_0.OperationFinished,
        "operation_paused": v1_0.OperationPaused,
        "operation_resumed": v1_0.OperationResumed,
        "operation_started": v1_0.OperationStarted,
        "operator_action": v1_0.OperatorAction,
    },
    "1.1": {
        "component_linked": v1_1.ComponentLinked,
        "inspection_reported": v1_1.InspectionReported,
        "item_registered": v1_1.ItemRegistered,
        "machine_state": v1_1.MachineState,
        "operation_finished": v1_1.OperationFinished,
        "operation_paused": v1_1.OperationPaused,
        "operation_resumed": v1_1.OperationResumed,
        "operation_started": v1_1.OperationStarted,
        "operator_action": v1_1.OperatorAction,
    },
}
