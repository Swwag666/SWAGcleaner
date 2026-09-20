"""UWP-приложения (AppX/MSIX): список, удаление выбранного, откат.

Чтение и запись идут через PowerShell (Get-AppxPackage / Remove-AppxPackage /
Add-AppxPackage) — pywin32 AppX не покрывает. Команды гоняются через
-EncodedCommand: инлайн -Command и .ps1 без BOM ломают кириллицу в именах
пакетов на не-русской локали (поймано на полигоне, промт №24).

Политика: только per-user Remove-AppxPackage (без -AllUsers и без
provisioning). Пакет при этом остаётся staged в C:\\Program Files\\WindowsApps,
поэтому откат — Add-AppxPackage -Register по сохранённому манифесту. Если
InstallLocation уже стёрт (система добила пакет), откат честно сообщает, что
вернуть можно только из Store.

Фреймворки, системные и несъёмные пакеты (IsFramework, SignatureKind System)
не показываем и не трогаем: они — зависимости чужих приложений.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import typing as t
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UwpPackage:
    name: str               # короткое имя (Microsoft.BingWeather)
    full_name: str          # PackageFullName (с версией и архитектурой)
    install_location: str   # путь staged-пакета (для отката)
    publisher: str = ""


def _encode_ps(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _run_ps(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Прогнать PowerShell-скрипт через EncodedCommand, вернуть результат."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
         "-EncodedCommand", _encode_ps(script)],
        capture_output=True, timeout=timeout)


# Минимальный набор полей, ConvertTo-Json сжат чтобы не раздувать NDJSON.
_LIST_SCRIPT = r"""
Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.SignatureKind -ne 'System' -and $_.InstallLocation } |
  Select-Object Name, PackageFullName, InstallLocation, @{N='Publisher';E={$_.Publisher}} |
  ConvertTo-Json -Compress -Depth 2
"""


class UwpController:
    """Контроллер UWP-пакетов: список + точечное удаление со снапшотом."""

    def __init__(self,
                 runner: t.Optional[t.Callable[[str, int], t.Any]] = None
                 ) -> None:
        # runner инъектируется в тестах: по умолчанию — настоящий PowerShell.
        self._runner = runner if runner is not None else _run_ps
        self._cache: t.Optional[t.List[UwpPackage]] = None

    def list_packages(self, refresh: bool = False) -> t.List[UwpPackage]:
        """Список пользовательских пакетов, свежие сверху по имени."""
        if self._cache is not None and not refresh:
            return list(self._cache)
        result = self._runner(_LIST_SCRIPT, 120)
        if result.returncode != 0:
            raise RuntimeError(
                "Get-AppxPackage не ответил: "
                + result.stderr.decode("utf-8", errors="replace")[:300])
        raw = result.stdout.decode("utf-8", errors="replace").strip()
        # PowerShell пишет прогресс CLIXML в stderr/stdout на первом запуске —
        # берём от первого '[' или '{'.
        start = min([i for i in (raw.find("["), raw.find("{")) if i >= 0],
                    default=-1)
        if start < 0:
            self._cache = []
            return []
        data = json.loads(raw[start:])
        if isinstance(data, dict):
            data = [data]
        packages = [
            UwpPackage(
                name=str(p.get("Name", "")),
                full_name=str(p.get("PackageFullName", "")),
                install_location=str(p.get("InstallLocation", "")),
                publisher=str(p.get("Publisher", "")),
            )
            for p in data
            if p.get("PackageFullName") and p.get("InstallLocation")
        ]
        packages.sort(key=lambda p: p.name.lower())
        self._cache = packages
        return list(packages)

    def remove_package(self, full_name: str) -> t.Dict[str, t.Any]:
        """Удалить пакет у текущего пользователя; вернуть снапшот отката.

        Снапшот хранит InstallLocation и путь манифеста: пакет остаётся
        staged, Add-AppxPackage -Register возвращает его за секунды.
        """
        pkg = next((p for p in self.list_packages()
                    if p.full_name == full_name), None)
        if pkg is None:
            raise ValueError(f"пакет не найден у пользователя: {full_name}")
        manifest = pkg.install_location.rstrip("\\/") + "\\AppxManifest.xml"
        snapshot = {
            "kind": "uwp",
            "name": pkg.name,
            "package_full_name": pkg.full_name,
            "install_location": pkg.install_location,
            "manifest": manifest,
        }
        safe = full_name.replace("'", "''")
        result = self._runner(
            f"Remove-AppxPackage -Package '{safe}' -ErrorAction Stop", 120)
        if result.returncode != 0:
            raise RuntimeError(
                "Remove-AppxPackage не сработал: "
                + result.stderr.decode("utf-8", errors="replace")[:300])
        self._cache = None
        return snapshot

    def restore_package(self, snapshot: t.Dict[str, t.Any]) -> str:
        """Вернуть пакет из staged-копии: Add-AppxPackage -Register манифест."""
        if not isinstance(snapshot, dict) or snapshot.get("kind") != "uwp":
            raise ValueError("снапшот UWP не найден или битый")
        manifest = str(snapshot.get("manifest", ""))
        if not manifest.lower().endswith("\\appxmanifest.xml"):
            raise ValueError(f"снапшот UWP ведёт не на манифест: {manifest}")
        safe = manifest.replace("'", "''")
        result = self._runner(
            "if (-not (Test-Path '" + safe + "')) { "
            "Write-Error 'staged-копия пакета стёрта — вернуть можно только из Store'; exit 3 }; "
            "Add-AppxPackage -DisableDevelopmentMode -Register '" + safe + "' -ErrorAction Stop",
            180)
        if result.returncode != 0:
            raise RuntimeError(
                "Add-AppxPackage -Register не сработал: "
                + result.stderr.decode("utf-8", errors="replace")[:300])
        self._cache = None
        return str(snapshot.get("name", ""))
