# -*- coding: utf-8 -*-
"""Этап 4.5: движок системных твиков (core/tweaks.py) и маршрутизация.

Реестр фейковый (словарь), команды — фейк-раннером; живая машина в тестах
не трогается.
"""
from __future__ import annotations

import json

import pytest


class _FakeTweakRegistry:
    """Реестр-словарь под движок твиков: значения и существование ключей."""

    def __init__(self):
        self.values = {}  # (hive, path, name) -> (data, vtype)
        self.keys = set()  # (hive, path)

    def get_value(self, hive, path, name):
        return self.values.get((hive, path, name))

    def key_exists(self, hive, path):
        if (hive, path) in self.keys:
            return True
        return any(k[0] == hive and k[1] == path for k in self.values)

    def set_value(self, hive, path, name, data, type_name):
        self.keys.add((hive, path))
        self.values[(hive, path, name)] = (
            data, {"dword": 4, "sz": 1, "expand_sz": 2, "multi_sz": 7}[type_name])

    def set_raw(self, hive, path, name, data, vtype):
        self.keys.add((hive, path))
        self.values[(hive, path, name)] = (data, vtype)

    def delete_value(self, hive, path, name):
        self.values.pop((hive, path, name), None)

    def create_key(self, hive, path):
        self.keys.add((hive, path))

    def delete_key(self, hive, path):
        self.keys.discard((hive, path))
        for k in [k for k in self.values
                  if k[0] == hive and k[1].startswith(path)]:
            del self.values[k]


class _FakeRunner:
    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc

    def __call__(self, argv, **kw):
        self.calls.append(argv)

        class _R:
            returncode = self.rc
            stderr = b""
        r = _R()
        r.returncode = self.rc
        return r


def _tweak(**kw):
    from core.tweaks import Tweak
    base = dict(id="test.demo", category="explorer", on=[], off=[])
    base.update(kw)
    return Tweak(**base)


class TestStage45TweaksEngine:
    def _engine(self, tmp_path):
        from core.backup import BackupStore
        from core.tweaks import TweaksEngine
        store = BackupStore(tmp_path / "backups")
        reg = _FakeTweakRegistry()
        runner = _FakeRunner()
        eng = TweaksEngine(store=store, registry=reg, runner=runner,
                           build=22631)
        return eng, reg, runner, store

    def test_apply_snapshots_previous_value(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        reg.set_raw("HKCU", r"Software\Demo", "Flag", 1, 4)
        tw = _tweak(on=[{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                         "name": "Flag", "type": "dword", "value": 0}],
                    off=[{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                          "name": "Flag", "type": "dword", "value": 1}])
        snap = eng.apply(tw, True)
        assert reg.values[("HKCU", r"Software\Demo", "Flag")] == (0, 4)
        data = store.restore(snap)
        assert data["kind"] == "tweak" and data["prev"][0]["data"] == 1
        store.save(snap, data, kind=data["kind"])
        eng.restore(snap)
        assert reg.values[("HKCU", r"Software\Demo", "Flag")] == (1, 4)

    def test_restore_removes_value_that_did_not_exist(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        tw = _tweak(on=[{"op": "set", "hive": "HKLM",
                         "path": r"SOFTWARE\Policies\Demo",
                         "name": "New", "type": "dword", "value": 1}])
        snap = eng.apply(tw, True)
        assert ("HKLM", r"SOFTWARE\Policies\Demo", "New") in reg.values
        eng.restore(snap)
        assert ("HKLM", r"SOFTWARE\Policies\Demo", "New") not in reg.values

    def test_delete_key_and_restore(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        reg.create_key("HKLM", r"SOFTWARE\Demo\{GUID}")
        tw = _tweak(on=[{"op": "delete_key", "hive": "HKLM",
                         "path": r"SOFTWARE\Demo\{GUID}"}],
                    off=[{"op": "create_key", "hive": "HKLM",
                          "path": r"SOFTWARE\Demo\{GUID}"}])
        assert eng.status(tw) == "off"
        eng.apply(tw, True)
        assert not reg.key_exists("HKLM", r"SOFTWARE\Demo\{GUID}")
        assert eng.status(tw) == "on"
        eng.restore(f"tweak-{tw.id}")
        assert reg.key_exists("HKLM", r"SOFTWARE\Demo\{GUID}")

    def test_status_unknown_on_mixed_state(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        reg.set_raw("HKCU", r"Software\Demo", "Flag", 7, 4)
        tw = _tweak(on=[{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                         "name": "Flag", "type": "dword", "value": 1}],
                    off=[{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                          "name": "Flag", "type": "dword", "value": 0}])
        assert eng.status(tw) == "unknown"

    def test_build_gate_refuses(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        tw = _tweak(min_build=30000,
                    on=[{"op": "set", "hive": "HKCU", "path": "X",
                         "name": "Y", "type": "dword", "value": 1}])
        with pytest.raises(ValueError):
            eng.apply(tw, True)
        avail = eng.available([tw])
        assert avail == []

    def test_cmd_whitelist(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        good = _tweak(on=[{"op": "cmd", "run": "powercfg /hibernate off"}])
        eng.apply(good, True)
        assert runner.calls[0][0] == "powercfg"
        bad = _tweak(id="test.bad",
                     on=[{"op": "cmd", "run": "rundll32 something"}])
        with pytest.raises(ValueError):
            eng.apply(bad, True)

    def test_params_defaults_and_substitution(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        tw = _tweak(params=[{"key": "folder_name", "default": "New File"}],
                    on=[{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                         "name": "Tpl", "type": "sz",
                         "value": "{folder_name}"}])
        eng.apply(tw, True)
        assert reg.values[("HKCU", r"Software\Demo", "Tpl")] == ("New File", 1)
        eng.apply(tw, True, {"folder_name": "Папка"})
        assert reg.values[("HKCU", r"Software\Demo", "Tpl")] == ("Папка", 1)

    def test_failed_apply_rolls_back(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        reg.set_raw("HKCU", r"Software\Demo", "Flag", 5, 4)
        eng._runner = _FakeRunner(rc=2)
        tw = _tweak(on=[{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                         "name": "Flag", "type": "dword", "value": 9},
                        {"op": "cmd", "run": "sfc /scannow"}])
        with pytest.raises(RuntimeError):
            eng.apply(tw, True)
        assert reg.values[("HKCU", r"Software\Demo", "Flag")] == (5, 4)

    def test_multi_sz_roundtrip(self, tmp_path):
        eng, reg, runner, store = self._engine(tmp_path)
        reg.set_raw("HKLM", r"SYSTEM\Demo", "PagingFiles",
                    ["?:\\pagefile.sys"], 7)
        tw = _tweak(on=[{"op": "set", "hive": "HKLM", "path": r"SYSTEM\Demo",
                         "name": "PagingFiles", "type": "multi_sz",
                         "value": []}])
        snap = eng.apply(tw, True)
        assert reg.values[("HKLM", r"SYSTEM\Demo", "PagingFiles")] == ([], 7)
        eng.restore(snap)
        assert reg.values[("HKLM", r"SYSTEM\Demo", "PagingFiles")] == \
            (["?:\\pagefile.sys"], 7)

    def test_db_loads_and_ids_unique(self):
        from core.tweaks import load_db
        tweaks = load_db()
        assert len(tweaks) >= 60
        ids = [tw.id for tw in tweaks]
        assert len(set(ids)) == len(ids)
        for tw in tweaks:
            assert tw.on, f"у {tw.id} пустой on"
            for op in tuple(tw.on) + tuple(tw.off):
                assert op.get("op") in ("set", "delete_value", "delete_key",
                                        "create_key", "service", "cmd")


class TestStage45ExecutorTweaks:
    def test_tweak_apply_and_restore_routing(self, tmp_path, monkeypatch):
        from core.backup import BackupStore
        from core.executor import Executor
        from core.tweaks import TweaksEngine

        db = tmp_path / "db.json"
        db.write_text(json.dumps([{
            "id": "demo.flag", "category": "explorer",
            "on": [{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                    "name": "Flag", "type": "dword", "value": 1}],
            "off": [{"op": "set", "hive": "HKCU", "path": r"Software\Demo",
                     "name": "Flag", "type": "dword", "value": 0}]}]),
            encoding="utf-8")
        import core.tweaks as tweaks_mod
        monkeypatch.setattr(tweaks_mod, "_DB_PATH", db)

        store = BackupStore(tmp_path / "backups")
        reg = _FakeTweakRegistry()
        engine = TweaksEngine(store=store, registry=reg, build=22631)
        ex = Executor(store=store, tweaks_engine=engine)
        out = ex.execute_actions([{"type": "tweak_apply", "id": "demo.flag",
                                   "enable": True}])
        assert out[0].success and out[0].snapshot
        assert reg.values[("HKCU", r"Software\Demo", "Flag")] == (1, 4)
        back = ex.execute_actions([{"type": "backup_restore",
                                    "snapshot": out[0].snapshot}])
        assert back[0].success
        assert ("HKCU", r"Software\Demo", "Flag") not in reg.values
