"""
Администрирование: справочники, роли, пользователи, база данных и оборудование.

Администратор описывает шаблоны: виды дефектов (как дефект оценивается) и типы станков
(какие данные станок передаёт, какие дефекты на нём возникают). Технолог по шаблону
заводит конкретный станок. Роли и пользователи заводятся здесь же.

Просмотр базы — только чтение и только то, что безопасно показать: у журнала — открытые
заголовки записей без шифротекста, у пользователей — признак заданного пароля без
хеша. Всё, что меняет данные, делается через ядро с аудитом, а не отсюда.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from zero_defect.history.projection import MACHINE_DEVIATIONS
from zero_defect.lines.catalog import DEFECTS, title_of
from zero_defect.lines.equipment import MACHINE_TYPES, EquipmentError, EquipmentStore, Machine
from zero_defect.lines.registry import Registry, RegistryError
from zero_defect.lines.store import LineStore
from zero_defect.security.auth import ROLE_META, SCREENS, Principal
from zero_defect.security.roles import RoleError, RoleStore
from zero_defect.service import QualitySystem
from zero_defect.storage.database import metadata

# Что означает каждое право — для матрицы «что могут делать пользователи».
PERMISSION_TITLES = {
    "read": "видеть линию, изделия, несоответствия, показатели",
    "decide": "подтверждать, отклонять, закрывать несоответствия",
    "set_cause": "устанавливать причину подтверждённого несоответствия",
    "export": "выгружать историю (OCEL 2.0)",
    "line_manage": "создавать и менять линии, параметры экономики",
    "emulate": "управлять эмуляцией и прогонами в пульте эмулятора",
    "integrate": "запускать обмен с внешними системами",
    "admin": "целостность журнала, ключи, аудит, база данных, пользователи",
    "view_as": "смотреть интерфейс глазами другой роли",
    "ingest": "передавать события в систему",
    "equipment_manage": "заводить и менять конкретные станки по типам из справочника",
}


class DefectSpec(BaseModel):
    code: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=1, max_length=160)
    method: str = Field(min_length=1, max_length=400)


class MachineTypeSpec(BaseModel):
    key: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=1, max_length=160)
    processing: list[str] = []
    defect_types: list[str] = []
    parameters: dict[str, dict] = {}


class RoleSpec(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    title: str = Field(min_length=1, max_length=120)
    screen: str
    permissions: list[str] = []


class UserSpec(BaseModel):
    user_id: str = Field(pattern="^[a-z][a-z0-9_.-]{1,63}$")
    name: str = Field(min_length=1, max_length=160)
    role: str
    password: str | None = Field(default=None, min_length=8, max_length=128)
    active: bool = True


# Столбцы, которые в просмотре не показываются никогда: секреты и шифротекст.
HIDDEN = {
    "users": {"password_hash"},
    "ledger": {"nonce", "ciphertext", "prev_hash", "record_hash", "signature"},
    "outbox": {"payload"},
    "lines": {"config"},
    "equipment": {"config"},
}


class MachineSpec(BaseModel):
    """Станок, как его заводит технолог по типу: паспорт, дефекты и допуски параметров."""

    equipment_id: str = Field(min_length=1, max_length=48)
    title: str = Field(min_length=1, max_length=160)
    machine_type: str
    inventory_no: str = ""
    status: str = "active"
    defect_types: list[str] | None = None
    parameters: dict[str, dict] | None = None

    def machine(self) -> Machine:
        if self.machine_type not in MACHINE_TYPES:
            raise EquipmentError(f"тип станка {self.machine_type!r} неизвестен")
        machine = Machine.of_type(
            self.equipment_id.strip(),
            self.title.strip(),
            self.machine_type,
            inventory_no=self.inventory_no.strip(),
            status=self.status,
        )
        if self.defect_types is not None:
            machine.defect_types = [code.strip().upper() for code in self.defect_types]
        if self.parameters is not None:
            # Набор параметров задаёт тип станка — их пишет MachineLogs; у конкретного
            # станка меняются только допуски.
            for key, spec in self.parameters.items():
                if key in machine.parameters:
                    machine.parameters[key].update(
                        {bound: spec[bound] for bound in ("low", "high") if bound in spec}
                    )
        return machine


def register(
    app: FastAPI,
    system: QualitySystem,
    lines: LineStore,
    equipment: EquipmentStore,
    registry: Registry,
    roles_store: RoleStore,
    principal,
) -> None:
    def admin(user: Principal = Depends(principal)) -> Principal:
        system.security.authorize(user, "admin", "admin_read")
        return user

    @app.get("/api/admin/db")
    def database(table: str | None = None, limit: int = 30, _: Principal = Depends(admin)) -> dict:
        tables = metadata.tables
        summary = []
        with system.database.engine.connect() as conn:
            for name, model in tables.items():
                count = conn.execute(select(func.count()).select_from(model)).scalar_one()
                summary.append(
                    {
                        "table": name,
                        "rows": count,
                        "columns": [
                            c.name for c in model.columns if c.name not in HIDDEN.get(name, set())
                        ],
                        "hidden": sorted(HIDDEN.get(name, set())),
                    }
                )
            rows = []
            if table:
                model = tables.get(table)
                if model is None:
                    raise HTTPException(404, f"таблицы {table} нет")
                visible = [c for c in model.columns if c.name not in HIDDEN.get(table, set())]
                order = (
                    model.c.seq.desc() if "seq" in model.c else list(model.primary_key.columns)[0]
                )
                for row in conn.execute(
                    select(*visible).order_by(order).limit(max(1, min(limit, 200)))
                ).mappings():
                    rows.append(
                        {
                            key: (
                                value
                                if isinstance(value, (int, float, str, bool)) or value is None
                                else str(value)
                            )
                            for key, value in row.items()
                        }
                    )
                if table == "users":
                    for row in rows:
                        row["has_password"] = system.users.has_password(row["user_id"])
        return {"dialect": system.database.dialect, "tables": summary, "table": table, "rows": rows}

    @app.get("/api/admin/roles")
    def roles(_: Principal = Depends(admin)) -> dict:
        return {
            "permissions": [
                {"permission": key, "title": title} for key, title in PERMISSION_TITLES.items()
            ],
            "screens": [{"screen": key, "title": ROLE_META[key]["title"]} for key in SCREENS],
            "roles": roles_store.all(),
            "users": [
                {**u, "has_password": system.users.has_password(u["user_id"])}
                for u in system.users.listing()
            ],
        }

    @app.post("/api/admin/roles")
    def role_create(spec: RoleSpec, user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "role_create", {"role": spec.code})
        try:
            return roles_store.save(spec.code, spec.model_dump(), user.user_id, create=True)
        except RoleError as error:
            raise HTTPException(422, str(error)) from error

    @app.put("/api/admin/roles/{code}")
    def role_update(code: str, spec: RoleSpec, user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "role_update", {"role": code})
        if spec.code != code:
            raise HTTPException(422, "код роли не меняется")
        try:
            return roles_store.save(code, spec.model_dump(), user.user_id, create=False)
        except RoleError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/admin/users")
    def user_create(spec: UserSpec, user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "user_create", {"user_id": spec.user_id})
        try:
            system.users.add(spec.user_id, spec.name, spec.role)
            if spec.password:
                system.users.set_password(spec.user_id, spec.password)
        except (ValueError, KeyError) as error:
            raise HTTPException(422, str(error)) from error
        except Exception as error:  # нарушение уникальности логина в базе
            raise HTTPException(409, f"пользователь {spec.user_id} уже есть") from error
        return {"user_id": spec.user_id, "role": spec.role}

    @app.put("/api/admin/users/{user_id}")
    def user_update(user_id: str, spec: UserSpec, user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "user_update", {"user_id": user_id})
        if spec.user_id != user_id:
            raise HTTPException(422, "логин не меняется")
        if user_id == user.user_id and not spec.active:
            raise HTTPException(422, "свою учётную запись отключить нельзя")
        try:
            system.users.update(user_id, spec.name, spec.role, spec.active)
            if spec.password:
                system.users.set_password(user_id, spec.password)
        except (ValueError, KeyError) as error:
            raise HTTPException(422, str(error)) from error
        return {"user_id": user_id, "role": spec.role, "active": spec.active}

    @app.post("/api/admin/defects")
    def defect_create(spec: DefectSpec, user: Principal = Depends(principal)) -> dict:
        return _save_catalog(user, "defect", spec.code, spec.model_dump(), create=True)

    @app.put("/api/admin/defects/{code}")
    def defect_update(code: str, spec: DefectSpec, user: Principal = Depends(principal)) -> dict:
        if spec.code != code:
            raise HTTPException(422, "код дефекта не меняется")
        return _save_catalog(user, "defect", code, spec.model_dump(), create=False)

    @app.get("/api/admin/machine-types")
    def machine_types(_: Principal = Depends(admin)) -> list[dict]:
        used = defaultdict(int)
        for machine in equipment.all():
            used[machine.machine_type] += 1
        return [
            {"key": key, **spec, "machines": used.get(key, 0)}
            for key, spec in sorted(MACHINE_TYPES.items())
        ]

    @app.post("/api/admin/machine-types")
    def machine_type_create(spec: MachineTypeSpec, user: Principal = Depends(principal)) -> dict:
        return _save_catalog(user, "machine_type", spec.key, spec.model_dump(), create=True)

    @app.put("/api/admin/machine-types/{key}")
    def machine_type_update(
        key: str, spec: MachineTypeSpec, user: Principal = Depends(principal)
    ) -> dict:
        if spec.key != key:
            raise HTTPException(422, "код типа не меняется")
        return _save_catalog(user, "machine_type", key, spec.model_dump(), create=False)

    def _save_catalog(user: Principal, kind: str, code: str, spec: dict, create: bool) -> dict:
        system.security.authorize(user, "admin", f"{kind}_save", {"code": code})
        try:
            return {"code": code, **registry.save(kind, code, spec, user.user_id, create)}
        except RegistryError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/admin/defects")
    def defects(_: Principal = Depends(admin)) -> list[dict]:
        """Справочник дефектов: название, где возникает по конфигурации, сколько найдено."""

        state = system.snapshot()
        configured: dict[str, list[str]] = defaultdict(list)
        for config in lines.all():
            for node in config.nodes:
                for code in node.defect_types:
                    configured[code].append(f"{config.line_id} · {node.title}")
        found = Counter(card.defect_type for card in state.cards.values())
        confirmed = Counter(
            card.defect_type
            for card in state.cards.values()
            if card.status in ("confirmed", "closed")
        )
        codes = sorted(set(DEFECTS) | set(configured) | set(found))
        return [
            {
                "code": code,
                "title": title_of(code),
                "method": DEFECTS.get(code, {}).get("method", "—"),
                "where": configured.get(code, []),
                "found": found.get(code, 0),
                "confirmed": confirmed.get(code, 0),
            }
            for code in codes
        ]

    def equipment_reader(user: Principal = Depends(principal)) -> Principal:
        system.security.authorize(user, "equipment_manage", "equipment_read")
        return user

    @app.get("/api/equipment")
    def equipment_for_technologist(_: Principal = Depends(equipment_reader)) -> list[dict]:
        return equipment_list_rows()

    @app.get("/api/admin/equipment")
    def equipment_list(_: Principal = Depends(admin)) -> list[dict]:
        return equipment_list_rows()

    def equipment_list_rows() -> list[dict]:
        """Справочник станков: паспорт, где стоит по конфигурации линий, последнее состояние."""

        state = system.snapshot()
        used: dict[str, list[str]] = defaultdict(list)
        for config in lines.all():
            for node in config.nodes:
                if node.kind == "operation" and node.equipment_id:
                    used[node.equipment_id].append(f"{config.line_id} · {node.title}")
        rows = []
        for machine in equipment.all():
            events = state.history.machine_events.get(machine.equipment_id, [])
            rows.append(
                {
                    **machine.to_dict(),
                    "used_in": used.get(machine.equipment_id, []),
                    "state": events[-1].machine_state if events else None,
                    "deviations": sum(
                        1 for event in events if event.machine_state in MACHINE_DEVIATIONS
                    ),
                }
            )
        return rows

    @app.post("/api/equipment")
    def equipment_create(spec: MachineSpec, user: Principal = Depends(principal)) -> dict:
        system.security.authorize(
            user, "equipment_manage", "equipment_create", {"equipment_id": spec.equipment_id}
        )
        try:
            machine = equipment.save(spec.machine(), user.user_id, create=True)
        except EquipmentError as error:
            raise HTTPException(422, str(error)) from error
        return machine.to_dict()

    @app.put("/api/equipment/{equipment_id}")
    def equipment_update(
        equipment_id: str, spec: MachineSpec, user: Principal = Depends(principal)
    ) -> dict:
        system.security.authorize(
            user, "equipment_manage", "equipment_update", {"equipment_id": equipment_id}
        )
        if spec.equipment_id != equipment_id:
            raise HTTPException(422, "код станка не меняется")
        try:
            machine = equipment.save(spec.machine(), user.user_id, create=False)
        except EquipmentError as error:
            raise HTTPException(422, str(error)) from error
        return machine.to_dict()
