"""Провайдер установленных приложений для Windows.

Читает список установленных программ из реестра (ветки Uninstall).
Всё только на чтение: ни один ключ не создаётся и не изменяется.
На не-Windows системах провайдер просто возвращает пустой список,
поэтому модуль можно импортировать и тестировать где угодно.
"""
import sys
import typing as t

from core.models import AppInfo
from core.ports import InstalledProvider

# Ветки Uninstall в реестре: системная (64-бит), WOW6432Node (32-бит) и
# пользовательская — там живут программы, установленные «для меня».
SYSTEM_UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
WOW64_UNINSTALL_SUBKEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"


def _reg_value(winreg: t.Any, key: t.Any, name: str) -> t.Any:
    """Безопасно прочитать значение из открытого ключа реестра."""
    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


class WindowsInstalledProvider(InstalledProvider):
    """Провайдер установленных приложений Windows через реестр.

    Порядок работы: сначала читаем реальный реестр. Если там ничего нет
    (не Windows, нет прав, пустой реестр) — подставляем демо-набор,
    переданный в known_apps, чтобы UI было на чём проверять.
    """

    def __init__(
        self,
        known_apps: t.List[t.Dict[str, t.Any]] | None = None,
    ) -> None:
        self._known_apps = known_apps or []
        self._cache: t.List[AppInfo] = []
        self._cache_skip_wow64: bool | None = None

    # ---------- чтение реестра ----------

    def _populate_from_registry(self, skip_wow64: bool = False) -> None:
        """Читает установленные программы из реестра. Ничего не изменяет."""
        self._cache = []
        if sys.platform != "win32":
            return
        try:
            import winreg
        except ImportError:  # pragma: no cover - на Windows всегда есть
            return

        subkeys = [SYSTEM_UNINSTALL_SUBKEY]
        if not skip_wow64:
            subkeys.append(WOW64_UNINSTALL_SUBKEY)

        seen: t.Set[str] = set()
        hives = (
            (winreg.HKEY_LOCAL_MACHINE, subkeys),
            (winreg.HKEY_CURRENT_USER, [SYSTEM_UNINSTALL_SUBKEY]),
        )
        for hive, hive_subkeys in hives:
            for subkey in hive_subkeys:
                self._read_uninstall_branch(winreg, hive, subkey, seen)

    def _read_uninstall_branch(
        self,
        winreg: t.Any,
        hive: t.Any,
        subkey: str,
        seen: t.Set[str],
    ) -> None:
        """Пробегает по всем записям одной ветки Uninstall."""
        try:
            root = winreg.OpenKey(hive, subkey)
        except OSError:
            return
        with root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                app = self._read_uninstall_entry(winreg, root, name)
                if app is None:
                    continue
                key = app.display_name.lower()
                if key in seen:
                    continue
                seen.add(key)
                self._cache.append(app)

    def _read_uninstall_entry(
        self,
        winreg: t.Any,
        root: t.Any,
        name: str,
    ) -> AppInfo | None:
        """Читает одну запись; возвращает None, если это не программа."""
        try:
            with winreg.OpenKey(root, name) as key:
                display_name = _reg_value(winreg, key, "DisplayName")
                if not isinstance(display_name, str) or not display_name.strip():
                    return None
                # Служебные записи (обновления, компоненты системы) не показываем.
                if _reg_value(winreg, key, "SystemComponent") in (1, "1"):
                    return None
                if _reg_value(winreg, key, "ParentKeyName"):
                    return None
                install_location = _reg_value(winreg, key, "InstallLocation")
                publisher = _reg_value(winreg, key, "Publisher")
                return AppInfo(
                    display_name=display_name.strip(),
                    install_location=install_location if isinstance(install_location, str) else None,
                    publisher=publisher if isinstance(publisher, str) else None,
                )
        except OSError:
            return None

    def _read_known_apps(self) -> None:
        """Чтение предзаполненных приложений (демо-набор для UI)."""
        for app in self._known_apps:
            try:
                info = AppInfo(
                    display_name=app.get("display_name", ""),
                    install_location=app.get("install_location"),
                    publisher=app.get("publisher"),
                )
                self._cache.append(info)
            except Exception:
                continue

    # ---------- публичный интерфейс ----------

    def get_installed_apps(self, skip_wow64: bool = False) -> t.List[AppInfo]:
        """Возвращает список установленных приложений, отсортированный по имени.

        skip_wow64 — игнорировать 32-битную ветку реестра (WOW6432Node).
        """
        if not self._cache or self._cache_skip_wow64 != skip_wow64:
            self._populate_from_registry(skip_wow64=skip_wow64)
            if not self._cache:
                self._read_known_apps()
            self._cache_skip_wow64 = skip_wow64
        return sorted(self._cache, key=lambda app: app.display_name.lower())


# Минимальный набор заранее известных приложений (демо-данные).
# Используется только если реестр недоступен или пуст.
KNOWN_APPS = [
    {
        "display_name": "Adobe Acrobat Reader DC",
        "install_location": r"C:\Program Files\Adobe\Acrobat Reader DC",
        "publisher": "Adobe",
    },
    {
        "display_name": "Some Bloatware App",
        "install_location": None,
        "publisher": "Bloatware Corp",
    },
    {
        "display_name": "WinRAR",
        "install_location": r"C:\Program Files\WinRAR",
        "publisher": "WinRAR",
    },
]
