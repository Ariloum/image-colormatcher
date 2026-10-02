"""Горячие клавиши: реестр действий со стабильными id и переназначение (T-0085).

У каждого действия главного окна — id (`file.open`, `match.selection`, `masks.brush`…). Клавиши по
умолчанию запоминаются при регистрации; `AppSettings.shortcuts` хранит только переназначения
(id -> клавиши в PortableText, «» — без клавиши). Удержание Z / X («до» / референс) — такие же записи
без QAction. [ ] и Shift+[ ] кисти ловятся по физической клавише — показываются для справки.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QAction, QKeySequence

from .i18n import N_

GROUPS = [   # порядок разделов в таблице настроек; названия переводятся при показе
    ("file", N_("Файл")), ("edit", N_("Правка")), ("photos", N_("Фото")), ("refs", N_("Референсы")), ("match", N_("Авто-баланс")),
    ("view", N_("Вид")), ("nav", N_("Навигация")), ("masks", N_("Маски")), ("app", N_("Программа")),
]


def seq_text(seq: QKeySequence) -> str:
    return seq.toString(QKeySequence.PortableText)


@dataclass
class Binding:
    id: str
    title: str                                   # как в меню (уже переведено)
    defaults: list[str]                          # клавиши по умолчанию (PortableText)
    action: QAction | None = None                # None — удержание Z / X или справочная запись
    editable: bool = True
    current: list[str] = field(default_factory=list)

    @property
    def group(self) -> str:
        return self.id.split(".")[0]


class Registry:
    def __init__(self) -> None:
        self.items: dict[str, Binding] = {}

    def add(self, bid: str, action: QAction | None, title: str | None = None, defaults: list[str] | None = None,
            editable: bool = True) -> Binding:
        if defaults is None:
            defaults = [seq_text(s) for s in action.shortcuts()] if action is not None else []
        title = title if title is not None else action.text().split("\t")[0].replace("&", "")
        b = Binding(bid, title, defaults, action, editable, list(defaults))
        self.items[bid] = b
        return b

    def apply(self, overrides: dict[str, str]) -> None:
        """Переназначения поверх умолчаний; неизвестные id игнорируются."""
        for b in self.items.values():
            if not b.editable:
                continue
            ov = overrides.get(b.id)
            b.current = list(b.defaults) if ov is None else ([ov] if ov else [])
            if b.action is not None:
                b.action.setShortcuts([QKeySequence(s) for s in b.current])

    def conflicts(self, current: dict[str, list[str]]) -> dict[str, list[str]]:
        """Одинаковые клавиши у разных действий: {клавиши: [id…]} (только конфликты)."""
        seen: dict[str, list[str]] = {}
        for bid, seqs in current.items():
            for s in seqs:
                if s:
                    seen.setdefault(s, []).append(bid)
        return {s: ids for s, ids in seen.items() if len(ids) > 1}
