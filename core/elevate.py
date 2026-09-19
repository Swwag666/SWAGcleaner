"""Запуск от имени администратора без постоянного UAC-окна.

Механика классическая для своих же приложений:
1. Собранный exe несёт манифест requireAdministrator (см. SWAGcleaner.spec) —
   любой запуск exe так или иначе админский, двойной клик покажет UAC один раз.
2. При первом админском запуске создаётся задача планировщика SWAGcleaner
   (RunLevel=HighestAvailable, без триггеров) и ярлык в меню «Пуск»
   «SWAGcleaner (без UAC)»: ярлык дёргает schtasks /run, задача поднимает
   уже админскую копию — окно UAC больше не появляется вообще.
3. Если exe запущен без прав, а задача уже есть — процесс просто просит
   планировщик запустить задачу и завершается (перезапуск через задачу).

Всё это актуально только для собранной сборки (sys.frozen): в dev-режиме
из venv механика выключена, чтобы не путать отладку.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

log = logging.getLogger(__name__)

TASK_NAME = "SWAGcleaner"
SHORTCUT_NAME = "SWAGcleaner (no UAC).lnk"


def is_admin() -> bool:
    """Текущий процесс уже с правами администратора."""
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 — права не смогли спросить, считаем что нет
        return False


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _schtasks(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="cp866",
        errors="replace",
    )


def task_exists() -> bool:
    try:
        r = _schtasks("/query", "/tn", TASK_NAME)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def task_command() -> str | None:
    """Команда, записанная в задаче (XML): нужна, чтобы заметить переезд exe."""
    try:
        r = _schtasks("/query", "/tn", TASK_NAME, "/xml")
        if r.returncode != 0:
            return None
        text = r.stdout
        lo = text.find("<Command>")
        hi = text.find("</Command>")
        if lo < 0 or hi < 0:
            return None
        return text[lo + len("<Command>"):hi].strip().strip('"')
    except Exception:  # noqa: BLE001
        return None


def _task_xml(exe_path: str) -> str:
    cmd = xml_escape(exe_path)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo><Description>SWAGcleaner elevated launcher</Description></RegistrationInfo>\n"
        "  <Triggers />\n"
        "  <Principals><Principal id=\"Author\">"
        "<LogonType>InteractiveToken</LogonType>"
        "<RunLevel>HighestAvailable</RunLevel></Principal></Principals>\n"
        "  <Settings>"
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<AllowHardTerminate>true</AllowHardTerminate>"
        "<StartWhenAvailable>false</StartWhenAvailable>"
        "<Enabled>true</Enabled><Hidden>false</Hidden>"
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>"
        "<Priority>7</Priority>"
        "</Settings>\n"
        "  <Actions Context=\"Author\"><Exec>"
        f"<Command>\"{cmd}\"</Command><Arguments>--gui</Arguments>"
        "</Exec></Actions>\n"
        "</Task>\n"
    )


def register_task(exe_path: str) -> bool:
    """Создать (или перезаписать) задачу планировщика на exe. Нужны права."""
    xml_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", suffix=".xml", prefix="swagtask_", delete=False
        ) as fh:
            xml_path = fh.name
            fh.write(_task_xml(exe_path).encode("utf-16"))
        r = _schtasks("/create", "/tn", TASK_NAME, "/xml", xml_path, "/f")
        ok = r.returncode == 0
        if not ok:
            log.warning("schtasks /create rc=%s: %s", r.returncode, r.stderr.strip())
        return ok
    except Exception:  # noqa: BLE001
        log.warning("задача планировщика не создана", exc_info=True)
        return False
    finally:
        if xml_path:
            try:
                Path(xml_path).unlink(missing_ok=True)
            except OSError:
                pass


def run_task() -> bool:
    """Попросить планировщик запустить задачу (админская копия без UAC)."""
    try:
        r = _schtasks("/run", "/tn", TASK_NAME, timeout=15)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _create_shortcut(exe_path: str) -> None:
    """Ярлык «без UAC» в меню Пуск текущего пользователя (через WScript.Shell)."""
    try:
        start_menu = _shortcut_path().parent
        start_menu.mkdir(parents=True, exist_ok=True)
        lnk = _shortcut_path()
        # Пути встраиваются в PowerShell-строку: апострофы удваиваем, чтобы
        # имя профиля с кавычкой не ломало и не «расширяло» команду.
        lnk_ps = str(lnk).replace("'", "''")
        exe_ps = str(exe_path).replace("'", "''")
        ps = (
            "$w = New-Object -ComObject WScript.Shell; "
            f"$s = $w.CreateShortcut('{lnk_ps}'); "
            "$s.TargetPath = 'schtasks.exe'; "
            f"$s.Arguments = '/run /tn {TASK_NAME}'; "
            f"$s.IconLocation = '{exe_ps},0'; "
            "$s.WindowStyle = 7; "
            "$s.Description = 'SWAGcleaner admin, no UAC prompt'; "
            "$s.Save()"
        )
        # Имя ярлыка и описание - строго ASCII: кириллица в -Command/-File
        # на части систем (не-RU локаль гостя, проверено в VM) доезжает битой,
        # и WScript.Shell.Save падает. -EncodedCommand страхует остальное.
        import base64
        encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not lnk.exists():
            log.warning(
                "ярлык без UAC: powershell rc=%s, файл %s",
                result.returncode, "создан" if lnk.exists() else "не появился",
            )
    except Exception:  # noqa: BLE001 — ярлык не критичен, просто пропускаем
        log.warning("ярлык без UAC не создан", exc_info=True)


def _shortcut_path() -> Path:
    return (
        Path.home()
        / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
        / SHORTCUT_NAME
    )


def ensure_task() -> None:
    """Из админского процесса: задача и ярлык на месте и с актуальным путём."""
    if not is_frozen():
        return
    exe = str(Path(sys.executable).resolve())
    try:
        if not task_exists() or task_command() != exe:
            if register_task(exe):
                log.info("задача планировщика %s зарегистрирована", TASK_NAME)
        # Ярлык «без UAC» нужен всегда, а не только в момент регистрации:
        # задача могла пережить удаление ярлыка (и наоборот).
        if not _shortcut_path().exists():
            _create_shortcut(exe)
    except Exception:  # noqa: BLE001 — отсутствие задачи не мешает работе
        log.warning("ensure_task пропущен", exc_info=True)


def relaunch_elevated(argv: list[str]) -> bool:
    """Перезапуск себя через ShellExecute runas (одно окно UAC)."""
    try:
        params = " ".join(f'"{a}"' for a in argv)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        return rc > 32
    except Exception:  # noqa: BLE001
        return False


def maybe_elevate(gui_argv: list[str]) -> bool:
    """Точка входа из main() для GUI-режима.

    True = этот процесс должен завершиться (перезапуск уже пошёл через
    задачу или runas). False = продолжаем обычный запуск в этом процессе.
    """
    if sys.platform != "win32" or not is_frozen():
        return False
    if is_admin():
        ensure_task()
        return False
    if task_exists() and run_task():
        log.info("запуск передан задаче планировщика (без UAC)")
        return True
    # Задачи нет (первый запуск после установки): одно окно UAC, дальше
    # админская копия сама зарегистрирует задачу через ensure_task().
    if relaunch_elevated(gui_argv):
        return True
    # Пользователь отказался от UAC: работаем без прав, как раньше.
    log.info("запуск без прав администратора (UAC отклонён)")
    return False
