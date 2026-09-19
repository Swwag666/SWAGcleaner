"""Мост к Rust-ядру swagscan.exe поверх NDJSON-протокола.

Провайдер по духу равен InstalledProvider/SystemProvider из core/ports.py:
UI и остальное ядро не знают, что за disk-операции отвечает отдельный бинарь.

Порядок команд: одна команда = один запрос {id, cmd, ...} -> поток событий
(progress/file/dupgroups/log) -> финальный {event:"result", id, ok, data|error}.
Команды сериализуются замком: один процесс, один живой job за раз.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

_LOGGER = logging.getLogger("swag.core.swagscan")

PROTOCOL = "swagscan-ndjson/1"


class SwagscanError(RuntimeError):
    """Ядро недоступно или вернуло ошибку."""


@dataclass
class FileEvent:
    path: str
    size: int
    mtime: int
    categories: t.List[str] = field(default_factory=list)


def candidate_binary_paths() -> t.List[Path]:
    """Где ищем swagscan.exe, по убыванию приоритета."""
    out: t.List[Path] = []
    env = os.environ.get("SWAGSCAN_BIN")
    if env:
        out.append(Path(env))
    if getattr(sys, "frozen", False):  # PyInstaller
        exe_dir = Path(sys.executable).parent
        out.append(exe_dir / "swagscan.exe")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(Path(meipass) / "swagscan.exe")
    root = Path(__file__).resolve().parent.parent
    out.append(root / "bin" / "swagscan.exe")
    out.append(root / "dist" / "swagscan.exe")
    for profile in ("release", "debug"):
        out.append(root / "rust" / "swagscan" / "target" / profile / "swagscan.exe")
    return out


class SwagscanClient:
    """Один живой процесс swagscan, команды через stdin, события из stdout."""

    def __init__(self, exe: t.Optional[Path] = None) -> None:
        self._exe = Path(exe) if exe else self.locate_binary()
        self._proc: t.Optional[subprocess.Popen] = None
        self._job_lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._seq = 0
        self.hello: t.Dict[str, t.Any] = {}
        # Строки stdout ядра собирает фоновый читатель: тогда ожидание
        # событий идёт с таймаутом, а не висит на блокирующем readline().
        self._lines: "queue.Queue[t.Optional[str]]" = queue.Queue()
        # Хвост stderr ядра: паники Rust и предупреждения попадают в ошибку.
        self._stderr_tail: "collections.deque[str]" = collections.deque(maxlen=200)

    @staticmethod
    def locate_binary() -> Path:
        for p in candidate_binary_paths():
            if p.exists():
                return p
        raise SwagscanError(
            "swagscan.exe не найден. Собери ядро: cargo build --release в rust/swagscan "
            "или положи бинарь в bin/. Переменная SWAGSCAN_BIN тоже работает."
        )

    @property
    def binary(self) -> Path:
        return self._exe

    def available(self) -> bool:
        return self._exe.exists()

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        """Фоновый читатель stdout: строки в очередь, EOF — сентинел None."""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        """stderr ядра копим в хвост: паника Rust иначе теряется в DEVNULL."""
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                self._stderr_tail.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def _stderr_hint(self) -> str:
        tail = [ln for ln in self._stderr_tail if ln.strip()][-5:]
        return ("; stderr: " + " | ".join(tail)) if tail else ""

    def _close_proc_locked(self, proc: subprocess.Popen) -> None:
        """Закрыть трубы убитого/завершённого процесса (под _io_lock)."""
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except (OSError, ValueError):
                pass

    def start(self) -> None:
        with self._io_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if self._proc is not None:
                # Мёртвый процесс: закрыть трубы до замены, иначе утечка хендлов.
                self._close_proc_locked(self._proc)
                self._proc = None
            if not self._exe.exists():
                raise SwagscanError(f"нет бинаря: {self._exe}")
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # Чистим очередь от строк возможной прошлой жизни процесса.
            while True:
                try:
                    self._lines.get_nowait()
                except queue.Empty:
                    break
            self._proc = subprocess.Popen(
                [str(self._exe)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
            threading.Thread(target=self._read_stdout, args=(self._proc,), daemon=True).start()
            threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True).start()
            try:
                line = self._lines.get(timeout=15)
            except queue.Empty:
                self._stop_locked()
                raise SwagscanError("ядро не поздоровалось: timeout 15s")
            if line is None:
                self._stop_locked()
                raise SwagscanError("ядро умерло до hello" + self._stderr_hint())
            try:
                hello = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                self._stop_locked()
                raise SwagscanError(f"ядро не поздоровалось: {line!r}")
            if hello.get("protocol") != PROTOCOL:
                self._stop_locked()
                raise SwagscanError(f"нежданный протокол: {hello.get('protocol')!r}")
            self.hello = hello
            _LOGGER.info("swagscan запущен: %s", self._exe)

    def _stop_locked(self) -> None:
        """Остановка процесса; вызывать только под _io_lock."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    if proc.stdin is not None:
                        proc.stdin.write('{"cmd":"quit"}\n')
                        proc.stdin.flush()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
        finally:
            self._close_proc_locked(proc)
            self._proc = None
            self.hello = {}
            # Очередь строк умершего процесса не должна протечь в следующий старт.
            while True:
                try:
                    self._lines.get_nowait()
                except queue.Empty:
                    break

    def stop(self) -> None:
        with self._io_lock:
            self._stop_locked()

    def __enter__(self) -> "SwagscanClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def _read_events(
        self,
        want_id: int,
        on_progress: t.Optional[t.Callable[[dict], None]],
        on_file: t.Optional[t.Callable[[FileEvent], None]],
        on_groups: t.Optional[t.Callable[[dict], None]],
        timeout_sec: float,
    ) -> dict:
        # Процесс держим локальной ссылкой: stop() из другого потока не
        # должен уронить нас AttributeError посреди чтения.
        proc = self._proc
        assert proc is not None
        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SwagscanError("ядро молчит: timeout, убиваю процесс")
            try:
                line = self._lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if proc.poll() is not None:
                    raise SwagscanError("ядро умерло" + self._stderr_hint())
                continue
            if line is None:
                raise SwagscanError(
                    "ядро закрыло вывод (вероятно, умерло)" + self._stderr_hint())
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                _LOGGER.warning("не-JSON строка от ядра: %.120s", line)
                continue
            kind = ev.get("event")
            if kind == "progress":
                if on_progress:
                    on_progress(ev)
            elif kind == "file":
                if on_file:
                    on_file(
                        FileEvent(
                            path=ev.get("path", ""),
                            size=int(ev.get("size", 0)),
                            mtime=int(ev.get("mtime", 0)),
                            categories=list(ev.get("categories") or []),
                        )
                    )
            elif kind == "dupgroups":
                if on_groups:
                    on_groups(ev)
            elif kind == "log":
                _LOGGER.debug("swagscan: %s", ev.get("msg"))
            elif kind == "result":
                if str(ev.get("id")) == str(want_id):
                    return ev
            elif kind == "cancelled":
                if on_progress:
                    on_progress({"event": "cancelled", "job": ev.get("job")})

    def command(
        self,
        cmd: str,
        params: t.Optional[dict] = None,
        on_progress: t.Optional[t.Callable[[dict], None]] = None,
        on_file: t.Optional[t.Callable[[FileEvent], None]] = None,
        on_groups: t.Optional[t.Callable[[dict], None]] = None,
        timeout_sec: float = 1800.0,
    ) -> t.Any:
        """Отправить команду и дождаться result. Возвращает data или бросает SwagscanError.

        Одна команда за раз (job-lock), поэтому cancel() из другого потока попадает
        ровно в тот job, который сейчас идёт.
        """
        self.start()
        with self._job_lock:
            self._seq += 1
            cid = self._seq
            payload = {"id": cid, "cmd": cmd}
            if params:
                payload.update(params)
            # Все записи в stdin ядра (команды И cancel) идут под одним
            # замком: два потока не могут перемешать байты кадров.
            with self._io_lock:
                proc = self._proc
                if proc is None or proc.stdin is None:
                    raise SwagscanError("ядро не запущено")
                try:
                    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                except OSError as e:
                    self._stop_locked()
                    raise SwagscanError(f"не записать команду ядру: {e}") from e
            try:
                ev = self._read_events(cid, on_progress, on_file, on_groups, timeout_sec)
            except SwagscanError:
                self.stop()
                raise
            if not ev.get("ok", False):
                raise SwagscanError(str(ev.get("error") or "ядро вернуло ok=false"))
            return ev.get("data")

    def cancel(self) -> None:
        """Сказать ядру бросить текущий job, не дожидаясь его результата."""
        with self._io_lock:
            if self._proc is None or self._proc.poll() is not None:
                return
            if self._proc.stdin is None:
                return
            try:
                self._proc.stdin.write('{"cmd":"cancel"}\n')
                self._proc.stdin.flush()
            except OSError:
                _LOGGER.warning("cancel не дошёл до ядра")

    def index(self, roots: t.Sequence[str], top: int = 40, categories: bool = True,
              on_progress=None) -> dict:
        return self.command("index", {"roots": list(roots), "top": top, "categories": categories},
                            on_progress=on_progress)

    def candidates(self, roots: t.Sequence[str], cat_roots: bool = False, min_size: int = 0,
                   on_progress=None, on_file=None) -> dict:
        return self.command("candidates", {"roots": list(roots), "cat_roots": cat_roots,
                                           "min_size": min_size},
                            on_progress=on_progress, on_file=on_file)

    def duplicates(self, roots: t.Sequence[str], min_size: int = 1024 * 1024,
                   exts: t.Optional[t.Sequence[str]] = None, limit_groups: int = 2000,
                   on_progress=None, on_groups=None) -> dict:
        return self.command("duplicates", {"roots": list(roots), "min_size": min_size,
                                           "exts": list(exts or []), "limit_groups": limit_groups},
                            on_progress=on_progress, on_groups=on_groups)

    def purge(self, items: t.Sequence[t.Mapping[str, str]], dry_run: bool = True,
              on_progress=None) -> dict:
        return self.command("purge", {"items": [dict(i) for i in items], "dry_run": dry_run},
                            on_progress=on_progress)

    def drives(self) -> list:
        return self.command("drives")

    def cat_meta(self) -> dict:
        """Каталог категорий ядра: id -> {title, lane, risk, regrows, admin}."""
        rows = self.command("cat_meta") or []
        return {r["id"]: r for r in rows if r.get("id")}

    def ping(self) -> bool:
        try:
            return bool(self.command("ping", timeout_sec=5).get("pong"))
        except SwagscanError:
            return False


_default: t.Optional[SwagscanClient] = None
_default_lock = threading.Lock()


def get_client() -> SwagscanClient:
    """Общий клиент процесса на приложение (одно ядро, один индекс)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = SwagscanClient()
        return _default
