"""Зависимости и рантаймы: каталог, детект «уже стоит», группы (этап 6).

Три группы по жизни: «всем» (базовые рантаймы, без которых половина софта
не стартует), «геймеру» (VC++ всех разрядностей + DirectX End-User Runtime),
«прогеру» (SDK, JDK, Node, Rust, Build Tools). Установка - тем же winget,
что и каталог приложений: id сверены с живым источником, детект - реестр
(VC++, .NET, Uninstall-ветки), а не «winget list» на каждый чих.

На не-Windows детект честно возвращает None: модуль импортируется везде,
работает там, где есть реестр.
"""
from __future__ import annotations

import typing as t
from dataclasses import dataclass

try:  # noqa: SIM105 - на Linux/win-отсутствии модуль всё равно импортируется
    import winreg
except ImportError:  # pragma: no cover - не-Windows
    winreg = None  # type: ignore[assignment]

GROUPS: t.Tuple[str, ...] = ("base", "gamer", "dev")


@dataclass(frozen=True)
class RedistEntry:
    rid: str
    winget_id: str
    name_ru: str
    name_en: str
    groups: t.Tuple[str, ...]
    detect: t.Optional[t.Dict[str, t.Any]]  # None = детекта нет, ставим втёмую


REDISTS: t.Tuple[RedistEntry, ...] = (
    RedistEntry("vc2015_x64", "Microsoft.VCRedist.2015+.x64",
                "Visual C++ 2015-2022 (x64)", "Visual C++ 2015-2022 (x64)",
                ("base", "gamer", "dev"),
                {"kind": "reg_value",
                 "path": r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
                 "name": "Installed", "expect": 1}),
    RedistEntry("vc2015_x86", "Microsoft.VCRedist.2015+.x86",
                "Visual C++ 2015-2022 (x86)", "Visual C++ 2015-2022 (x86)",
                ("gamer", "dev"),
                {"kind": "reg_value",
                 "path": r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86",
                 "name": "Installed", "expect": 1}),
    RedistEntry("vc2013_x64", "Microsoft.VCRedist.2013.x64",
                "Visual C++ 2013 (x64)", "Visual C++ 2013 (x64)",
                ("gamer",),
                {"kind": "reg_value",
                 "path": r"SOFTWARE\Microsoft\VisualStudio\12.0\VC\Runtimes\x64",
                 "name": "Installed", "expect": 1}),
    RedistEntry("directx", "Microsoft.DirectX",
                "DirectX End-User Runtime (июнь 2010)",
                "DirectX End-User Runtime (June 2010)",
                ("gamer",), None),
    RedistEntry("dotnet_desktop8", "Microsoft.DotNet.DesktopRuntime.8",
                ".NET Desktop Runtime 8", ".NET Desktop Runtime 8",
                ("base", "gamer", "dev"),
                {"kind": "uninstall_contains", "substrs": ("Windows Desktop Runtime",),
                 "versions": ("8.",)}),
    RedistEntry("dotnet_sdk9", "Microsoft.DotNet.SDK.9",
                ".NET SDK 9 (C#)", ".NET SDK 9 (C#)",
                ("dev",),
                {"kind": "uninstall_contains", "substrs": (".NET SDK",),
                 "versions": ("9.",)}),
    RedistEntry("jdk21", "EclipseAdoptium.Temurin.21.JDK",
                "JDK 21 (Temurin)", "JDK 21 (Temurin)",
                ("dev",),
                {"kind": "uninstall_contains", "substrs": ("Temurin",),
                 "versions": ("21.",)}),
    RedistEntry("node_lts", "OpenJS.NodeJS.LTS",
                "Node.js LTS", "Node.js LTS",
                ("dev",),
                {"kind": "uninstall_contains", "substrs": ("Node.js",),
                 "versions": None}),
    RedistEntry("rustup", "Rustlang.Rustup",
                "Rustup (тулчейн Rust)", "Rustup (Rust toolchain)",
                ("dev",),
                {"kind": "uninstall_contains", "substrs": ("Rustup",),
                 "versions": None}),
    RedistEntry("buildtools", "Microsoft.VisualStudio.2022.BuildTools",
                "VS 2022 Build Tools (C++/C#)", "VS 2022 Build Tools (C++/C#)",
                ("dev",),
                {"kind": "uninstall_contains",
                 "substrs": ("BuildTools 2022", "Build Tools 2022"),
                 "versions": None}),
)

_BY_ID: t.Dict[str, RedistEntry] = {e.rid: e for e in REDISTS}


def entry(rid: str) -> RedistEntry:
    return _BY_ID[rid]


def group_ids(group: str) -> t.List[str]:
    """id зависимостей группы: base | gamer | dev."""
    return [e.rid for e in REDISTS if group in e.groups]


def winget_ids(rids: t.Sequence[str]) -> t.List[str]:
    return [_BY_ID[r].winget_id for r in rids if r in _BY_ID]


# ---------- детект ----------

def _uninstall_names() -> t.List[t.Tuple[str, str]]:
    """(DisplayName, DisplayVersion) из обеих разрядностей Uninstall."""
    out: t.List[t.Tuple[str, str]] = []
    if winreg is None:
        return out
    bases = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 64),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 32),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 64),
    )
    for hive, path, bits in bases:
        try:
            if bits == 32:
                root = winreg.OpenKey(hive, path, 0,
                                      winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
            else:
                root = winreg.OpenKey(hive, path, 0,
                                      winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = winreg.QueryValueEx(key, "DisplayName")[0]
                        try:
                            ver = str(winreg.QueryValueEx(key,
                                                          "DisplayVersion")[0])
                        except OSError:
                            ver = ""
                    out.append((str(name), ver))
                except OSError:
                    continue
    return out


def detect(entry_: RedistEntry) -> t.Optional[bool]:
    """True - стоит, False - не стоит, None - детекта нет или среды нет."""
    rule = entry_.detect
    if rule is None or winreg is None:
        return None
    kind = rule["kind"]
    if kind == "reg_value":
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rule["path"], 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value = winreg.QueryValueEx(key, rule["name"])[0]
            return int(value) == int(rule["expect"])
        except OSError:
            return False
        except (TypeError, ValueError):
            return False
    if kind == "uninstall_contains":
        names = _uninstall_names()
        for name, ver in names:
            if any(s in name for s in rule["substrs"]):
                versions = rule.get("versions")
                if not versions or any(ver.startswith(v) for v in versions):
                    return True
        return False
    return None


def detect_all() -> t.Dict[str, t.Optional[bool]]:
    return {e.rid: detect(e) for e in REDISTS}
