"""Провайдер установленных приложений для Windows.

Позволяет получать список установленных приложений, читать информацию
из реестра. В начальной итерации Read-only + основная работа с реестром.
"""
import typing as t
from core.models import AppInfo
from core.ports import InstalledProvider


class WindowsInstalledProvider(InstalledProvider):
    """Провайдер установленных приложений Windows через реестр.

    Читает ключи uninstall из HKLM/HKCU. В начальной итерации
    только read-only, возвращая известные предзаполненные данные.
    """

    # Реестровые пути для чтения установленных программ
    REGISTRY_UNINSTALL_PATHS = {
        "HKLM": (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\({GUID})",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{{PRODUCT-CODE}}",
        ),
        "HKCU": (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    }

    def __init__(
        self,
        known_apps: t.List[t.Dict[str, t.Any]] | None = None,
    ) -> None:
        self._known_apps = known_apps or []
        self._cache: t.List[AppInfo] = []

    def _populate_from_registry(self) -> None:
        """Чтение реестра — реальная реализация будет использовать winreg.

        В начальной итерации пропускаем реальное чтение и работаем с заглушками.
        """
        # Здесь должно быть чтение через winreg
        self._cache = []

    def _read_known_apps(self) -> None:
        """Чтение предзаполненных приложений."""
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

    def get_installed_apps(self, skip_wow64: bool = False) -> t.List[AppInfo]:
        """Возвращает список известных установленных приложений.

        skip_wow64 — игнорировать wow64-ключи.
        """
        # Кэш обновляется при первом вызове
        if not self._cache:
            self._read_known_apps()
        return list(self._cache)


# Минимальный набор заранее известных приложений для демо
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
