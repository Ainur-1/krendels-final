"""
Загружаемый поток: файл с линией и прогоном, который система проигрывает на экране.

Формат zd-flow/1 — JSON с двумя частями. line — этапы процесса с длительностями и
вероятностями дефекта (то же, что задаёт редактор линии). run — сколько изделий
пропустить, за сколько секунд показать весь прогон и в какие изделия и этапы внести
дефект принудительно. Ускорение считается само: весь процесс укладывается в заданное
время показа, поэтому прогон реальной смены смотрится за минуту.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from zero_defect.lines.model import LineConfig

FORMAT = "zd-flow/1"


class ForcedDefect(BaseModel):
    item: int = Field(ge=1, description="номер изделия в прогоне, с 1")
    stage: int = Field(ge=1, description="номер этапа в линии, с 1")
    kind: str = Field(default="defect", pattern="^(defect|deviation)$")


class RunSpec(BaseModel):
    items: int = Field(ge=1, le=200)
    duration_s: float = Field(default=60, ge=10, le=900)
    seed: int = 1
    defects: list[ForcedDefect] = []


class FlowFile(BaseModel):
    format: str = Field(pattern=f"^{FORMAT}$")
    # Линия в форме редактора (serving.lines_routes.LineSpec); проверяется там же.
    line: dict
    run: RunSpec

    @model_validator(mode="after")
    def _stages_exist(self) -> FlowFile:
        steps = len(self.line.get("steps", []))
        for defect in self.run.defects:
            if defect.stage > steps:
                raise ValueError(f"дефект указан на этапе {defect.stage}, а этапов {steps}")
            if defect.item > self.run.items:
                raise ValueError(
                    f"дефект указан у изделия {defect.item}, а изделий {self.run.items}"
                )
        return self


def speed_for(config: LineConfig, items: int, duration_s: float) -> float:
    """Ускорение, при котором прогон items изделий займёт duration_s секунд показа."""

    path = sum(node.duration_s for node in config.chain_from(config.sources()[0].node_id))
    # Операции с паузами и перемещения между этапами добавляют к пути около четверти.
    total = config.takt_s * max(items - 1, 0) + path * 1.25
    return max(1.0, total / duration_s)
