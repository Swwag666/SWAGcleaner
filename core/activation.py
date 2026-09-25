"""Активация Windows/Office — встроенные MAS-скрипты (HWID + Office Ohook).

Скрипты лежат в assets/activation/ и пакуются в exe. Запуск: cmd.exe /c со
stdin=DEVNULL (внутри есть choice-подтверждение на eval-редакциях — без stdin
оно не зависает, а падает мимо), таймаут, классификация результата по строкам
вывода (тот же протокол, что читал MakuTweaker, только без молчаливого
проглатывания: хвост вывода возвращается для диагностики).

Плюс ручной KMS-путь: slmgr /ipk (ключ по редакции) + /skms + /ato.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import typing as t
from pathlib import Path

log = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "assets" / "activation"
_HWID_SCRIPT = "MakuTweakerNew.HWID.cmd"
_OFFICE_SCRIPT = "MakuTweakerNew.Office.cmd"

# Ключи GVLK по редакциям (публичные KMS-клиентские ключи Microsoft).
_EDITION_KEYS = {
    "Professional": "W269N-WFGWX-YVC9B-4J6C9-T83GX",
    "Core": "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99",
    "CoreSingleLanguage": "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99",
    "Education": "NW6C2-QMPVW-D7KKK-3GKT6-VCFB2",
    "ProEducation": "NW6C2-QMPVW-D7KKK-3GKT6-VCFB2",
    "Enterprise": "NPPR9-FWDCX-D2C8J-H872K-2YT43",
    "IoTEnterprise": "NPPR9-FWDCX-D2C8J-H872K-2YT43",
    "EnterpriseS": "M7XTQ-FN8P6-TTKYV-9D4CC-J462D",
    "IoTEnterpriseS": "KBN8V-HFGQ4-MGXVD-347P6-PDQGT",
}

KMS_SERVERS = ("kms.digiboy.ir", "kms.ddns.net", "k.zpale.com")


def _script_path(name: str) -> Path:
    """Путь до встроенного скрипта: в exe — через _MEIPASS, иначе рядом."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS"))  # type: ignore[attr-defined]
        return base / "assets" / "activation" / name
    return _SCRIPT_DIR / name


def _classify(output: str) -> str:
    """Классификация результата MAS по маркерным строкам вывода."""
    low = output.lower()
    if "permanently activated" in low:
        return "activated"
    if "activation is not required" in low:
        return "already"
    if "evaluation editions cannot be activated" in low:
        return "eval"
    if "not connected" in low:
        return "offline"
    return "error"


class Activator:
    """Запуск активации. runner инъектируется для тестов."""

    def __init__(self, runner: t.Optional[t.Callable[..., t.Any]] = None,
                 script_dir: t.Optional[Path] = None) -> None:
        self._runner = runner if runner is not None else subprocess.run
        self._dir = script_dir

    def _run_script(self, name: str, timeout: int = 600) -> t.Dict[str, t.Any]:
        src = self._dir / name if self._dir else _script_path(name)
        if not src.is_file():
            return {"ok": False, "status": "missing_script",
                    "detail": str(src)}
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / name
            dst.write_bytes(src.read_bytes())
            try:
                result = self._runner(
                    ["cmd.exe", "/c", str(dst)],
                    capture_output=True, timeout=timeout,
                    stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                return {"ok": False, "status": "timeout"}
            out = result.stdout or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            status = _classify(out)
            return {"ok": status in ("activated", "already"),
                    "status": status, "rc": result.returncode,
                    "tail": out.strip()[-400:]}

    def activate_windows(self) -> t.Dict[str, t.Any]:
        """HWID-активация Windows (MAS)."""
        return self._run_script(_HWID_SCRIPT)

    def activate_office(self) -> t.Dict[str, t.Any]:
        """Ohook-активация Office (MAS)."""
        return self._run_script(_OFFICE_SCRIPT)

    def edition(self) -> str:
        """EditionID текущей Windows (для подбора GVLK-ключа)."""
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            return str(winreg.QueryValueEx(key, "EditionID")[0])

    def kms_activate(self, server: str,
                     timeout: int = 180) -> t.Dict[str, t.Any]:
        """KMS-путь: /ipk ключ редакции → /skms сервер → /ato."""
        key = _EDITION_KEYS.get(self.edition())
        if key is None:
            return {"ok": False, "status": "unknown_edition"}
        if server not in KMS_SERVERS:
            return {"ok": False, "status": "unknown_server"}
        slmgr = r"C:\Windows\System32\slmgr.vbs"
        outputs = []
        for args in (["cscript", slmgr, "/ipk", key],
                     ["cscript", slmgr, "/skms", server],
                     ["cscript", slmgr, "/ato"]):
            result = self._runner(args, capture_output=True, timeout=timeout)
            out = result.stdout or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            outputs.append(out)
            if result.returncode != 0:
                return {"ok": False, "status": "error",
                        "tail": out.strip()[-400:]}
        joined = "\n".join(outputs).lower()
        ok = "successfully" in joined or "успешно" in joined
        return {"ok": ok, "status": "activated" if ok else "error",
                "tail": joined.strip()[-400:]}
