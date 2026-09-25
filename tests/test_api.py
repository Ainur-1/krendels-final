"""
Внешняя граница сервиса: подлинность, права ролей, подписанные пакеты источников и
основные представления на демонстрационном наборе.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.helpers import message
from zero_defect.security.auth import sign_body
from zero_defect.serving.api import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from zero_defect.simulation.checker import fresh_settings

    app = create_app(
        fresh_settings(tmp_path_factory.mktemp("api")),
        adapters={},
        load_demo=True,
        autostart=False,
        history_sets=10,
    )
    with TestClient(app) as test_client:
        test_client.app = app
        yield test_client
    app.state.system.close()


def login(client, user_id: str) -> dict:
    token = client.post("/api/auth/demo-login", json={"user_id": user_id}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_everything_but_health_needs_a_token(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/line").status_code == 401
    bad = {"Authorization": "Bearer x.y"}
    assert client.get("/api/line", headers=bad).status_code == 401


def test_views_on_demo_data(client):
    headers = login(client, "master-01")
    line = client.get("/api/line", headers=headers).json()
    assert line["counters"]["quarantine"] == 4
    assert line["source_gaps"]
    cards = client.get("/api/nonconformances", headers=headers).json()
    assert {card["defect_type"] for card in cards} >= {
        "INCLUSION",
        "POROSITY",
        "CRACK",
        "LACK_OF_FUSION",
    }
    item = client.get("/api/items/U-0401", headers=headers).json()
    assert item["status"] == "suspect" and item["components"]
    card = client.get(f"/api/nonconformances/{cards[0]['nc_id']}", headers=headers).json()
    assert card["allowed_actions"] == []
    assert card["signals_detail"][0]["raw"]["event_id"]


def test_missing_evidence_is_not_faked(client):
    headers = login(client, "ctrl-01")
    cards = client.get("/api/nonconformances", headers=headers).json()
    porosity = next(card for card in cards if card["defect_type"] == "POROSITY")
    detail = client.get(f"/api/nonconformances/{porosity['nc_id']}", headers=headers).json()
    assert detail["evidence"] and detail["evidence"][0]["available"] is False


def test_master_cannot_decide_controller_can(client):
    cards = client.get("/api/nonconformances", headers=login(client, "ctrl-01")).json()
    target = next(card for card in cards if card["status"] == "reported")
    url = f"/api/nonconformances/{target['nc_id']}/decisions"
    body = {"action": "start_review", "reason": "беру в работу"}
    assert client.post(url, json=body, headers=login(client, "master-01")).status_code == 403
    response = client.post(url, json=body, headers=login(client, "ctrl-01"))
    assert response.status_code == 200
    assert response.json()["card"]["status"] == "under_review"
    wrong = client.post(
        url, json={"action": "close", "reason": "нельзя"}, headers=login(client, "ctrl-01")
    )
    assert wrong.status_code == 409


def test_signed_batch_from_edge_source(client):
    system = client.app.state.system
    key = system.keyring.register_source("edge-test")
    body = json.dumps({"events": [message("API-1", item_id="API-B1")]}).encode()
    headers = {
        "X-Source-Id": "edge-test",
        "X-Signature": sign_body(key, body),
        "Content-Type": "application/json",
    }
    ok = client.post("/api/events", content=body, headers=headers)
    assert ok.status_code == 200 and ok.json()["results"][0]["status"] == "accepted"
    headers["X-Signature"] = "0" * 64
    assert client.post("/api/events", content=body, headers=headers).status_code == 401


def test_admin_only_security_endpoints(client):
    assert client.get("/api/integrity", headers=login(client, "ctrl-01")).status_code == 403
    report = client.get("/api/integrity", headers=login(client, "admin-01")).json()
    assert report["ok"] is True
    audit = client.get("/api/audit", headers=login(client, "admin-01")).json()
    assert {"denied", "login"} <= {row["action"] for row in audit}


def test_ocel_export(client):
    data = client.get("/api/export/ocel", headers=login(client, "tech-01")).json()
    assert {"objectTypes", "eventTypes", "objects", "events"} <= set(data)
    assert any(
        rel["qualifier"] == "performed_by"
        for event in data["events"]
        for rel in event["relationships"]
    )


def test_openapi_document_is_current():
    import subprocess
    import sys

    from zero_defect.config import PROJECT_ROOT

    result = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
