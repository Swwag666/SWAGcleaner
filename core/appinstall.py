"""Установка приложений через winget — каталог, установка списком.

Каталог собран из MakuTweaker + очевидные базовые вещи (7zip, VLC, Chrome).
Полировка относительно оригинала: winget зовётся напрямую argv-массивом
(без powershell-обёртки и конкатенации строк), с обоими флагами согласий
(--accept-package-agreements у них отсутствовал — интерактивный вис),
silent там, где пакет умеет, отмена между пакетами, честный результат по
каждому пакету.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import typing as t
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppEntry:
    winget_id: str
    name: str
    category: str  # browser | media | util | dev | game


CATALOG: t.Tuple[AppEntry, ...] = (
    AppEntry("Google.Chrome", "Google Chrome", "browser"),
    AppEntry("Mozilla.Firefox", "Mozilla Firefox", "browser"),
    AppEntry("Vivaldi.Vivaldi", "Vivaldi", "browser"),
    AppEntry("7zip.7zip", "7-Zip", "util"),
    AppEntry("Notepad++.Notepad++", "Notepad++", "util"),
    AppEntry("ShareX.ShareX", "ShareX", "util"),
    AppEntry("RamenSoftware.Windhawk", "Windhawk", "util"),
    AppEntry("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo", "util"),
    AppEntry("AnyDesk.AnyDesk", "AnyDesk", "util"),
    AppEntry("VideoLAN.VLC", "VLC", "media"),
    AppEntry("Spotify.Spotify", "Spotify", "media"),
    AppEntry("Audacity.Audacity", "Audacity", "media"),
    AppEntry("HandBrake.HandBrake", "HandBrake", "media"),
    AppEntry("dotPDN.PaintDotNet", "Paint.NET", "media"),
    AppEntry("ByteDance.CapCut", "CapCut", "media"),
    AppEntry("qBittorrent.qBittorrent", "qBittorrent", "media"),
    AppEntry("Git.Git", "Git", "dev"),
    AppEntry("Python.Python.3.13", "Python 3.13", "dev"),
    AppEntry("PrismLauncher.PrismLauncher", "Prism Launcher", "game"),
)


class AppInstaller:
    """winget-обёртка: наличие, установка списка, отмена между пакетами."""

    def __init__(self,
                 runner: t.Optional[t.Callable[..., t.Any]] = None,
                 winget_path: t.Optional[str] = None) -> None:
        self._runner = runner if runner is not None else subprocess.run
        self._winget = winget_path

    def winget_available(self) -> bool:
        if self._winget is not None:
            return True
        return shutil.which("winget") is not None

    def _exe(self) -> str:
        return self._winget or "winget"

    def install(self, winget_id: str,
                timeout: int = 900) -> t.Dict[str, t.Any]:
        """Поставить один пакет. rc 0 → ok; -1978335189 → уже стоит."""
        argv = [self._exe(), "install", "--id", winget_id, "-e",
                "--accept-source-agreements", "--accept-package-agreements",
                "--disable-interactivity"]
        try:
            result = self._runner(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"id": winget_id, "ok": False, "error": "timeout"}
        rc = result.returncode
        out = (result.stdout or b"")
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        # -1978335189 (0x8A15002B) — уже установлен и свежий.
        already = rc == -1978335189 or "No newer version" in out
        return {"id": winget_id, "ok": rc == 0 or already,
                "already": already, "rc": rc,
                "tail": out.strip()[-300:]}

    def installed_ids(self, timeout: int = 180) -> t.Set[str]:
        """Id пакетов, которые winget видит установленными.

        Парсим таблицу «winget list»: строки после разделителя из тире,
        id - вторая колонка с точкой в имени. Ошибка/таймаут - пустое
        множество: снимок конфига тогда просто не включит приложения.
        """
        argv = [self._exe(), "list", "--accept-source-agreements",
                "--disable-interactivity"]
        try:
            result = self._runner(argv, capture_output=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return set()
        out = result.stdout or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        found: t.Set[str] = set()
        started = False
        for line in out.splitlines():
            if not started:
                if set(line.strip()) and set(line.strip()) <= {"-"}:
                    started = True
                continue
            parts = line.split()
            for token in parts:
                if "." in token and any(c.isalnum() for c in token):
                    found.add(token)
                    break
        return found

    def install_many(self, ids: t.Sequence[str],
                     progress: t.Optional[t.Callable[[int, int, str],
                                                     None]] = None,
                     cancel: t.Optional[threading.Event] = None
                     ) -> t.List[t.Dict[str, t.Any]]:
        """Поставить список пакетов по очереди, с отменой между ними."""
        results: t.List[t.Dict[str, t.Any]] = []
        for i, wid in enumerate(ids):
            if cancel is not None and cancel.is_set():
                break
            if progress is not None:
                progress(i, len(ids), wid)
            results.append(self.install(wid))
        return results
