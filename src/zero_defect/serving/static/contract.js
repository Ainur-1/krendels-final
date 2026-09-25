// Сгенерировано scripts/generate_contracts.py (datamodel-code-generator 0.83.0).
// Не править руками: интерфейс берёт отсюда перечисления контракта.
export const CONTRACT = {
  "versions": [
    "1.0",
    "1.1"
  ],
  "eventTypes": [
    "component_linked",
    "inspection_reported",
    "item_registered",
    "machine_state",
    "operation_finished",
    "operation_paused",
    "operation_resumed",
    "operation_started",
    "operator_action"
  ],
  "enums": {
    "InspectionResult": [
      "defect_signs_found",
      "no_defect_signs",
      "not_assessable"
    ],
    "CheckpointKind": [
      "incoming",
      "before_operation",
      "after_operation",
      "final"
    ],
    "ObservationQuality": [
      "good",
      "degraded",
      "poor",
      "unknown"
    ],
    "Severity": [
      "minor",
      "major",
      "critical"
    ],
    "DurationUnit": [
      "s",
      "min",
      "h"
    ],
    "DurationMeaning": [
      "active_processing",
      "station_total",
      "other"
    ],
    "OperationOutcome": [
      "completed",
      "aborted"
    ],
    "ActionType": [
      "mode_change",
      "confirmation",
      "check_skipped",
      "manual_decision",
      "tool_change",
      "other"
    ],
    "MachineStateValue": [
      "running",
      "idle",
      "warning",
      "deviation",
      "stopped",
      "maintenance"
    ]
  }
};
