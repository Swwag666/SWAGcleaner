"""Движок системных твиков — декларативная база + применение со снапшотом.

База (core/tweaks_db.json) — список твиков: id, категория, риск, build-гейты
и наборы операций для включения/выключения. Операции: реестр (set/
delete_value/delete_key/create_key), службы (service), внешние команды из
белого списка (cmd: bcdedit, powercfg, schtasks, DISM и т.п. — без shell).

Отличие от исходного подхода «on/off захардкожены»: перед применением
снимается снапшот ПРЕЖНИХ значений всех затрагиваемых значений и ключей —
откат возвращает реальное прошлое состояние, а не «выключение по базе».

Состояние твика читается из системы: все set-опы совпали со значениями «on»
и delete-цели отсутствуют → «on»; совпали «off» → «off»; иначе «unknown»
(систему меняли мимо нас — честный нейтральный статус).
"""
from __future__ import annotations

import json
import logging
import subprocess
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent / "tweaks_db.json"

# Команды, которые твикам разрешено запускать (только из этого списка,
# без shell: строка режется shlex на argv).
_CMD_WHITELIST = ("bcdedit", "powercfg", "reagentc", "schtasks", "dism",
                  "sfc", "vssadmin", "netsh", "ipconfig", "control")


@dataclass(frozen=True)
class Tweak:
    id: str
    category: str
    name_en: str = ""
    name_ru: str = ""
    risk: str = "low"
    reboot: bool = False
    explorer_restart: bool = False
    min_build: int = 0
    max_build: int = 999999
    params: t.Tuple[t.Dict[str, t.Any], ...] = ()
    note: str = ""
    on: t.Tuple[t.Dict[str, t.Any], ...] = ()
    off: t.Tuple[t.Dict[str, t.Any], ...] = ()


def load_db(path: t.Optional[Path] = None) -> t.List[Tweak]:
    """Загрузить базу твиков из JSON."""
    raw = json.loads((path or _DB_PATH).read_text(encoding="utf-8"))
    out: t.List[Tweak] = []
    for item in raw:
        out.append(Tweak(
            id=str(item["id"]),
            category=str(item.get("category", "misc")),
            name_en=str(item.get("name_en", "")),
            name_ru=str(item.get("name_ru", "")),
            risk=str(item.get("risk", "low")),
            reboot=bool(item.get("reboot", False)),
            explorer_restart=bool(item.get("explorer_restart", False)),
            min_build=int(item.get("min_build", 0)),
            max_build=int(item.get("max_build", 999999)),
            params=tuple(item.get("params", ())),
            note=str(item.get("note", "")),
            on=tuple(item.get("on", ())),
            off=tuple(item.get("off", ())),
        ))
    return out


def current_build() -> int:
    """Билд Windows для гейтов min/max_build."""
    try:
        import sys
        return sys.getwindowsversion().build  # type: ignore[attr-defined]
    except Exception:
        return 0


class RegistryOps:
    """Реестр для твиков: get/set/delete_value/delete_key/create_key.

    Отдельно от startup.WinregRegistry: там строковый set_value, тут типы
    сохраняются честно (dword — числом), плюс нужны delete_key и создание
    пути целиком. Инъектируется: в тестах — словарь.
    """

    def __init__(self) -> None:
        import winreg
        self._winreg = winreg

    def _hive(self, name: str) -> int:
        return getattr(self._winreg, "HKEY_CURRENT_USER" if name == "HKCU"
                       else "HKEY_LOCAL_MACHINE")

    @staticmethod
    def _ctype(type_name: str) -> int:
        import winreg
        return {"dword": winreg.REG_DWORD,
                "sz": winreg.REG_SZ,
                "expand_sz": winreg.REG_EXPAND_SZ,
                "multi_sz": winreg.REG_MULTI_SZ}[type_name]

    def get_value(self, hive: str, path: str, name: str
                  ) -> t.Optional[t.Tuple[t.Any, int]]:
        """(данные, тип) или None — значения/ключа нет."""
        try:
            with self._winreg.OpenKey(self._hive(hive), path, 0,
                                      self._winreg.KEY_READ
                                      | self._winreg.KEY_WOW64_64KEY) as key:
                data, vtype = self._winreg.QueryValueEx(key, name)
                return data, int(vtype)
        except OSError:
            return None

    def key_exists(self, hive: str, path: str) -> bool:
        try:
            with self._winreg.OpenKey(self._hive(hive), path, 0,
                                      self._winreg.KEY_READ
                                      | self._winreg.KEY_WOW64_64KEY):
                return True
        except OSError:
            return False

    def set_value(self, hive: str, path: str, name: str, data: t.Any,
                  type_name: str) -> None:
        # Путь создаём целиком: твики часто пишут в несуществующие Policies.
        with self._winreg.CreateKeyEx(
                self._hive(hive), path, 0,
                self._winreg.KEY_SET_VALUE | self._winreg.KEY_WOW64_64KEY
                | self._winreg.KEY_CREATE_SUB_KEY) as key:
            self._winreg.SetValueEx(key, name, 0, self._ctype(type_name), data)

    def set_raw(self, hive: str, path: str, name: str, data: t.Any,
                vtype: int) -> None:
        """Запись с готовым winreg-типом (для отката снапшота)."""
        with self._winreg.CreateKeyEx(
                self._hive(hive), path, 0,
                self._winreg.KEY_SET_VALUE | self._winreg.KEY_WOW64_64KEY
                | self._winreg.KEY_CREATE_SUB_KEY) as key:
            self._winreg.SetValueEx(key, name, 0, vtype, data)

    def delete_value(self, hive: str, path: str, name: str) -> None:
        try:
            with self._winreg.OpenKey(self._hive(hive), path, 0,
                                      self._winreg.KEY_SET_VALUE
                                      | self._winreg.KEY_WOW64_64KEY) as key:
                self._winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def create_key(self, hive: str, path: str) -> None:
        self._winreg.CreateKeyEx(self._hive(hive), path, 0,
                                 self._winreg.KEY_CREATE_SUB_KEY
                                 | self._winreg.KEY_WOW64_64KEY).Close()

    def delete_key(self, hive: str, path: str) -> None:
        """Удалить ключ с поддеревом (DeleteSubKey в C# молча глотается)."""
        try:
            self._winreg.DeleteKeyEx(self._hive(hive), path,
                                     self._winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            return
        except OSError:
            # Не пуст: рекурсивно снизу вверх.
            with self._winreg.OpenKey(self._hive(hive), path, 0,
                                      self._winreg.KEY_READ
                                      | self._winreg.KEY_WOW64_64KEY) as key:
                subs = []
                i = 0
                while True:
                    try:
                        subs.append(self._winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
            for sub in subs:
                self.delete_key(hive, path + "\\" + sub)
            self._winreg.DeleteKeyEx(self._hive(hive), path,
                                     self._winreg.KEY_WOW64_64KEY)


class TweaksEngine:
    """Применение твиков: снапшот прежнего состояния → операции → журнал."""

    def __init__(self,
                 store: t.Optional[t.Any] = None,
                 registry: t.Optional[t.Any] = None,
                 services: t.Optional[t.Any] = None,
                 runner: t.Optional[t.Callable[..., t.Any]] = None,
                 build: t.Optional[int] = None) -> None:
        self._store = store
        self._reg = registry if registry is not None else RegistryOps()
        self._services = services
        self._runner = runner if runner is not None else subprocess.run
        self._build = build if build is not None else current_build()

    # ---------- чтение ----------

    def available(self, tweaks: t.Sequence[Tweak]) -> t.List[Tweak]:
        """Твики, применимые на этом билде Windows."""
        return [tw for tw in tweaks
                if tw.min_build <= self._build <= tw.max_build]

    def status(self, tweak: Tweak) -> str:
        """on | off | unknown — по фактическому состоянию системы."""
        on = self._ops_match(tweak, tweak.on)
        if on:
            return "on"
        if tweak.off and self._ops_match(tweak, tweak.off):
            return "off"
        return "unknown"

    def _ops_match(self, tweak: Tweak, ops: t.Sequence[t.Dict[str, t.Any]]) -> bool:
        """Все проверяемые операции набора совпадают с системой?"""
        checked = 0
        for op in ops:
            kind = op.get("op")
            if kind == "set":
                checked += 1
                cur = self._reg.get_value(str(op["hive"]), str(op["path"]),
                                          str(op["name"]))
                want = op.get("value")
                if str(op.get("type", "dword")) == "dword":
                    want = int(want)
                if cur is None or cur[0] != want:
                    return False
            elif kind == "delete_value":
                checked += 1
                if self._reg.get_value(str(op["hive"]), str(op["path"]),
                                       str(op["name"])) is not None:
                    return False
            elif kind == "delete_key":
                checked += 1
                if self._reg.key_exists(str(op["hive"]), str(op["path"])):
                    return False
            elif kind == "create_key":
                checked += 1
                if not self._reg.key_exists(str(op["hive"]), str(op["path"])):
                    return False
            # service/cmd состояние не определяем — не участвуют в проверке
        return checked > 0

    # ---------- запись ----------

    def apply(self, tweak: Tweak, enable: bool,
              params: t.Optional[t.Dict[str, str]] = None) -> str:
        """Применить твик; вернуть имя снапшота прежнего состояния."""
        if not (tweak.min_build <= self._build <= tweak.max_build):
            raise ValueError(f"твик не для этого билда Windows: {tweak.id}")
        ops = tweak.on if enable else tweak.off
        if not ops:
            raise ValueError(f"у твика нет операций: {tweak.id}")
        merged = dict(params or {})
        for p in tweak.params:
            merged.setdefault(str(p.get("key", "")), str(p.get("default", "")))
        prev = self._snapshot_state(tweak)
        snapshot = f"tweak-{tweak.id}"
        if self._store is None:
            raise ValueError("нет хранилища бэкапов — твикать нельзя")
        self._store.save(snapshot, {
            "kind": "tweak",
            "tweak": tweak.id,
            "enabled": enable,
            "prev": prev,
        }, kind="tweak_apply")
        try:
            self._run_ops(ops, merged)
        except Exception:
            # Откат к снятому состоянию, снапшот остаётся для диагностики.
            self._restore_prev(prev)
            self._store.remove(snapshot)
            raise
        return snapshot

    def restore(self, snapshot: str) -> str:
        """Откат твика по снапшоту прежних значений."""
        if self._store is None:
            raise ValueError("нет хранилища бэкапов")
        data = self._store.restore(snapshot)
        if not isinstance(data, dict) or data.get("kind") != "tweak":
            raise ValueError(f"снапшот твика не найден или битый: {snapshot}")
        self._restore_prev(data.get("prev", []))
        self._store.remove(snapshot)
        return str(data.get("tweak", ""))

    # ---------- внутреннее ----------

    def _snapshot_state(self, tweak: Tweak) -> t.List[t.Dict[str, t.Any]]:
        """Прежнее состояние всех затрагиваемых значений и ключей."""
        prev: t.List[t.Dict[str, t.Any]] = []
        seen_values: t.Set[t.Tuple[str, str, str]] = set()
        seen_keys: t.Set[t.Tuple[str, str]] = set()
        for op in tuple(tweak.on) + tuple(tweak.off):
            kind = op.get("op")
            hive, path = str(op.get("hive", "")), str(op.get("path", ""))
            if kind == "set" or kind == "delete_value":
                name = str(op.get("name", ""))
                if (hive, path, name) in seen_values:
                    continue
                seen_values.add((hive, path, name))
                cur = self._reg.get_value(hive, path, name)
                prev.append({"what": "value", "hive": hive, "path": path,
                             "name": name,
                             "existed": cur is not None,
                             "data": cur[0] if cur else None,
                             "vtype": cur[1] if cur else 0})
            elif kind in ("delete_key", "create_key"):
                if (hive, path) in seen_keys:
                    continue
                seen_keys.add((hive, path))
                prev.append({"what": "key", "hive": hive, "path": path,
                             "existed": self._reg.key_exists(hive, path)})
        return prev

    def _restore_prev(self, prev: t.Sequence[t.Dict[str, t.Any]]) -> None:
        for item in prev:
            try:
                if item["what"] == "value":
                    if item["existed"]:
                        self._reg.set_raw(item["hive"], item["path"],
                                          item["name"], item["data"],
                                          int(item["vtype"]))
                    else:
                        self._reg.delete_value(item["hive"], item["path"],
                                               item["name"])
                elif item["what"] == "key":
                    if item["existed"]:
                        self._reg.create_key(item["hive"], item["path"])
                    else:
                        self._reg.delete_key(item["hive"], item["path"])
            except OSError as exc:
                log.warning("откат куска твика не удался: %s", exc)

    def _run_ops(self, ops: t.Sequence[t.Dict[str, t.Any]],
                 params: t.Dict[str, str]) -> None:
        for op in ops:
            kind = op.get("op")
            if kind == "set":
                value = op.get("value")
                if isinstance(value, str):
                    for key, val in params.items():
                        value = value.replace("{" + key + "}", val)
                self._reg.set_value(str(op["hive"]), str(op["path"]),
                                    str(op["name"]), value,
                                    str(op.get("type", "dword")))
            elif kind == "delete_value":
                self._reg.delete_value(str(op["hive"]), str(op["path"]),
                                       str(op["name"]))
            elif kind == "delete_key":
                self._reg.delete_key(str(op["hive"]), str(op["path"]))
            elif kind == "create_key":
                self._reg.create_key(str(op["hive"]), str(op["path"]))
            elif kind == "service":
                self._services_ctl().set_start_mode(str(op["name"]),
                                                    str(op["mode"]))
            elif kind == "cmd":
                self._run_cmd(str(op["run"]))
            else:
                raise ValueError(f"неизвестная операция твика: {kind}")

    def _services_ctl(self) -> t.Any:
        if self._services is None:
            from core.services import WindowsServiceController
            self._services = WindowsServiceController()
        return self._services

    def _run_cmd(self, command: str) -> None:
        import shlex
        argv = shlex.split(command, posix=False)
        exe = Path(argv[0]).name.lower().replace(".exe", "")
        if exe not in _CMD_WHITELIST:
            raise ValueError(f"команда твика вне белого списка: {argv[0]}")
        result = self._runner(argv, capture_output=True, timeout=300)
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[:200] \
                if isinstance(result.stderr, bytes) else str(result.stderr)
            raise RuntimeError(f"{argv[0]} rc={result.returncode}: {err}")
