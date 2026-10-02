"""Диалоги: экспорт (T-0020 в GUI) и настройки приложения по вкладкам (T-0023, T-0024, T-0035, T-0084, T-0085)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..models import masks
from ..models.workspace import ExportSettings
from ..services.naming import Naming
from . import i18n
from . import theme as themes
from .i18n import N_, tr
from .qtutil import palette_icon
from .settings import AppSettings
from .shortcuts import GROUPS, Registry, seq_text


class ExportDialog(QDialog):
    def __init__(self, parent, settings: ExportSettings, n_selected: int, n_scene: int, n_all: int):
        super().__init__(parent)
        self.setWindowTitle(tr("Экспорт с правками"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.scope = QComboBox()
        for key, title, n in (("selection", tr("Выделенные"), n_selected), ("scene", tr("Текущая сцена"), n_scene), ("all", tr("Весь воркспейс"), n_all)):
            self.scope.addItem(f"{title} ({n})", key)
        if not n_selected:
            self.scope.setCurrentIndex(2)
        row = QHBoxLayout()
        self.out = QLineEdit(settings.out_dir or "")
        self.out.setPlaceholderText(tr("рядом с исходниками"))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.out, 1)
        row.addWidget(browse)
        self.naming = QComboBox()
        for key, title in ((Naming.version, tr("Новая версия (_v001, _v002…)")), (Naming.suffix, tr("С суффиксом")), (Naming.overwrite, tr("Те же имена"))):
            self.naming.addItem(title, key)
        self.naming.setCurrentIndex(max(0, self.naming.findData(settings.naming)))
        self.suffix = QLineEdit(settings.suffix)
        self.format = QComboBox()
        for key, title in (("keep", tr("Как исходник")), ("jpg", "JPEG"), ("png", "PNG"), ("tif", "TIFF")):
            self.format.addItem(title, key)
        self.format.setCurrentIndex(max(0, self.format.findData(settings.format)))
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(settings.quality)
        self.depth = QComboBox()
        for key, title in (("keep", tr("Как исходник")), ("8", tr("8 бит")), ("16", tr("16 бит (PNG/TIFF)"))):
            self.depth.addItem(title, key)
        self.depth.setCurrentIndex(max(0, self.depth.findData(settings.bit_depth)))
        self.structure = QCheckBox(tr("Подпапки по сценам"))
        self.structure.setChecked(settings.keep_structure)
        self.force = QCheckBox(tr("Разрешить перезапись исходных файлов"))
        form.addRow(tr("Что"), self.scope)
        form.addRow(tr("Папка"), row)
        form.addRow(tr("Имена"), self.naming)
        form.addRow(tr("Суффикс"), self.suffix)
        form.addRow(tr("Формат"), self.format)
        form.addRow(tr("Качество"), self.quality)
        form.addRow(tr("Битность"), self.depth)
        form.addRow(self.structure)
        form.addRow(self.force)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("Экспортировать"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Папка экспорта"), self.out.text())
        if d:
            self.out.setText(d)

    def result_settings(self) -> tuple[str, ExportSettings, bool]:
        s = ExportSettings(
            out_dir=self.out.text().strip() or None, naming=self.naming.currentData(), suffix=self.suffix.text() or "_cm",
            format=self.format.currentData(), quality=self.quality.value(), bit_depth=self.depth.currentData(),
            keep_structure=self.structure.isChecked(),
        )
        return self.scope.currentData(), s, self.force.isChecked()


class SettingsDialog(QDialog):
    """Настройки по вкладкам (T-0084): основные, внешний вид, маски, горячие клавиши (T-0085).

    Тема, шрифты, клавиши применяются сразу (сигнал applied); масштаб интерфейса и язык — после перезапуска."""

    applied = Signal()

    def __init__(self, parent: QWidget, settings: AppSettings, shortcuts: Registry | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Настройки"))
        self.s = settings
        self.registry = shortcuts
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._general_tab(), tr("Основные"))
        self.tabs.addTab(self._look_tab(), tr("Внешний вид"))
        self.tabs.addTab(self._masks_tab(), tr("Маски"))
        if shortcuts is not None:
            self.tabs.addTab(self._keys_tab(), tr("Горячие клавиши"))
        note = QLabel(tr("Масштаб интерфейса применится после перезапуска; остальное — сразу (язык — после закрытия настроек)."))
        note.setObjectName("Dim")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("ОК"))
        bb.button(QDialogButtonBox.Apply).setText(tr("Применить"))
        bb.button(QDialogButtonBox.Cancel).setText(tr("Отмена"))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        lay.addWidget(bb)
        self.theme.currentIndexChanged.connect(self._apply)  # предпросмотр темы

    # --- вкладки -------------------------------------------------------------------------
    @staticmethod
    def _page() -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        return w, form

    def _general_tab(self) -> QWidget:
        s = self.s
        w, form = self._page()
        self.language = QComboBox()
        for code, name in sorted(i18n.available().items(), key=lambda kv: (kv[0] != "en", kv[1])):
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(s.language)))
        self.language.setToolTip(tr("Файлы языков: папка lang рядом с программой и %APPDATA%/colormatcher/lang"))
        form.addRow(tr("Язык"), self.language)
        self.reopen = QCheckBox(tr("Открывать последний воркспейс при запуске"))
        self.reopen.setChecked(s.reopen_last)
        form.addRow(self.reopen)
        self.autosave = QSpinBox()
        self.autosave.setRange(0, 60)
        self.autosave.setSuffix(tr(" мин"))
        self.autosave.setSpecialValueText(tr("выкл"))
        self.autosave.setValue(s.autosave_minutes)
        form.addRow(tr("Автосохранение"), self.autosave)
        self.wheel = QDoubleSpinBox()
        self.wheel.setRange(0.1, 20)
        self.wheel.setSingleStep(0.5)
        self.wheel.setValue(s.wheel_step)
        self.wheel.setToolTip(tr("На сколько шагов сдвигается ползунок за щелчок колеса; Shift — ×10, Ctrl — ×0.1. "
                                 "Значения в пресетах и воркспейсах не меняются."))
        form.addRow(tr("Колесо на ползунках"), self.wheel)
        self.relative = QCheckBox(tr("Групповая правка по умолчанию — относительно (+Δ к каждому фото)"))
        self.relative.setChecked(s.group_edit_relative)
        form.addRow(self.relative)
        return w

    def _look_tab(self) -> QWidget:
        s = self.s
        w, form = self._page()
        self.theme = QComboBox()
        for key, th in sorted(themes.available().items(), key=lambda kv: (not kv[1].dark, tr(kv[1].name))):
            self.theme.addItem(("◐ " if th.dark else "○ ") + tr(th.name), key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.75, 2.5)
        self.scale.setSingleStep(0.05)
        self.scale.setSuffix(" ×")
        self.scale.setValue(s.ui_scale)
        self.font = QComboBox()
        self.font.addItems(sorted(set(QFontDatabase.families())))
        self.font.setCurrentText(s.font_family)
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(7, 20)
        self.font_size.setSingleStep(0.5)
        self.font_size.setValue(s.font_size)
        self.label_size = QDoubleSpinBox()
        self.label_size.setRange(6, 18)
        self.label_size.setSingleStep(0.5)
        self.label_size.setValue(s.slider_label_size)
        self.thumb = QSpinBox()
        self.thumb.setRange(64, 320)
        self.thumb.setSingleStep(8)
        self.thumb.setValue(s.thumb_size)
        self.ref_thumb = QSpinBox()
        self.ref_thumb.setRange(64, 320)
        self.ref_thumb.setSingleStep(8)
        self.ref_thumb.setValue(s.ref_thumb_size)
        self.bg = QComboBox()
        for key, title in (("theme", N_("Как у темы")), ("gray18", N_("Нейтральный серый 18% (точная работа с цветом)")),
                           ("black", N_("Чёрный")), ("white", N_("Белый")), ("checker", N_("Шахматка"))):
            self.bg.addItem(tr(title), key)
        self.bg.setCurrentIndex(max(0, self.bg.findData(s.viewer_bg)))
        form.addRow(tr("Тема"), self.theme)
        form.addRow(tr("Масштаб интерфейса"), self.scale)
        form.addRow(tr("Шрифт"), self.font)
        form.addRow(tr("Размер шрифта"), self.font_size)
        form.addRow(tr("Подписи ползунков"), self.label_size)
        form.addRow(tr("Миниатюры ленты"), self.thumb)
        form.addRow(tr("Миниатюры референсов"), self.ref_thumb)
        form.addRow(tr("Фон просмотра"), self.bg)
        return w

    def _masks_tab(self) -> QWidget:
        s = self.s
        w, form = self._page()
        self.palette = QComboBox()
        for key, (title, _colors) in masks.PALETTES.items():
            self.palette.addItem(palette_icon(masks.palette_colors(key)), tr(title), key)
        self.palette.setCurrentIndex(max(0, self.palette.findData(s.mask_palette)))
        self.palette.setToolTip(tr("Цвета меток масок. Метки хранятся по номеру — смена палитры маски не меняет"))
        form.addRow(tr("Палитра масок"), self.palette)
        self.alpha = QSpinBox()
        self.alpha.setRange(0, 100)
        self.alpha.setSuffix(" %")
        self.alpha.setValue(round(s.mask_alpha * 100))
        form.addRow(tr("Прозрачность оверлея масок"), self.alpha)
        return w

    def _keys_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        self.key_filter = QLineEdit()
        self.key_filter.setPlaceholderText(tr("Поиск действия или клавиши…"))
        self.key_filter.textChanged.connect(self._filter_keys)
        v.addWidget(self.key_filter)
        self.keys = QTreeWidget()
        self.keys.setColumnCount(3)
        self.keys.setHeaderLabels([tr("Действие"), tr("Клавиши"), ""])
        self.keys.setRootIsDecorated(True)
        self.keys.setUniformRowHeights(False)
        row_h = max(26, self.fontMetrics().height() + 12)   # поле записи сочетания — не обрезать по высоте
        self.key_edits: dict[str, QKeySequenceEdit] = {}
        self.key_items: dict[str, QTreeWidgetItem] = {}
        groups = {}
        for gid, title in GROUPS:
            gi = QTreeWidgetItem(self.keys, [tr(title)])
            gi.setFirstColumnSpanned(True)
            gi.setExpanded(True)
            groups[gid] = gi
        for b in self.registry.items.values():
            parent = groups.get(b.group)
            if parent is None:
                parent = groups[b.group] = QTreeWidgetItem(self.keys, [b.group])
            it = QTreeWidgetItem(parent, [b.title])
            it.setSizeHint(0, QSize(0, row_h))
            self.key_items[b.id] = it
            if b.editable:
                edit = QKeySequenceEdit(QKeySequence(b.current[0]) if b.current else QKeySequence())
                edit.setMaximumSequenceLength(1)
                edit.setToolTip(tr("Щёлкните и нажмите новое сочетание; Backspace — без клавиши"))
                edit.keySequenceChanged.connect(lambda _s: self._check_keys())
                self.keys.setItemWidget(it, 1, edit)
                self.key_edits[b.id] = edit
                reset = QToolButton(text="↺")
                reset.setAutoRaise(True)
                reset.setToolTip(tr("По умолчанию: {keys}", keys=", ".join(b.defaults) or tr("нет")))
                reset.clicked.connect(lambda _c=False, bid=b.id: self._reset_key(bid))
                self.keys.setItemWidget(it, 2, reset)
            else:
                it.setText(1, ", ".join(b.current))
                it.setToolTip(1, tr("Физическая клавиша — работает при любой раскладке, не переназначается"))
        self.keys.header().setStretchLastSection(False)
        self.keys.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.keys.header().setSectionResizeMode(1, QHeaderView.Interactive)
        self.keys.setColumnWidth(1, 180)
        self.keys.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        v.addWidget(self.keys, 1)
        row = QHBoxLayout()
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        row.addWidget(self.key_status, 1)
        all_reset = QPushButton(tr("Все по умолчанию"))
        all_reset.clicked.connect(self._reset_all_keys)
        row.addWidget(all_reset)
        v.addLayout(row)
        self._check_keys()
        return w

    # --- горячие клавиши ----------------------------------------------------------------------
    def _key_values(self) -> dict[str, list[str]]:
        out = {}
        for bid, b in self.registry.items.items():
            if bid in self.key_edits:
                seq = self.key_edits[bid].keySequence()
                out[bid] = [seq_text(seq)] if not seq.isEmpty() else []
            else:
                out[bid] = list(b.current)
        return out

    def _check_keys(self) -> None:
        clash = self.registry.conflicts(self._key_values())
        bad = {bid for ids in clash.values() for bid in ids}
        for bid, it in self.key_items.items():
            it.setForeground(0, QColor("#e06c75") if bid in bad else self.keys.palette().text().color())
        if clash:
            parts = [f"{keys}: " + ", ".join(self.registry.items[i].title for i in ids) for keys, ids in clash.items()]
            self.key_status.setText("⚠ " + tr("Одинаковые клавиши у разных действий") + " — " + "; ".join(parts))
        else:
            self.key_status.setText("")

    def _reset_key(self, bid: str) -> None:
        d = self.registry.items[bid].defaults
        self.key_edits[bid].setKeySequence(QKeySequence(d[0]) if d else QKeySequence())

    def _reset_all_keys(self) -> None:
        for bid in self.key_edits:
            self._reset_key(bid)

    def _filter_keys(self, text: str) -> None:
        t = text.strip().lower()
        for bid, it in self.key_items.items():
            keys = ", ".join(self._key_values().get(bid, []))
            it.setHidden(bool(t) and t not in it.text(0).lower() and t not in keys.lower())

    def _key_overrides(self) -> dict[str, str]:
        """Только отличия от умолчаний (первая клавиша; «» — снята)."""
        out = {}
        for bid, seqs in self._key_values().items():
            b = self.registry.items[bid]
            if b.editable and seqs[:1] != b.defaults[:1]:
                out[bid] = seqs[0] if seqs else ""
        return out

    # --- применение ------------------------------------------------------------------------------
    def _apply(self) -> None:
        s = self.s
        s.language = self.language.currentData()
        s.theme = self.theme.currentData()
        s.ui_scale = round(self.scale.value(), 2)
        s.font_family = self.font.currentText()
        s.font_size = self.font_size.value()
        s.slider_label_size = self.label_size.value()
        s.thumb_size = self.thumb.value()
        s.ref_thumb_size = self.ref_thumb.value()
        s.viewer_bg = self.bg.currentData()
        s.wheel_step = self.wheel.value()
        s.autosave_minutes = self.autosave.value()
        s.reopen_last = self.reopen.isChecked()
        s.group_edit_relative = self.relative.isChecked()
        s.mask_palette = self.palette.currentData()
        s.mask_alpha = self.alpha.value() / 100
        if self.registry is not None:
            s.shortcuts = self._key_overrides()
        s.save()
        self.applied.emit()

    def _ok(self) -> None:
        self._apply()
        self.accept()
