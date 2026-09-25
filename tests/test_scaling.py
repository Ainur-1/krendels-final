"""
Масштабирование не меняет смысла: число обработчиков, порядок доставки и сбой с
повторной отправкой дают ту же историю и те же показатели без повторного учёта.
"""

from __future__ import annotations

from zero_defect.simulation.generator import GeneratorConfig, generate
from zero_defect.simulation.load import run_direct, run_outage

CONFIG = GeneratorConfig(items=40, sources=3, seed=11)


def test_generator_is_reproducible():
    first, unique = generate(CONFIG)
    second, _ = generate(CONFIG)
    assert [d.raw for d in first] == [d.raw for d in second]
    assert unique < len(first)


def test_workers_order_and_outage_do_not_change_the_result():
    baseline = run_direct(CONFIG, workers=1)
    runs = [
        run_direct(CONFIG, workers=4),
        run_direct(CONFIG, workers=1, shuffle=True),
        run_outage(CONFIG, workers=2),
    ]
    assert baseline["complete"]
    for run in runs:
        assert run["fingerprint"] == baseline["fingerprint"]
        assert run["complete"]
    outage = runs[-1]
    assert outage["outage"]["refused_batches"] > 0
    assert outage["duplicates"] > baseline["duplicates"]
