"""
Прогоняет все проверочные сценарии и сверяет итог с ожидаемым.

Ожидания лежат отдельно от входных событий, в data/scenarios/<имя>/expected.json.
Правка, которая меняет поведение системы в одной из обязательных ситуаций, падает
здесь и в CI, а не обнаруживается на защите.
"""

from __future__ import annotations

import json
import sys

from zero_defect.config import METRICS_DIR
from zero_defect.simulation.checker import check
from zero_defect.simulation.scenarios import SCENARIOS


def main(argv: list[str]) -> int:
    names = argv or list(SCENARIOS)
    failed = 0
    summary = {}
    for name in names:
        mismatches, observed = check(name)
        summary[name] = {"ok": not mismatches, "mismatches": mismatches}
        mark = "ok" if not mismatches else "РАСХОЖДЕНИЕ"
        print(f"[{mark}] {name} — {SCENARIOS[name][0]}")
        for line in mismatches:
            print(f"    {line}")
        failed += bool(mismatches)
        if "--dump" in sys.argv:
            print(json.dumps(observed, ensure_ascii=False, indent=1, default=str))
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    (METRICS_DIR / "scenarios.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Сценариев: {len(names)}, с расхождениями: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main([arg for arg in sys.argv[1:] if not arg.startswith("--")]))
