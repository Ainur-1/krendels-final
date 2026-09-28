"""
Линии как граф: конфигурация, эмулятор, этапы, маршрут изделия, загрузка потока и права.

Главное, что здесь проверяется, — эмулятор ничего не подсказывает представлениям:
дефект, внесённый в этап принудительно, должен найтись разбором как возникший именно на
этом этапе и обнаруженный на следующем контроле, по тем же правилам, что и для
событий настоящей линии.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from zero_defect.config import DATA_DIR
from zero_defect.lines.emulator import Planner, history
from zero_defect.lines.model import LineConfig, default_lines
from zero_defect.lines.views import Filters, build_index, item_path, stage_table
from zero_defect.serving.api import create_app
from zero_defect.serving.lines_routes import LineSpec, spec_to_config
from zero_defect.simulation.checker import check, fresh_settings
from zero_defect.simulation.replay import replay

SPEC = {
    "line_id": "LT",
    "title": "Тестовая линия",
    "product_type_id": "PART-T",
    "takt_min": 5,
    "steps": [
        {"title": "Вход", "kind": "inspection", "duration_min": 1},
        {"title": "Резка", "kind": "operation", "duration_min": 4, "defect_types": ["BURR"]},
        {"title": "Контроль", "kind": "inspection", "duration_min": 1},
        {"title": "Гибка", "kind": "operation", "duration_min": 3, "defect_types": ["CRACK"]},
        {"title": "ОТК", "kind": "inspection", "duration_min": 1},
    ],
}


def test_default_lines_are_valid_graphs():
    for config in default_lines():
        assert config.validate() == []
        assert len(config.order()) == len(config.nodes)
    l1 = default_lines()[0]
    assert [node.node_id for node in l1.sources()] == ["IN-B", "IN-F"]
    assert l1.predecessors("ASM") == ["QC-WELD", "IN-F"]


def test_invalid_graph_is_refused():
    config = spec_to_config(LineSpec(**SPEC))
    config.edges.append(("LT-S05", "LT-S01"))
    assert any("цикл" in problem for problem in config.validate())


def test_config_round_trips_through_json():
    config = default_lines()[0]
    again = LineConfig.from_dict(json.loads(config.to_json()))
    assert again.to_json() == config.to_json()


def test_history_is_reproducible():
    config = default_lines()[1]
    end = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert history(config, 20, end, seed=3) == history(config, 20, end, seed=3)


def test_forced_defect_is_found_where_it_was_made(system):
    config = spec_to_config(LineSpec(**SPEC))
    planner = Planner(config, __import__("random").Random(1), prefix="T")
    start = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    planner.plan_set(start, "001", {"LT-S04": "defect"})
    steps = [
        {"kind": "deliver", "received_at": at.isoformat(), "event": msg}
        for at, msg in planner.events
    ]
    replay(system, sorted(steps, key=lambda step: step["received_at"]))
    state = system.state()
    index = build_index(config, state)
    path = item_path(index, "LT-001-T", state)
    statuses = {node["node_id"]: node["status"] for node in path["route"]}
    assert statuses["LT-S04"] == "defect_origin"
    assert statuses["LT-S05"] == "defect_detected"
    assert statuses["LT-S02"] == "passed"
    rows = {row["node_id"]: row for row in stage_table(index, state, system.settings, Filters())}
    assert rows["LT-S04"]["defects_originated_strong"] == 1
    assert rows["LT-S05"]["first_detections"] == 1


def test_stage_filters_by_item_type(system):
    config = default_lines()[0]
    replay(system, history(config, 12, datetime(2026, 9, 25, 12, 0, tzinfo=UTC), seed=5))
    state = system.state()
    index = build_index(config, state)
    body = {
        row["node_id"]: row
        for row in stage_table(index, state, system.settings, Filters(item_type="BODY-K1"))
    }
    flange = {
        row["node_id"]: row
        for row in stage_table(index, state, system.settings, Filters(item_type="FLANGE-F2"))
    }
    assert body["MILL"]["runs"] > 0 and flange["MILL"]["runs"] == 0
    assert flange["IN-F"]["checks"] > 0 and body["IN-F"]["checks"] == 0


@pytest.fixture()
def api(tmp_path):
    app = create_app(
        fresh_settings(tmp_path), adapters={}, load_demo=True, autostart=False, history_sets=8
    )
    with TestClient(app) as client:
        client.app = app
        yield client


def login(client, user_id: str) -> dict:
    token = client.post("/api/auth/demo-login", json={"user_id": user_id}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_only_main_role_creates_lines(api):
    assert api.post("/api/lines", json=SPEC, headers=login(api, "master")).status_code == 403
    created = api.post("/api/lines", json=SPEC, headers=login(api, "manager"))
    assert created.status_code == 200
    assert api.post("/api/lines", json=SPEC, headers=login(api, "manager")).status_code == 409
    lines = {line["line_id"] for line in api.get("/api/lines", headers=login(api, "master")).json()}
    assert {"L1", "L2", "LT"} <= lines


def test_items_filter_by_columns(api):
    headers = login(api, "technologist")
    everything = api.get("/api/lines/L1/items?limit=1000", headers=headers).json()
    units = api.get("/api/lines/L1/items?item_type=UNIT-U1&limit=1000", headers=headers).json()
    assert units["total"] < everything["total"]
    assert {row["item_type_id"] for row in units["rows"]} == {"UNIT-U1"}
    at_final = api.get("/api/lines/L1/items?stage=FINAL&status=conforming", headers=headers).json()
    assert all(
        row["stage"] == "FINAL" and row["status"] == "conforming" for row in at_final["rows"]
    )
    assert set(everything["facets"]["item_type"]) == {"BODY-K1", "FLANGE-F2", "UNIT-U1"}


def test_flow_upload_runs_to_completion(api):
    flow = json.loads((DATA_DIR / "flows" / "bracket_minute.json").read_text(encoding="utf-8"))
    flow["run"]["items"] = 4
    flow["run"]["defects"] = [{"item": 2, "stage": 4, "kind": "defect"}]
    flow["run"]["duration_s"] = 10
    headers = login(api, "manager")
    started = api.post("/api/flows", json=flow, headers=headers).json()
    assert started["emulation"]["run"]["events_total"] > 0
    emulator = api.app.state.emulator
    now = time.time()
    for step in range(60):
        emulator.tick(now + step)
    # Интерфейс читает последний готовый снимок, свежий строится в фоне. Тест не ждёт
    # фоновую пересборку, а строит состояние сам.
    api.app.state.system.state()
    status = api.get("/api/lines/DEMO/live", headers=headers).json()["emulation"]
    assert status["run"]["finished"] is True
    stages = {
        row["node_id"]: row for row in api.get("/api/lines/DEMO/stages", headers=headers).json()
    }
    assert stages["DEMO-S04"]["defects_originated"] >= 1


def test_flow_with_defect_outside_line_is_refused(api):
    flow = json.loads((DATA_DIR / "flows" / "bracket_minute.json").read_text(encoding="utf-8"))
    flow["run"]["defects"] = [{"item": 1, "stage": 99}]
    assert api.post("/api/flows", json=flow, headers=login(api, "manager")).status_code == 422


def test_economics_uses_parameters_and_measurements(api):
    headers = login(api, "manager")
    before = api.get("/api/lines/L1/economics", headers=headers).json()
    assert before["measured"]["products"] > 0
    api.put(
        "/api/lines/L1/economics", json={"scrap_cost_rub": 0, "rework_cost_rub": 0}, headers=headers
    )
    after = api.get("/api/lines/L1/economics", headers=headers).json()
    assert after["computed"]["scrap_cost_rub"] == 0 and after["computed"]["rework_cost_rub"] == 0
    assert (
        api.put("/api/lines/L1/economics", json={}, headers=login(api, "controller")).status_code
        == 403
    )


def test_password_login(api):
    system = api.app.state.system
    demo = {"user_id": "administrator", "password": "administrator"}
    assert api.post("/api/auth/login", json=demo).json()["user"]["role"] == "admin"
    assert (
        api.post("/api/auth/login", json={"user_id": "controller", "password": "x"}).status_code
        == 401
    )
    system.users.set_password("controller", "длинный-пароль-1")
    ok = api.post("/api/auth/login", json={"user_id": "controller", "password": "длинный-пароль-1"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "controller"
    assert (
        api.post(
            "/api/auth/login", json={"user_id": "controller", "password": "не тот"}
        ).status_code
        == 401
    )


def test_admin_can_list_users(api):
    users = api.get("/api/admin/users", headers=login(api, "administrator")).json()
    assert {user["role"] for user in users} >= {"controller", "manager", "admin"}
    assert api.get("/api/admin/users", headers=login(api, "manager")).status_code == 403


@pytest.mark.parametrize(
    "name", ["03_defect_after_operation", "07_controller_decision", "08_integrity_tamper"]
)
def test_scenarios_on_postgres(name, database_url, monkeypatch):
    if not database_url.startswith("postgresql"):
        pytest.skip("этот тест — только для PostgreSQL; SQLite проверяет test_scenarios")
    import zero_defect.simulation.checker as checker

    original = checker.fresh_settings
    monkeypatch.setattr(
        checker, "fresh_settings", lambda root, **kw: original(root, storage_url=database_url, **kw)
    )
    mismatches, _ = check(name)
    assert mismatches == []


GRAPH = {
    "line_id": "LG",
    "title": "Сборка из блоков",
    "product_type_id": "ASM-G",
    "takt_min": 5,
    "nodes": [
        {
            "node_id": "A",
            "title": "Вход корпуса",
            "kind": "inspection",
            "x": 0,
            "y": 0,
            "duration_min": 1,
            "item_type_id": "BODY-G",
        },
        {
            "node_id": "B",
            "title": "Вход крышки",
            "kind": "inspection",
            "x": 0,
            "y": 200,
            "duration_min": 1,
            "item_type_id": "LID-G",
            "origin": "purchased",
        },
        {
            "node_id": "C",
            "title": "Сборка",
            "kind": "operation",
            "x": 300,
            "y": 100,
            "duration_min": 4,
            "equipment_id": "ASM-TOOL-01",
            "processing": "запрессовка",
            "defect_types": ["DENT"],
        },
        {
            "node_id": "D",
            "title": "ОТК",
            "kind": "inspection",
            "x": 600,
            "y": 100,
            "duration_min": 2,
        },
    ],
    "edges": [["A", "C"], ["B", "C"], ["C", "D"]],
}


def test_graph_from_blocks_builds_an_assembly(api):
    headers = login(api, "manager")
    assert api.post("/api/lines/graph", json=GRAPH, headers=headers).status_code == 200
    config = api.get("/api/lines/LG", headers=headers).json()
    nodes = {node["node_id"]: node for node in config["nodes"]}
    assert nodes["C"]["assembly"] is True and nodes["C"]["output_type"] == "ASM-G"
    assert nodes["A"]["checkpoint_kind"] == "incoming" and nodes["D"]["checkpoint_kind"] == "final"
    assert nodes["C"]["machine_type"] == "assembly" and nodes["C"]["processing"] == "запрессовка"
    cyclic = {**GRAPH, "edges": GRAPH["edges"] + [["D", "A"]]}
    assert api.put("/api/lines/LG/graph", json=cyclic, headers=headers).status_code == 422


def test_editing_existing_line_keeps_its_history(api):
    headers = login(api, "manager")
    config = api.get("/api/lines/L1", headers=headers).json()
    before = {
        row["node_id"]: row["items"]
        for row in api.get("/api/lines/L1/stages", headers=headers).json()
    }
    keep = ("node_id", "title", "kind", "x", "y", "defect_types", "rework_types", "equipment_id")
    keep += ("processing", "operators", "checkpoint_kind", "item_type_id", "origin", "output_type")
    keep += ("station_id", "operation_id", "checkpoint_id")
    nodes = [
        {**{key: node[key] for key in keep}, "duration_min": node["duration_s"] / 60}
        for node in config["nodes"]
    ]
    nodes = [
        dict(node, equipment_id="CNC-02") if node["node_id"] == "MILL" else node for node in nodes
    ]
    spec = {
        "line_id": "L1",
        "title": config["title"],
        "product_type_id": config["product_type_id"],
        "takt_min": config["takt_s"] / 60,
        "nodes": nodes,
        "edges": config["edges"],
        "economics": config["economics"],
    }
    assert api.put("/api/lines/L1/graph", json=spec, headers=headers).status_code == 200
    after = {
        row["node_id"]: row["items"]
        for row in api.get("/api/lines/L1/stages", headers=headers).json()
    }
    assert after == before and after["WELD"] > 0


def _with_operation(**changes) -> dict:
    nodes = [dict(node, **changes) if node["node_id"] == "C" else node for node in GRAPH["nodes"]]
    return {**GRAPH, "line_id": "LG2", "nodes": nodes}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"equipment_id": "NEW-99"}, "не зарегистрирован"),
        ({"processing": "фрезерование"}, "не выполняет"),
        ({"defect_types": ["POROSITY"]}, "не относятся"),
    ],
)
def test_operation_uses_only_registered_equipment(api, changes, reason):
    response = api.post(
        "/api/lines/graph", json=_with_operation(**changes), headers=login(api, "manager")
    )
    assert response.status_code == 422 and reason in response.json()["detail"]


def test_technologist_registers_machine_and_manager_picks_it(api):
    spec = {
        "equipment_id": "WELD-09",
        "title": "Аргонодуговая установка №9",
        "machine_type": "welder",
        "parameters": {"current_a": {"low": 150, "high": 180}},
    }
    manager = login(api, "manager")
    assert api.post("/api/equipment", json=spec, headers=manager).status_code == 403
    tech = login(api, "technologist")
    created = api.post("/api/equipment", json=spec, headers=tech).json()
    assert created["defect_types"] == ["POROSITY", "LACK_OF_FUSION", "CRACK"]
    assert created["parameters"]["current_a"]["high"] == 180
    assert api.post("/api/equipment", json=spec, headers=tech).status_code == 422
    bad = {**spec, "parameters": {"current_a": {"low": 200, "high": 180}}}
    assert api.put("/api/equipment/WELD-09", json=bad, headers=tech).status_code == 422
    catalog = api.get("/api/catalog", headers=manager).json()
    assert "WELD-09" in {machine["equipment_id"] for machine in catalog["equipment"]}
    assert "аргонодуговая сварка" in catalog["machine_types"]["welder"]["processing"]
    graph = _with_operation(
        equipment_id="WELD-09", processing="прихватка", defect_types=["POROSITY"]
    )
    assert api.post("/api/lines/graph", json=graph, headers=manager).status_code == 200


def test_admin_describes_defect_and_machine_type_templates(api):
    admin = login(api, "administrator")
    defect = {
        "code": "DELAMINATION_X",
        "title": "Отслоение покрытия",
        "method": "камера, косой свет",
    }
    assert (
        api.post("/api/admin/defects", json=defect, headers=login(api, "technologist")).status_code
        == 403
    )
    assert api.post("/api/admin/defects", json=defect, headers=admin).status_code == 200
    kind = {
        "key": "plasma_spray",
        "title": "Установка плазменного напыления",
        "processing": ["напыление"],
        "defect_types": ["DELAMINATION_X", "SAG"],
        "parameters": {
            "arc_power_kw": {"title": "Мощность дуги", "unit": "кВт", "low": 30, "high": 45}
        },
    }
    assert api.post("/api/admin/machine-types", json=kind, headers=admin).status_code == 200
    broken = {**kind, "key": "plasma_bad", "defect_types": ["NO_SUCH"]}
    assert api.post("/api/admin/machine-types", json=broken, headers=admin).status_code == 422
    machine = {"equipment_id": "PLASMA-01", "title": "Напыление №1", "machine_type": "plasma_spray"}
    made = api.post("/api/equipment", json=machine, headers=login(api, "technologist")).json()
    assert made["defect_types"] == ["DELAMINATION_X", "SAG"]
    assert made["parameters"]["arc_power_kw"]["unit"] == "кВт"


def test_admin_creates_role_and_user_who_can_log_in(api):
    admin = login(api, "administrator")
    role = {
        "code": "auditor",
        "title": "Аудитор",
        "screen": "manager",
        "permissions": ["read", "export"],
    }
    assert api.post("/api/admin/roles", json=role, headers=admin).status_code == 200
    user = {
        "user_id": "auditor1",
        "name": "Аудитор 1",
        "role": "auditor",
        "password": "длинный-пароль",
    }
    assert api.post("/api/admin/users", json=user, headers=admin).status_code == 200
    token = api.post(
        "/api/auth/login", json={"user_id": "auditor1", "password": "длинный-пароль"}
    ).json()["token"]
    me = api.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["screen"] == "manager" and "export" in me["permissions"]
    lockout = {
        "code": "admin",
        "title": "Администратор",
        "screen": "admin",
        "permissions": ["read"],
    }
    assert api.put("/api/admin/roles/admin", json=lockout, headers=admin).status_code == 422


def test_stage_shows_machine_readings_with_tolerances(api):
    data = api.get("/api/lines/L1/stages/WELD", headers=login(api, "technologist")).json()
    machine = data["machine"]
    assert machine["machine_type"] == "welder" and "current_a" in machine["parameters"]
    limits = machine["parameters"]

    def outside(event: dict) -> bool:
        return any(
            not limits[key]["low"] <= value <= limits[key]["high"]
            for key, value in event["parameters"].items()
            if key in limits
        )

    readings = [event for event in machine["events"] if event["parameters"]]
    assert readings, "эмулятор пишет показания станка"
    assert not any(outside(event) for event in readings if event["state"] == "running")
    assert all(outside(event) for event in readings if event["state"] == "deviation")


def test_configured_run_on_existing_line(api):
    headers = login(api, "master")
    body = {
        "items": 3,
        "duration_s": 10,
        "seed": 2,
        "defect_rates_pct": {"L2-BEND": 0},
        "defects": [{"item": 2, "node_id": "L2-BEND", "kind": "defect"}],
    }
    started = api.post("/api/lines/L2/runs", json=body, headers=headers).json()
    assert started["emulation"]["run"]["items"] == 3
    bad = {**body, "defects": [{"item": 9, "node_id": "L2-BEND"}]}
    assert api.post("/api/lines/L2/runs", json=bad, headers=headers).status_code == 422
    # Пульт доступен всем ролям линии, но не без входа.
    assert api.post("/api/lines/L2/runs", json=body).status_code == 401


def test_state_at_moment_hides_the_future(api):
    headers = login(api, "controller")
    timeline = api.get("/api/lines/L1/timeline", headers=headers).json()
    detected = [m for m in timeline["markers"] if m["kind"] == "problem"]
    assert detected, "в демонстрационной базе должны быть обнаружения"
    first = min(detected, key=lambda m: m["at"])
    before = datetime.fromisoformat(first["at"]).timestamp() - 1
    moment = datetime.fromtimestamp(before, UTC).isoformat()
    past = api.get("/api/lines/L1/problems", params={"at": moment}, headers=headers).json()
    assert first["problem_id"] not in {p["problem_id"] for p in past["active"]}
    live = api.get("/api/lines/L1/live", params={"at": moment}, headers=headers).json()
    assert live["latest_event_at"] <= first["at"]


def test_problem_resolved_later_is_resolved_in_the_past_too(api):
    headers = login(api, "controller")
    now = api.get("/api/lines/L1/problems", headers=headers).json()
    resolved = now["resolved"]
    assert resolved, "в демонстрационной базе есть решённые проблемы"
    problem = resolved[-1]
    moment = datetime.fromisoformat(problem["resolved_at"]).timestamp() - 1
    at = datetime.fromtimestamp(moment, UTC).isoformat()
    past = api.get("/api/lines/L1/problems", params={"at": at}, headers=headers).json()
    # Решённость — на настоящее время: за секунду до решения проблема уже в решённых.
    assert problem["problem_id"] not in {p["problem_id"] for p in past["active"]}
    assert problem["problem_id"] in {p["problem_id"] for p in past["resolved"]}
    live = api.get("/api/lines/L1/live", params={"at": at}, headers=headers).json()
    circles = sum(
        n["problems"]["nonconformance"] + n["problems"]["not_assessable"] for n in live["nodes"]
    )
    assert circles == len(past["active"]), "круг над этапом — все действующие проблемы"
    for node in live["nodes"]:
        counts = node["counts"]
        assert sum(counts.values()) == node["passed"]
        # Шарики «проблема» и «оценка невозможна» — изделия, круг — проблемы: изделий не больше.
        assert counts.get("problem", 0) <= node["problems"]["nonconformance"]
        assert counts.get("not_assessable", 0) <= node["problems"]["not_assessable"]
    assert all(p["kind"] in {"nonconformance", "not_assessable"} for p in now["active"])


def test_not_assessable_is_a_problem_but_not_a_nonconformance(api):
    headers = login(api, "controller")
    data = api.get("/api/lines/L1/problems", headers=headers).json()
    unclear = [p for p in data["active"] + data["resolved"] if p["kind"] == "not_assessable"]
    assert unclear, "эмулятор даёт недостоверные наблюдения"
    cards = {c["nc_id"] for c in api.get("/api/nonconformances", headers=headers).json()}
    assert not {p["problem_id"] for p in unclear} & cards


def test_item_visits_separate_the_problem_pass_from_the_repeat(api):
    headers = login(api, "controller")
    data = api.get("/api/lines/L1/problems", headers=headers).json()
    nc = next(p for p in data["resolved"] if p["kind"] == "nonconformance")
    path = api.get(f"/api/items/{nc['item_id']}/path", headers=headers).json()
    labels = [visit["label"] for visit in path["visits"]]
    assert "defect" in labels
    assert labels.index("defect") < len(labels) - 1, "после дефекта есть повторный проход"
    linked = {
        problem
        for visit in path["visits"]
        if visit["label"] == "defect"
        for problem in visit["problem_ids"]
    }
    assert nc["problem_id"] in linked, "шаг с дефектом ведёт к своей проблеме"


def test_plant_overview_lists_lines_and_products(api):
    data = api.get("/api/plant", headers=login(api, "master")).json()
    assert {line["line_id"] for line in data["lines"]} >= {"L1", "L2"}
    products = {entry["item_type"]: entry["lines"] for entry in data["products"]}
    assert products["UNIT-U1"] == ["L1"]


def test_live_view_counts_every_item_that_passed_a_stage(api):
    live = api.get("/api/lines/L1/live", headers=login(api, "master")).json()
    for node in live["nodes"]:
        assert sum(node["counts"].values()) == node["passed"]
    assert max(node["passed"] for node in live["nodes"]) > 0


def test_stage_counts_follow_the_range(api):
    headers = login(api, "master")
    whole = api.get("/api/lines/L1/live", headers=headers).json()
    end = api.get("/api/lines/L1/timeline", headers=headers).json()["end"]
    since = (datetime.fromisoformat(end) - timedelta(minutes=5)).isoformat()
    recent = api.get("/api/lines/L1/live", params={"since": since}, headers=headers).json()
    passed = {n["node_id"]: n["passed"] for n in whole["nodes"]}
    assert all(n["passed"] <= passed[n["node_id"]] for n in recent["nodes"])
    assert sum(n["passed"] for n in recent["nodes"]) < sum(passed.values())


def test_machine_problem_is_an_episode_until_the_machine_runs_again(api):
    headers = login(api, "technologist")
    data = api.get("/api/lines/L1/problems", params={"lane": "machines"}, headers=headers).json()
    episodes = data["active"] + data["resolved"]
    assert episodes, "эмулятор даёт сбои станков"
    assert all(p["kind"] == "machine" and p["equipment_id"] for p in episodes)
    assert all(p["resolved_at"] > p["at"] for p in data["resolved"])
    live = api.get("/api/lines/L1/live", headers=headers).json()
    assert sum(n["problems"]["machine"] for n in live["nodes"]) == len(data["active"])
    marks = api.get("/api/lines/L1/timeline", headers=headers).json()["markers"]
    machine = [m for m in marks if m["lane"] == "machines"]
    assert {m["kind"] for m in machine} <= {"problem", "resolved"}
    assert all(m["problem_id"].startswith("MF-") for m in machine)


def test_stage_problem_list_has_active_and_resolved_in_range(api):
    headers = login(api, "controller")
    data = api.get("/api/lines/L1/problems", headers=headers).json()
    problem = data["resolved"][0]
    node = problem["node_id"]
    here = api.get("/api/lines/L1/problems", params={"node": node}, headers=headers).json()
    assert all(p["node_id"] == node for p in here["active"] + here["resolved"])
    since = problem["resolved_at"]
    later = api.get(
        "/api/lines/L1/problems", params={"node": node, "since": since}, headers=headers
    ).json()
    assert all(p["resolved_at"] >= since for p in later["resolved"])
    mine = api.get("/api/lines/L1/problems", params={"item": problem["item_id"]}, headers=headers)
    assert problem["problem_id"] in {p["problem_id"] for p in mine.json()["resolved"]}


def test_admin_sees_database_without_secrets(api):
    headers = login(api, "administrator")
    db = api.get("/api/admin/db", headers=headers).json()
    ledger = next(table for table in db["tables"] if table["table"] == "ledger")
    assert "ciphertext" not in ledger["columns"] and "ciphertext" in ledger["hidden"]
    users = api.get("/api/admin/db", params={"table": "users"}, headers=headers).json()
    assert all("password_hash" not in row for row in users["rows"])
    roles = api.get("/api/admin/roles", headers=headers).json()
    assert "decide" in roles["roles"]["controller"]["permissions"]
    assert "decide" in roles["roles"]["master"]["permissions"]
    assert "decide" not in roles["roles"]["technologist"]["permissions"]
    assert any(
        row["code"] == "POROSITY" for row in api.get("/api/admin/defects", headers=headers).json()
    )
    assert any(
        row["equipment_id"] == "WELD-01"
        for row in api.get("/api/admin/equipment", headers=headers).json()
    )
    assert api.get("/api/admin/db", headers=login(api, "manager")).status_code == 403


def test_emulator_console_page_is_served(api):
    page = api.get("/emulator")
    assert page.status_code == 200 and "Пульт эмулятора" in page.text


def _drain(api) -> None:
    emulator = api.app.state.emulator
    now = time.time()
    for step in range(0, 7200, 30):
        emulator.tick(now + step)
    api.app.state.system.state()


def test_master_decides_on_not_assessable(api):
    master = login(api, "master")
    open_ones = [
        p
        for p in api.get("/api/lines/L1/problems", headers=master).json()["active"]
        if p["kind"] == "not_assessable"
    ]
    assert open_ones, "в демонстрационной базе есть «оценка невозможна»"
    first = open_ones[0]
    body = {"action": "accept_manual", "reason": "ручной осмотр, признаков нет"}
    url = f"/api/unassessable/{first['problem_id']}/decisions"
    assert api.post(url, json=body, headers=login(api, "technologist")).status_code == 403
    assert api.post(url, json={**body, "reason": " "}, headers=master).status_code == 409
    assert api.post(url, json=body, headers=master).status_code == 200
    assert api.post(url, json=body, headers=master).status_code == 409, "уже решена"
    api.app.state.system.state()
    done = {
        p["problem_id"]: p
        for p in api.get("/api/lines/L1/problems", headers=master).json()["resolved"]
    }
    assert done[first["problem_id"]]["resolution"] == "accepted_manual"
    assert done[first["problem_id"]]["decisions"][0]["author_id"] == "master"


def test_unclear_item_waits_and_goes_on_after_recheck(api):
    config = api.app.state.lines.get("L2")
    entry = config.node("L2-IN")
    moment = datetime.now(UTC) - timedelta(minutes=1)
    common = {"schema_version": "1.0", "line_id": "L2", "item_id": "L2-NA1-B2"}
    api.app.state.system.ingest(
        [
            {
                **common,
                "event_id": "NA-TEST-1",
                "event_type": "item_registered",
                "occurred_at": moment.isoformat(),
                "source_id": "mes-test",
                "item_type_id": config.product_type_id,
            },
            {
                **common,
                "event_id": "NA-TEST-2",
                "event_type": "inspection_reported",
                "occurred_at": (moment + timedelta(seconds=20)).isoformat(),
                "source_id": "vision-test",
                "item_type_id": config.product_type_id,
                "station_id": entry.station_id,
                "checkpoint_id": entry.checkpoint_id,
                "checkpoint_kind": entry.checkpoint_kind,
                "inspection_result": "not_assessable",
                "observation_quality": "poor",
                "confidence": 0.2,
            },
        ]
    )
    api.app.state.system.state()
    master = login(api, "master")
    path = api.get("/api/items/L2-NA1-B2/path", headers=master).json()
    assert path["status"] == "not_assessable", "плохой снимок — ни годно, ни брак"
    body = {"action": "request_recheck", "reason": "снимок пересвечен"}
    url = "/api/unassessable/NA-NA-TEST-2/decisions"
    assert api.post(url, json=body, headers=master).status_code == 200
    _drain(api)
    after = api.get("/api/items/L2-NA1-B2/path", headers=master).json()
    visits = [(v["node_id"], v["label"]) for v in after["visits"] if v["at"]]
    assert visits[:2] == [("L2-IN", "not_assessable"), ("L2-IN", "passed")], visits
    assert len(visits) > 2, "после повторного контроля изделие пошло дальше по линии"
    problem = api.get("/api/lines/L2/problems", params={"problem": "NA-NA-TEST-2"}, headers=master)
    assert problem.json()["resolved"][0]["resolution"] == "rechecked"


def test_confirmed_defect_is_reworked_and_the_item_goes_on(api):
    master, controller = login(api, "master"), login(api, "controller")
    body = {
        "items": 3,
        "duration_s": 10,
        "seed": 2,
        "defect_rates_pct": {"L2-BEND": 0},
        "defects": [{"item": 2, "node_id": "L2-BEND", "kind": "defect"}],
    }
    assert api.post("/api/lines/L2/runs", json=body, headers=master).status_code == 200
    _drain(api)
    held = [
        p
        for p in api.get("/api/lines/L2/problems", headers=controller).json()["active"]
        if p["kind"] == "nonconformance" and p["node_id"] == "L2-QC" and "-002-" in p["item_id"]
    ]
    assert held, "дефект гибки обнаружен на контроле геометрии, изделие ждёт решения"
    problem = held[0]
    before = api.get(f"/api/items/{problem['item_id']}/path", headers=controller).json()
    assert "rework" not in [visit["label"] for visit in before["visits"]]
    decided = api.post(
        f"/api/nonconformances/{problem['problem_id']}/decisions",
        json={"action": "confirm", "reason": "трещина подтверждена осмотром"},
        headers=controller,
    )
    assert decided.status_code == 200
    _drain(api)
    after = api.get(f"/api/items/{problem['item_id']}/path", headers=controller).json()
    labels = [visit["label"] for visit in after["visits"]]
    assert "rework" in labels, "после подтверждения — доработка на гибке"
    assert labels.index("rework") < len(labels) - 1, "и путь дальше по линии"


def test_emulation_starts_new_items_only_while_someone_watches():
    from zero_defect.lines.emulator import LiveEmulator

    config = default_lines()[1]
    delivered: list[dict] = []
    emulator = LiveEmulator(delivered.extend, lambda line_id: config)
    emulator.start(config.line_id)
    later = time.time() + LiveEmulator.WATCH_S + 60
    emulator.tick(later)
    assert emulator.status(config.line_id)["sets_started"] == 0, "без зрителя новых изделий нет"
    emulator.watch(config.line_id)
    emulator.tick(time.time())
    assert emulator.status(config.line_id)["sets_started"] == 1
    emulator.shutdown()


def test_emulation_stops_at_the_event_ceiling_even_while_watched():
    from zero_defect.lines.emulator import LiveEmulator

    config = default_lines()[1]

    def sets_after_a_minute(ceiling: int) -> int:
        emulator = LiveEmulator(lambda events: None, lambda line_id: config)
        emulator.MAX_EVENTS = ceiling
        emulator.start(config.line_id)
        start = time.time()
        # Зритель смотрит всё время: каждую секунду экран линии опрашивает сервис.
        for step in range(60):
            emulator._watched[config.line_id] = start + step
            emulator.tick(start + step)
        emulator.shutdown()
        return emulator.status(config.line_id)["sets_started"]

    unlimited = sets_after_a_minute(10**9)
    capped = sets_after_a_minute(1)
    assert unlimited > 2, "без потолка линия запускает комплект за комплектом"
    assert capped < unlimited, "с потолком новые комплекты не запускаются"
