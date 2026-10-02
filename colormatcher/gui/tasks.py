"""Фоновые задачи GUI: вызов функции в пуле потоков, результат — сигналом в главный поток."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)   # сделано, всего


class Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], with_progress: bool = False):
        super().__init__()
        self.fn = fn
        self.with_progress = with_progress
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(self.signals.progress.emit) if self.with_progress else self.fn()
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
            return
        self.signals.finished.emit(result)


def run_task(fn: Callable[..., Any], on_done: Callable[[Any], None], on_error: Callable[[str], None],
             pool: QThreadPool | None = None, on_progress: Callable[[int, int], None] | None = None) -> Task:
    """Выполнить fn в пуле. С on_progress fn получает аргумент progress(сделано, всего)."""
    task = Task(fn, with_progress=on_progress is not None)
    task.setAutoDelete(False)  # держим ссылку, пока не пришёл сигнал
    task.signals.finished.connect(on_done)
    task.signals.failed.connect(on_error)
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    (pool or QThreadPool.globalInstance()).start(task)
    return task
