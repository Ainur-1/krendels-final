"""
Отправляет события в работающий сервис так, как это делал бы edge-агент: пакетами, с
подписью HMAC и с буфером на случай недоступности.

    uv run python scripts/replay.py --scenario 03_defect_after_operation
    uv run python scripts/replay.py --unit 9001      # маршрут узла из задания эмулятора

Ключ источника выдаётся при первом запуске и хранится в хранилище ключей сервиса.
Решения контролёра и контрольное изменение записи из сценария этим скриптом не
отправляются — их делают люди через интерфейс и scripts/tamper.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime

import httpx

from zero_defect.config import SCENARIOS_DIR, load_settings
from zero_defect.ledger.crypto import Keyring
from zero_defect.simulation.edge import EdgeAgent
from zero_defect.simulation.line import Builder, standard_unit
from zero_defect.simulation.replay import load_steps

SOURCE_ID = "edge-replay"


def messages_for(args: argparse.Namespace) -> list[dict]:
    if args.unit:
        builder = Builder(clock=datetime.now(UTC), prefix=f"LIVE{args.unit}")
        standard_unit(builder, args.unit)
        return [step["event"] for step in builder.steps]
    steps = load_steps(SCENARIOS_DIR / args.scenario / "input.jsonl")
    return [step["event"] for step in steps if step["kind"] == "deliver"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario")
    group.add_argument("--unit", help="номер узла из задания, например 9001")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--pause", type=float, default=0.0, help="пауза между пакетами, с")
    args = parser.parse_args()

    keyring = Keyring(load_settings().keys_dir)
    key = keyring.source_keys().get(SOURCE_ID) or keyring.register_source(SOURCE_ID)
    client = httpx.Client(base_url=args.url, timeout=10, trust_env=False)

    def send(body: bytes, headers: dict[str, str]) -> int:
        response = client.post("/api/events", content=body, headers=headers)
        if response.status_code < 300:
            statuses = [result["status"] for result in response.json()["results"]]
            print(
                f"пакет: {len(statuses)} сообщений, "
                + ", ".join(
                    f"{status} {statuses.count(status)}" for status in sorted(set(statuses))
                )
            )
        else:
            print(f"пакет не принят: {response.status_code} {response.text[:200]}")
        return response.status_code

    agent = EdgeAgent(SOURCE_ID, key, send, batch_size=args.batch)
    for message in messages_for(args):
        agent.submit([message])
        if len(agent.buffer) >= args.batch:
            agent.flush()
            time.sleep(args.pause)
    agent.flush()
    if agent.buffer:
        print(f"не доставлено: {len(agent.buffer)} сообщений остались в буфере агента")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
