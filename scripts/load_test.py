"""
Нагрузочный прогон: эталон одним обработчиком, четыре обработчика, перемешанный порядок
и недоступность центра с повторной доставкой. Пишет отчёт в reports/metrics/load.json.

    uv run python scripts/load_test.py --items 300 --sources 6 --seed 7
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zero_defect.config import METRICS_DIR
from zero_defect.simulation.generator import GeneratorConfig
from zero_defect.simulation.load import run_all


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=300)
    parser.add_argument("--sources", type=int, default=6)
    parser.add_argument("--rate", type=float, default=6.0, help="изделий в минуту")
    parser.add_argument("--defects", type=float, default=0.08)
    parser.add_argument("--duplicates", type=float, default=0.05)
    parser.add_argument("--late", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=METRICS_DIR / "load.json")
    args = parser.parse_args()
    config = GeneratorConfig(
        items=args.items,
        sources=args.sources,
        items_per_minute=args.rate,
        defect_rate=args.defects,
        duplicate_rate=args.duplicates,
        late_rate=args.late,
        seed=args.seed,
    )
    report = run_all(config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for run in report["runs"]:
        label = f"обработчиков {run['workers']}" + (", перемешано" if run.get("shuffled") else "")
        label += ", со сбоем" if "outage" in run else ""
        print(
            f"{label:32} сообщений {run['messages_sent']:5}  принято {run['accepted']:5}  "
            f"повторов {run['duplicates']:4}  {run['messages_per_second']:8} сообщ./с  "
            f"p95 пакета {run['batch_p95_ms']} мс"
        )
    print(f"Итог везде одинаков: {report['same_result_everywhere']}")
    print(f"Повторного учёта нет: {report['no_double_counting']}")
    return 0 if report["same_result_everywhere"] and report["no_double_counting"] else 1


if __name__ == "__main__":
    sys.exit(main())
