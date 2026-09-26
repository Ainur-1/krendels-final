"""
Несоответствия: от сообщения анализатора до решения людей.

Четыре вещи здесь намеренно разведены по разным статусам, как требует постановка:
- сообщение о признаке дефекта — что сказал анализатор (исходные наблюдения);
- подтверждённое несоответствие — что решил контролёр;
- предполагаемая причина — гипотеза системы с основаниями (analysis.causes);
- подтверждённая причина или ошибка — решение технолога или контролёра.

Ничто из второго–четвёртого не пишется поверх первого: решения — отдельные записи
журнала с автором и причиной, а статус карточки вычисляется из них заново.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from zero_defect.history.projection import History, Observation

# Статусы рассмотрения несоответствия.
REPORTED = "reported"
UNDER_REVIEW = "under_review"
RECHECK_REQUESTED = "recheck_requested"
CONFIRMED = "confirmed"
REJECTED = "rejected"
# Подтверждённое несоответствие устранено: доработано, допущено по решению или изделие
# списано. Факт подтверждения при этом остаётся в показателях.
CLOSED = "closed"
CONFIRMED_STATUSES = (CONFIRMED, CLOSED)
OPEN_STATUSES = (REPORTED, UNDER_REVIEW, RECHECK_REQUESTED)

# Действие решения → статус, в который оно переводит карточку, и откуда это можно.
TRANSITIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "start_review": (UNDER_REVIEW, (REPORTED, RECHECK_REQUESTED)),
    "request_recheck": (RECHECK_REQUESTED, (REPORTED, UNDER_REVIEW)),
    "confirm": (CONFIRMED, (REPORTED, UNDER_REVIEW, RECHECK_REQUESTED)),
    "reject": (REJECTED, (REPORTED, UNDER_REVIEW, RECHECK_REQUESTED)),
    "close": (CLOSED, (CONFIRMED,)),
    "reopen": (UNDER_REVIEW, (CONFIRMED, REJECTED, CLOSED)),
}

# Установить причину можно только у подтверждённого несоответствия: у отклонённого
# сигнала причины нет, а у нерассмотренного её рано называть.
CAUSE_ACTION = "confirm_cause"
CAUSE_CATEGORIES = (
    "incoming_defect",
    "equipment_problem",
    "operator_error",
    "process_issue",
    "handling_damage",
    "other",
)

SEVERITY_RANK = {None: 0, "minor": 1, "major": 2, "critical": 3}

# Решения по наблюдению «оценка невозможна». Это не несоответствие, поэтому своих статусов
# у него нет: человек либо назначает повторный контроль (проблему решит следующее
# достоверное наблюдение), либо допускает изделие по ручному контролю. Автоматически
# плохой снимок годностью не становится — годность здесь всегда решение человека.
UNASSESSABLE_PREFIX = "NA-"
UNASSESSABLE_ACTIONS = ("request_recheck", "accept_manual")


@dataclass(frozen=True)
class Decision:
    """Решение человека по несоответствию. Хранится отдельной записью журнала."""

    decision_id: str
    nc_id: str
    action: str
    author_id: str
    author_role: str
    reason: str
    decided_at: datetime
    cause_category: str | None = None
    ledger_seq: int = 0

    def as_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "nc_id": self.nc_id,
            "action": self.action,
            "author_id": self.author_id,
            "author_role": self.author_role,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
            "cause_category": self.cause_category,
        }


@dataclass
class Signal:
    """Одно наблюдение признака, относящееся к несоответствию."""

    observation: Observation
    defect_index: int

    @property
    def event_id(self) -> str:
        return self.observation.event.event_id


@dataclass
class Nonconformance:
    """Карточка несоответствия. Статус вычисляется из решений, а не хранится."""

    nc_id: str
    item_id: str
    defect_type: str
    area: str | None
    signals: list[Signal] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    status: str = REPORTED
    confirmed_cause: str | None = None
    cause_decision: Decision | None = None
    ignored_decisions: list[Decision] = field(default_factory=list)
    assessment: dict | None = None

    @property
    def first_signal(self) -> Signal:
        return self.signals[0]

    @property
    def first_detected_at(self) -> datetime:
        return self.first_signal.observation.occurred_at

    @property
    def severity(self) -> str | None:
        values = [
            signal.observation.event.defects[signal.defect_index].severity
            for signal in self.signals
        ]
        return max(values, key=lambda value: SEVERITY_RANK.get(value, 0), default=None)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def signals_after_last_decision(self) -> int:
        if not self.decisions:
            return 0
        last = self.decisions[-1].decided_at
        return sum(1 for signal in self.signals if signal.observation.occurred_at > last)


def nc_id_for(item_id: str, defect_type: str, area: str | None) -> str:
    """Устойчивый идентификатор: одни и те же наблюдения всегда дают тот же номер.

    От этого зависит, что пересборка истории и параллельная обработка не порождают
    новых карточек для уже известного дефекта.
    """

    digest = hashlib.sha256(f"{item_id}|{defect_type}|{area or ''}".encode()).hexdigest()
    return f"NC-{digest[:8].upper()}"


def build_nonconformances(history: History, decisions: list[Decision]) -> dict[str, Nonconformance]:
    """Связывает наблюдения одного дефекта в одну карточку и применяет решения.

    Ключ дефекта — объект (компонент, если анализатор его назвал), тип и зона. Второе и
    последующие наблюдения того же дефекта добавляются в ту же карточку, поэтому
    повторная съёмка не увеличивает число дефектов в показателях.
    """

    cards: dict[str, Nonconformance] = {}
    for observation in history.observations:
        if observation.event.inspection_result != "defect_signs_found":
            continue
        for index, defect in enumerate(observation.event.defects):
            target = defect.component_item_id or observation.event.item_id
            nc_id = nc_id_for(target, defect.defect_type, defect.area)
            card = cards.get(nc_id)
            if card is None:
                card = Nonconformance(nc_id, target, defect.defect_type, defect.area)
                cards[nc_id] = card
            card.signals.append(Signal(observation, index))

    for decision in sorted(decisions, key=lambda item: (item.decided_at, item.ledger_seq)):
        card = cards.get(decision.nc_id)
        if card is None:
            continue
        if not apply_decision(card, decision):
            card.ignored_decisions.append(decision)
    return cards


def check_decision(card: Nonconformance, action: str, cause_category: str | None) -> str | None:
    """Возвращает текст ошибки, если решение недопустимо в текущем статусе."""

    if action == CAUSE_ACTION:
        if card.status not in CONFIRMED_STATUSES:
            return "причину устанавливают только для подтверждённого несоответствия"
        if cause_category not in CAUSE_CATEGORIES:
            return f"категория причины должна быть одной из: {', '.join(CAUSE_CATEGORIES)}"
        return None
    transition = TRANSITIONS.get(action)
    if transition is None:
        return f"неизвестное действие {action!r}"
    target, allowed_from = transition
    if card.status not in allowed_from:
        return f"из статуса {card.status} действие {action} невозможно"
    return None


def apply_decision(card: Nonconformance, decision: Decision) -> bool:
    if check_decision(card, decision.action, decision.cause_category) is not None:
        return False
    card.decisions.append(decision)
    if decision.action == CAUSE_ACTION:
        card.confirmed_cause = decision.cause_category
        card.cause_decision = decision
        return True
    card.status = TRANSITIONS[decision.action][0]
    if card.status not in CONFIRMED_STATUSES:
        # Отклонение или повторное открытие снимает ранее установленную причину: у
        # несоответствия, которого нет, не может быть подтверждённой причины.
        card.confirmed_cause = None
        card.cause_decision = None
    return True


def recheck_outcome(card: Nonconformance, history: History) -> str | None:
    """Что показала проверка после запроса дополнительного контроля, если она была."""

    requests = [item for item in card.decisions if item.action == "request_recheck"]
    if not requests:
        return None
    since = requests[-1].decided_at
    item = history.items.get(card.item_id)
    later = [obs for obs in item.observations if obs.occurred_at > since] if item else []
    for observation in later:
        types = {defect.defect_type for defect in observation.event.defects}
        if (
            observation.event.inspection_result == "defect_signs_found"
            and card.defect_type in types
        ):
            return "повторная проверка снова обнаружила признак"
        if observation.effective_result == "no_defect_signs" and observation.reliable:
            return "повторная проверка признаков не обнаружила"
    return "повторная проверка ещё не проводилась"


def item_status(
    item_id: str,
    history: History,
    cards: dict[str, Nonconformance],
    accepted: frozenset[str] = frozenset(),
    by_item: dict[str, list[Nonconformance]] | None = None,
    pending: frozenset[str] = frozenset(),
) -> str:
    """Итоговый статус изделия с учётом его компонентов.

    accepted — наблюдения «оценка невозможна», по которым человек допустил изделие по
    ручному контролю: такое наблюдение считается достоверным и чистым. by_item — карточки
    по изделию: без него каждое изделие перебирает все карточки, и на десятках тысяч
    событий пересборка становится квадратичной. pending — изделия с нерешённой «оценка
    невозможна»: пока её не сняли повторным контролем или решением человека, изделие не
    признаётся годным, даже если следующие проверки чистые.
    """

    scope = {item_id, *_descendants(item_id, history)}
    if by_item is None:
        related = [card for card in cards.values() if card.item_id in scope]
    else:
        related = [card for member in scope for card in by_item.get(member, [])]
    if any(card.status == CONFIRMED for card in related):
        return "nonconforming"
    if any(card.is_open for card in related):
        return "suspect"
    if scope & pending:
        return "not_assessable"
    item = history.items.get(item_id)
    if item is None:
        return "unknown"
    # Компонент после установки в сборку проверяется финальным контролем сборки, поэтому
    # его итог — итог сборки, если у самого компонента вопросов нет.
    if item.parent_id and item.parent_id in history.items:
        return item_status(item.parent_id, history, cards, accepted, by_item, pending)

    def unclear(obs: Observation) -> bool:
        return obs.effective_result == "not_assessable" and obs.event.event_id not in accepted

    finals = [obs for obs in item.observations if obs.event.checkpoint_kind == "final"]
    if finals:
        last = finals[-1]
        if (last.effective_result == "no_defect_signs" and last.reliable) or (
            last.effective_result == "not_assessable" and not unclear(last)
        ):
            return "conforming"
        if unclear(last):
            return "not_assessable"
    if item.observations and unclear(item.observations[-1]):
        return "not_assessable"
    return "in_progress"


def _descendants(item_id: str, history: History) -> list[str]:
    root = history.items.get(item_id)
    result: list[str] = []
    stack = list(root.components) if root else []
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.append(current)
        if current in history.items:
            stack.extend(history.items[current].components)
    return result
