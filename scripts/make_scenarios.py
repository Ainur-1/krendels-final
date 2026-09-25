"""Записывает входы проверочных сценариев и демонстрационного набора в data/."""

from __future__ import annotations

from zero_defect.config import DATA_DIR, SCENARIOS_DIR
from zero_defect.simulation.replay import write_steps
from zero_defect.simulation.scenarios import SCENARIOS, build, build_demo


def main() -> None:
    for name in SCENARIOS:
        path = SCENARIOS_DIR / name / "input.jsonl"
        write_steps(path, build(name))
        print(path.relative_to(DATA_DIR.parent))
    demo = DATA_DIR / "demo" / "input.jsonl"
    write_steps(demo, build_demo())
    print(demo.relative_to(DATA_DIR.parent))


if __name__ == "__main__":
    main()
