"""Модуль автозагрузки — чтение и изменение элементов автозагрузки.

Источники: Run/RunOnce-ключи реестра (HKCU и HKLM, 64-битный вид) и папки
«Автозагрузка» (пользовательская и общая). Отключение делается не молча:
сначала снапшот значения в BackupStore, потом удаление; «вернуть как было»
пишет значение обратно из снапшота.

Реестр ходит через адаптер: настоящий — WinregRegistry (winreg), в тестах —
словарь в памяти. Так логика отключения проверяется без касания живой
машины.
"""
from __future__ import annotations

import os
import typing as t
from dataclasses import dataclass
from pathlib import Path

# Ключи автозагрузки в реестре: (куст, путь). Wow64-редирект отключаем —
# смотрим 64-битный вид, там живёт автозагрузка на 64-битной Windows.
_RUN_KEYS: t.Tuple[t.Tuple[str, str], ...] = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
)

SOURCE_REGISTRY = "registry"
SOURCE_FOLDER = "startup_folder"


@dataclass(frozen=True)
class StartupEntry:
    """Элемент автозагрузки — запись реестра или файл в папке запуска."""

    name: str
    path: str
    enabled: bool
    source: str  # registry | startup_folder
    hive: str = ""       # HKCU/HKLM (для registry)
    key_path: str = ""   # путь ключа (для registry)


class RegistryProtocol(t.Protocol):
    """Минимальный интерфейс реестра, который нужен автозагрузке."""

    def list_values(self, hive: str, key_path: str) -> t.List[t.Tuple[str, str, int]]:
        """Список значений ключа: (имя, данные-строка, тип)."""
        ...

    def delete_value(self, hive: str, key_path: str, value_name: str) -> None:
        ...

    def set_value(self, hive: str, key_path: str, value_name: str,
                  data: str, value_type: int) -> None:
        ...


class WinregRegistry:
    """Настоящий реестр Windows через winreg (чтение 64-битного вида)."""

    def __init__(self) -> None:
        import winreg
        self._winreg = winreg

    def _hive(self, name: str) -> int:
        return getattr(self._winreg, "HKEY_CURRENT_USER" if name == "HKCU"
                       else "HKEY_LOCAL_MACHINE")

    def list_values(self, hive: str, key_path: str) -> t.List[t.Tuple[str, str, int]]:
        out: t.List[t.Tuple[str, str, int]] = []
        flags = self._winreg.KEY_READ | self._winreg.KEY_WOW64_64KEY
        try:
            with self._winreg.OpenKey(self._hive(hive), key_path, 0, flags) as key:
                index = 0
                while True:
                    try:
                        name, data, vtype = self._winreg.EnumValue(key, index)
                    except OSError:
                        break
                    out.append((name, str(data), int(vtype)))
                    index += 1
        except OSError:
            pass
        return out

    def delete_value(self, hive: str, key_path: str, value_name: str) -> None:
        flags = self._winreg.KEY_SET_VALUE | self._winreg.KEY_WOW64_64KEY
        with self._winreg.OpenKey(self._hive(hive), key_path, 0, flags) as key:
            self._winreg.DeleteValue(key, value_name)

    def set_value(self, hive: str, key_path: str, value_name: str,
                  data: str, value_type: int) -> None:
        flags = self._winreg.KEY_SET_VALUE | self._winreg.KEY_WOW64_64KEY
        with self._winreg.OpenKey(self._hive(hive), key_path, 0, flags) as key:
            self._winreg.SetValueEx(key, value_name, 0, value_type, data)


def startup_folders() -> t.List[Path]:
    """Папки «Автозагрузка»: пользовательская и общая (все пользователи)."""
    folders: t.List[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        folders.append(Path(appdata) / "Microsoft" / "Windows"
                       / "Start Menu" / "Programs" / "Startup")
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        folders.append(Path(programdata) / "Microsoft" / "Windows"
                       / "Start Menu" / "Programs" / "Startup")
    return folders


def read_startup(registry: t.Optional[RegistryProtocol] = None,
                 folders: t.Optional[t.Sequence[Path]] = None,
                 ) -> t.List[StartupEntry]:
    """Прочитать автозагрузку целиком: реестр + папки запуска."""
    registry = registry if registry is not None else WinregRegistry()
    entries: t.List[StartupEntry] = []
    for hive, key_path in _RUN_KEYS:
        for name, data, _vtype in registry.list_values(hive, key_path):
            entries.append(StartupEntry(
                name=name, path=data, enabled=True,
                source=SOURCE_REGISTRY, hive=hive, key_path=key_path))
    for folder in (folders if folders is not None else startup_folders()):
        try:
            files = sorted(folder.iterdir())
        except OSError:
            continue
        for file in files:
            if file.name.lower() == "desktop.ini":
                continue
            entries.append(StartupEntry(
                name=file.stem, path=str(file), enabled=True,
                source=SOURCE_FOLDER))
    return entries


class StartupManager:
    """Менеджер автозагрузки — чтение, отключение со снапшотом, откат."""

    def __init__(self,
                 registry: t.Optional[RegistryProtocol] = None,
                 store: t.Optional[t.Any] = None) -> None:
        self._registry = registry if registry is not None else WinregRegistry()
        self._store = store

    def get_startup_entries(self) -> t.List[StartupEntry]:
        return read_startup(self._registry)

    def disable(self, entry: StartupEntry) -> str:
        """Отключить элемент: снапшот в BackupStore, затем удаление записи.

        Возвращает имя снапшота — по нему запись возвращается обратно.
        Поддерживаются записи реестра; файл из папки автозагрузки пока не
        трогаем (его отключение — переезд файла — отдельная история).
        """
        if entry.source != SOURCE_REGISTRY:
            raise ValueError(f"не умею отключать {entry.source}: {entry.name}")
        if self._store is None:
            raise ValueError("нет хранилища бэкапов — отключать нельзя")
        snapshot = f"startup-{entry.hive}-{entry.name}".replace("\\", "_")
        self._store.save(snapshot, {
            "hive": entry.hive,
            "key_path": entry.key_path,
            "value_name": entry.name,
            "value_data": entry.path,
            "value_type": 1,  # REG_SZ
        }, kind="startup_disable")
        self._registry.delete_value(entry.hive, entry.key_path, entry.name)
        return snapshot

    def restore(self, snapshot: str) -> None:
        """Вернуть запись автозагрузки из снапшота; снапшот после — удалить."""
        if self._store is None:
            raise ValueError("нет хранилища бэкапов")
        data = self._store.restore(snapshot)
        if not isinstance(data, dict) or "value_name" not in data:
            raise ValueError(f"снапшот не найден или битый: {snapshot}")
        self._registry.set_value(data["hive"], data["key_path"],
                                 data["value_name"], data["value_data"],
                                 int(data.get("value_type", 1)))
        self._store.remove(snapshot)
