"""
Интеграции: двусторонний обмен с эмулятором и преобразования форматов 1С, Галактики,
MES по ISA-95 и КОМПАС-3D на примерах сообщений из contracts/examples/.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from zero_defect.config import CONTRACTS_DIR, AdapterSettings
from zero_defect.integration import emulator
from zero_defect.integration.adapters import galaktika, kompas, mes_isa95, onec
from zero_defect.integration.adapters.mes_emulator import MesEmulatorAdapter
from zero_defect.integration.hub import IntegrationHub, Outbox
from zero_defect.integration.model import IntegrationError, QualityResult
from zero_defect.simulation.line import Builder, standard_unit
from zero_defect.simulation.replay import replay

EXAMPLES = CONTRACTS_DIR / "examples"


@pytest.fixture()
def hub(system, tmp_path):
    emulator.reset()
    client = TestClient(emulator.app)
    adapter = MesEmulatorAdapter(AdapterSettings(), client=client)
    instance = IntegrationHub(
        system, {"mes_emulator": adapter}, Outbox(tmp_path / "integration.sqlite3")
    )
    yield instance
    emulator.reset()


def produce_unit(system, number: str) -> None:
    builder = Builder(clock=datetime(2026, 9, 26, 9, 0, tzinfo=UTC), prefix=f"T{number}")
    standard_unit(builder, number)
    replay(system, builder.steps)


def test_two_way_exchange_with_emulator(system, hub):
    pulled = hub.pull("mes_emulator")
    assert pulled == {"orders": 2, "registered": 6, "already_known": 0}
    produce_unit(system, "9001")
    report = hub.sync("mes_emulator")["mes_emulator"]
    assert report["queued"] == 1
    assert report["push"]["delivered"] == 1
    received = emulator.received()["reports"]
    assert [entry["report"]["serial"] for entry in received] == ["U-9001"]
    assert received[0]["report"]["verdict"] == "OK"
    assert system.exchanges[-1]["status"] == "delivered"


def test_repeated_sync_does_not_resend(system, hub):
    hub.pull("mes_emulator")
    produce_unit(system, "9001")
    hub.sync("mes_emulator")
    again = hub.sync("mes_emulator")["mes_emulator"]
    assert again["pull"]["already_known"] == 6
    assert again["queued"] == 0
    assert len(emulator.received()["reports"]) == 1


def test_outage_keeps_message_in_queue_until_recovery(system, hub):
    hub.pull("mes_emulator")
    produce_unit(system, "9001")
    hub.enqueue_results("mes_emulator")
    emulator.set_faults(emulator.Faults(fail_next=1, status=503))
    moment = datetime.now(UTC)
    first = hub.flush("mes_emulator", moment)
    assert first == {"delivered": 0, "retry_later": 1, "dead_letter": 0}
    assert hub.flush("mes_emulator", moment)["delivered"] == 0
    second = hub.flush("mes_emulator", moment + timedelta(seconds=10))
    assert second["delivered"] == 1
    assert hub.outbox.rows()[0]["attempts"] == 2


def test_content_error_goes_to_dead_letter(hub):
    result = QualityResult("QR-X", "WO-X", "NOT-A-SERIAL", "UNIT-U1", "conforming")
    hub.outbox.enqueue("mes_emulator", result)
    assert hub.flush("mes_emulator")["dead_letter"] == 1
    row = hub.outbox.rows()[0]
    assert row["status"] == "dead_letter" and row["last_error"].startswith("unknown_serial")


def test_emulator_is_idempotent_on_message_id():
    emulator.reset()
    client = TestClient(emulator.app)
    body = {"msgId": "M-1", "serial": "U-9001", "verdict": "OK"}
    assert client.post("/api/v1/quality-reports", json=body).json()["status"] == "accepted"
    assert client.post("/api/v1/quality-reports", json=body).json()["status"] == "duplicate"
    emulator.reset()


def test_onec_mapping_round_trip():
    reference, keys = onec.nomenclature_to_internal(
        json.loads((EXAMPLES / "onec_nomenclature.json").read_text(encoding="utf-8"))
    )
    assert reference.item_types["UNIT-U1"] == "Узел крепления в сборе"
    orders = json.loads((EXAMPLES / "onec_orders.json").read_text(encoding="utf-8"))["value"]
    order = onec.order_to_internal(orders[0], keys)
    assert order.work_order_id == "ЗП-000123" and order.items[0].item_type_id == "UNIT-U1"
    outbound = onec.result_to_external(
        QualityResult("QR-1", order.work_order_id, "U-1C-0001", "UNIT-U1", "nonconforming"), keys
    )
    assert outbound["Номенклатура_Key"] == "5f1c8a2e-0b8e-11ef-9a41-00155d010a01"
    assert outbound["Результат"] == "Брак"


def test_onec_unmapped_nomenclature_is_a_content_error():
    with pytest.raises(IntegrationError) as error:
        onec.order_to_internal(
            {"Number": "1", "Продукция": [{"Номенклатура_Key": "?", "СерийныйНомер": "S"}]}, {}
        )
    assert error.value.retryable is False


def test_galaktika_files(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "orders.xml").write_text(
        (EXAMPLES / "galaktika_orders.xml").read_text(encoding="utf-8")
    )
    (inbox / "nomenclature.xml").write_text(
        (EXAMPLES / "galaktika_nomenclature.xml").read_text(encoding="utf-8")
    )
    adapter = galaktika.GalaktikaFileAdapter(tmp_path)
    order = adapter.fetch_work_orders()[0]
    assert [item.parent_id for item in order.items] == [None, "U-GAL-0001", "U-GAL-0001"]
    assert "BODY-K1" in adapter.fetch_reference().item_types
    ack = adapter.send_result(QualityResult("QR-G", "ПЗ-77", "U-GAL-0001", "UNIT-U1", "conforming"))
    written = (tmp_path / "outbox" / "QR-G.xml").read_text(encoding="utf-8")
    assert ack.accepted and 'verdict="ACCEPT"' in written
    with pytest.raises(IntegrationError):
        galaktika.parse_orders("<broken")


def test_isa95_schedule_and_performance():
    schedule = json.loads((EXAMPLES / "isa95_operations_schedule.json").read_text(encoding="utf-8"))
    order = mes_isa95.schedule_to_internal(schedule)[0]
    origins = {item.item_id: item.origin for item in order.items}
    assert origins == {
        "U-MES-0501": "manufactured",
        "B-MES-0501": "manufactured",
        "F-MES-0501": "purchased",
    }
    nc = {"nc_id": "NC-1", "defect_type": "CRACK", "status": "confirmed"}
    performance = mes_isa95.result_to_performance(
        QualityResult("QR-M", "OR-501", "U-MES-0501", "UNIT-U1", "nonconforming", (nc,))
    )
    actual = performance["OperationsPerformance"]["OperationsResponse"][0]["SegmentResponse"][0]
    assert actual["MaterialActual"][0]["Disposition"] == "Fail"


def test_kompas_assembly_file(tmp_path):
    assembly = kompas.load_assembly(EXAMPLES / "kompas_assembly.json")
    assert assembly.geometry is None
    assert {c.item_type_id for c in assembly.components} == {"BODY-K1", "FLANGE-F2"}
    bad = json.loads((EXAMPLES / "kompas_assembly.json").read_text(encoding="utf-8"))
    del bad["geometry"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(IntegrationError):
        kompas.load_assembly(path)


def test_kompas_com_explains_itself_off_windows():
    with pytest.raises(IntegrationError) as error:
        kompas.KompasComSource().load("C:/assembly.a3d")
    assert "Windows" in str(error.value)


def test_third_party_adapter_plugs_in_through_config(tmp_path):
    from zero_defect.integration.registry import resolve

    config = tmp_path / "zero_defect.toml"
    config.write_text(
        "[integration.custom]\n"
        'factory = "zero_defect.integration.adapters.mes_emulator:MesEmulatorAdapter"\n',
        encoding="utf-8",
    )
    assert resolve("custom", config) is MesEmulatorAdapter
