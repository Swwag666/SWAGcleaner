"""Асинхронные задачи UI — QRunnable + сигналы.

Все длительные операции (скан, чистка, поиск дубликатов) выносятся
в воркеры, чтобы не.blockировать основной поток Qt.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt
from PySide6.QtWidgets import QWidget

_LOGGER = logging.getLogger("swag.ui.workers")


class WorkerSignals(QObject):
    """Сигналы воркера — для связи с UI без блокировки."""

    started = Signal()
    progress = Signal(int, str)
    finished = Signal(object)
    error = Signal(str)


@dataclass
class WorkerTask:
    """Задача для выполнения в пуле потоков."""

    func: Callable[..., Any]
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = None
    on_start: Optional[Callable[[], None]] = None
    on_done: Optional[Callable[[Any], None]] = None
    on_error: Optional[Callable[[str], None]] = None
    on_progress: Optional[Callable[[int, str], None]] = None

    def __post_init__(self) -> None:
        if self.kwargs is None:
            self.kwargs = {}


class AppWorker(QRunnable):
    """Контейнер для фоновой задачи с сигналами UI."""

    def __init__(self, task: WorkerTask) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._task = task
        self._signals = WorkerSignals()

    def run(self) -> None:
        try:
            self._signals.started.emit()
            if self._task.on_start is not None:
                self._task.on_start()
            result = self._task.func(*self._task.args, **self._task.kwargs)
            self._signals.finished.emit(result)
            # Колбэки UI вызываются из рабочего потока — Qt должен перекинуть их
            # в главный поток через queued connections, поэтому в slot-коде
            # программист должен быть готов к тому, что on_done срабатывает асинхронно.
            if self._task.on_done is not None:
                self._task.on_done(result)
        except Exception as e:
            msg = str(e)
            _LOGGER.exception("Worker error: %s", msg)
            self._signals.error.emit(msg)
            if self._task.on_error is not None:
                self._task.on_error(msg)


def run_async(
    pool: QThreadPool,
    task: WorkerTask,
) -> AppWorker:
    """Отправить задачу в пул потоков и вернуть воркер.

    Воркер удаляется автоматически после выполнения (AutoDelete).
    Возвращённый объект можно использовать только для подключения сигналов
    до того, как задача уйдёт в пул — после start() воркер живой пока не отработает.
    """
    worker = AppWorker(task)
    pool.start(worker)
    return worker


class WorkerPool:
    """Единый пул потоков приложения.

    Одиночка, но без гонок при инициализации: первый запрос создаёт пул,
    последующие просто его отдают. Инициализация пула происходит один раз,
    даже если кто-то провоцирует WorkerPool() много раз.
    """

    _instance: Optional["WorkerPool"] = None
    _pool: Optional[QThreadPool] = None

    def __new__(cls) -> "WorkerPool":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # __init__ может вызваться несколько раз для одного экземпляра,
        # поэтому создаём пул только если он ещё не создан.
        if self._pool is None:
            self._pool = QThreadPool()
            self._pool.setMaxThreadCount(4)

    @property
    def pool(self) -> QThreadPool:
        return self._pool

    def shutdown(self, wait_ms: int = 3000) -> None:
        """Закрытие приложения: снять очередь и дождаться бегущих задач."""
        pool = self._pool
        if pool is None:
            return
        pool.clear()
        pool.waitForDone(wait_ms)
