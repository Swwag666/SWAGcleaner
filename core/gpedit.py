"""Установка gpedit (редактор групповых политик) на Home-редакции Windows.

В отличие от MakuTweaker (генерирует bat в %TEMP% и гоняет его), у нас чистый
Python: перечисляем .mum-пакеты GroupPolicy в %SystemRoot%\\servicing\\Packages
и ставим каждый через DISM напрямую. Результат по каждому пакету честно
возвращается.
"""
from __future__ import annotations

import logging
import os
import subprocess
import typing as t
from pathlib import Path

log = logging.getLogger(__name__)


def is_home_edition() -> bool:
    """EditionID начинается с Core → Home, gpedit отсутствует штатно."""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            edition = str(winreg.QueryValueEx(key, "EditionID")[0])
        return edition.startswith("Core")
    except OSError:
        return False


def gpedit_packages(
        system_root: t.Optional[t.Union[str, Path]] = None) -> t.List[Path]:
    """Список .mum-пакетов групповых политик в хранилище компонентов."""
    root = Path(system_root or os.environ.get("SystemRoot", r"C:\Windows"))
    pkg_dir = root / "servicing" / "Packages"
    if not pkg_dir.is_dir():
        return []
    return sorted(pkg_dir.glob("*GroupPolicy*.mum"))


def install_gpedit(
        runner: t.Optional[t.Callable[..., t.Any]] = None,
        system_root: t.Optional[t.Union[str, Path]] = None,
        progress: t.Optional[t.Callable[[int, int, str], None]] = None,
) -> t.Dict[str, t.Any]:
    """Поставить все GroupPolicy-пакеты через DISM.

    Возвращает {"total": N, "ok": N, "failed": [(pkg, rc)], "skipped": False}.
    На Pro/Enterprise ставить нечего — возвращаем skipped=True.
    """
    if not is_home_edition():
        return {"total": 0, "ok": 0, "failed": [], "skipped": True}
    packages = gpedit_packages(system_root)
    run = runner if runner is not None else subprocess.run
    failed: t.List[t.Tuple[str, int]] = []
    ok = 0
    for i, pkg in enumerate(packages):
        if progress is not None:
            progress(i, len(packages), pkg.name)
        result = run(["dism", "/online", "/norestart",
                      f"/add-package:{pkg}"],
                     capture_output=True, timeout=600)
        if result.returncode == 0:
            ok += 1
        else:
            failed.append((pkg.name, result.returncode))
            log.warning("DISM не встал %s rc=%s", pkg.name, result.returncode)
    return {"total": len(packages), "ok": ok, "failed": failed,
            "skipped": False}
