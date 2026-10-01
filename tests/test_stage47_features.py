# -*- coding: utf-8 -*-
"""Промт №27: hidepart, таймер, gpedit, winget, активация, пресеты, полигон."""
from __future__ import annotations

import threading

import pytest


class TestHidePart:
    def test_letters_to_mask(self):
        from core.hidepart import letters_to_mask
        assert letters_to_mask(["A"]) == 1
        assert letters_to_mask(["D"]) == 8
        assert letters_to_mask(["A", "D", "Z"]) == 1 + 8 + 33554432
        assert letters_to_mask([]) == 0

    def test_mask_to_letters_roundtrip(self):
        from core.hidepart import letters_to_mask, mask_to_letters
        letters = ["A", "D", "E", "X"]
        assert mask_to_letters(letters_to_mask(letters)) == letters
        assert mask_to_letters(0) == []

    def test_banned_letters_raise(self):
        from core.hidepart import letters_to_mask
        with pytest.raises(ValueError):
            letters_to_mask(["C"])
        with pytest.raises(ValueError):
            letters_to_mask(["b"])


class TestShutdownTimer:
    class _R:
        returncode = 0
        stdout = b""
        stderr = b""

    def test_schedule_and_cancel(self):
        from core.power import ShutdownTimer
        calls = []
        now = [1000.0]

        def runner(argv, **kw):
            calls.append(argv)
            return self._R()

        timer = ShutdownTimer(runner=runner, clock=lambda: now[0])
        assert timer.schedule(30) == 1800
        assert calls[0] == ["shutdown", "/s", "/t", "1800"]
        assert timer.remaining() == 1800
        now[0] += 600
        assert timer.remaining() == 1200
        timer.cancel()
        assert calls[1] == ["shutdown", "/a"]
        assert timer.remaining() is None

    def test_bounds(self):
        from core.power import ShutdownTimer
        timer = ShutdownTimer(runner=lambda *a, **k: self._R())
        with pytest.raises(ValueError):
            timer.schedule(0)
        with pytest.raises(ValueError):
            timer.schedule(99999)


class TestGpedit:
    class _R:
        returncode = 0

    def test_packages_and_install(self, tmp_path, monkeypatch):
        from core import gpedit
        pkg_dir = tmp_path / "servicing" / "Packages"
        pkg_dir.mkdir(parents=True)
        for name in ("Microsoft-Windows-GroupPolicy-A.mum",
                     "Microsoft-Windows-GroupPolicy-B.mum",
                     "unrelated.mum"):
            (pkg_dir / name).write_text("x")
        monkeypatch.setattr(gpedit, "is_home_edition", lambda: True)
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return self._R()

        res = gpedit.install_gpedit(runner=runner, system_root=tmp_path)
        assert res["total"] == 2 and res["ok"] == 2 and not res["failed"]
        assert all(c[0] == "dism" for c in calls)

    def test_skips_non_home(self, monkeypatch):
        from core import gpedit
        monkeypatch.setattr(gpedit, "is_home_edition", lambda: False)
        res = gpedit.install_gpedit(runner=lambda *a, **k: self._R())
        assert res["skipped"] is True


class TestAppInstall:
    class _R:
        def __init__(self, rc=0, out=b""):
            self.returncode = rc
            self.stdout = out
            self.stderr = b""

    def test_install_argv_and_ok(self):
        from core.appinstall import AppInstaller
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return self._R(0)

        inst = AppInstaller(runner=runner, winget_path="winget-stub")
        res = inst.install("Google.Chrome")
        assert res["ok"] and res["rc"] == 0
        argv = calls[0]
        assert argv[:3] == ["winget-stub", "install", "--id"]
        assert "--accept-package-agreements" in argv
        assert "--disable-interactivity" in argv

    def test_already_installed_is_ok(self):
        from core.appinstall import AppInstaller
        inst = AppInstaller(runner=lambda *a, **k: self._R(-1978335189),
                            winget_path="winget-stub")
        res = inst.install("Mozilla.Firefox")
        assert res["ok"] and res["already"]

    def test_cancel_between_packages(self):
        from core.appinstall import AppInstaller
        cancel = threading.Event()
        seen = []

        def runner(argv, **kw):
            seen.append(argv)
            cancel.set()  # после первого пакета отмена
            return self._R(0)

        inst = AppInstaller(runner=runner, winget_path="winget-stub")
        results = inst.install_many(["A.A", "B.B", "C.C"], cancel=cancel)
        assert len(results) == 1 and len(seen) == 1

    def test_catalog_sane(self):
        from core.appinstall import CATALOG
        ids = [e.winget_id for e in CATALOG]
        assert len(ids) == len(set(ids))
        assert all("." in wid for wid in ids)


class TestActivation:
    def test_classify(self):
        from core.activation import _classify
        assert _classify("... permanently activated ...") == "activated"
        assert _classify("Activation is not required") == "already"
        assert _classify("Evaluation editions cannot be activated") == "eval"
        assert _classify("Not Connected") == "offline"
        assert _classify("random noise") == "error"

    def test_missing_script(self, tmp_path):
        from core.activation import Activator
        act = Activator(runner=lambda *a, **k: None, script_dir=tmp_path)
        res = act.activate_windows()
        assert res["status"] == "missing_script"

    def test_run_script_ok(self, tmp_path):
        from core.activation import Activator
        script = tmp_path / "hwid_activation.cmd"
        script.write_text("@echo off")
        seen = {}

        class R:
            returncode = 0
            stdout = b"The machine is permanently activated."
            stderr = b""

        def runner(argv, **kw):
            seen.update(kw)
            return R()

        act = Activator(runner=runner, script_dir=tmp_path)
        res = act.activate_windows()
        assert res["ok"] and res["status"] == "activated"
        import subprocess
        assert seen.get("stdin") is subprocess.DEVNULL

    def test_kms_unknown_server_refused(self):
        from core.activation import Activator
        act = Activator(runner=lambda *a, **k: None)
        res = act.kms_activate("evil.example.com")
        assert res["status"] == "unknown_server"


class TestLabFixes:
    """Фиксы по результатам полигона на виртуалке."""

    def test_cmd_quotes_stripped(self, tmp_path):
        from core.backup import BackupStore
        from core.tweaks import Tweak, TweaksEngine
        from test_tweaks_stage45 import _FakeTweakRegistry

        calls = []

        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        engine = TweaksEngine(store=BackupStore(tmp_path / "b"),
                              registry=_FakeTweakRegistry(), build=22631,
                              runner=lambda a, **k: (calls.append(a), R())[1])
        tw = Tweak(id="x.bcd", category="sysrec",
                   on=[{"op": "cmd",
                        "run": 'bcdedit /set "{current}" bootmenupolicy legacy'}])
        engine.apply(tw, True)
        assert calls[0] == ["bcdedit", "/set", "{current}",
                            "bootmenupolicy", "legacy"]

    def test_delete_glob(self, tmp_path):
        import os
        from core.backup import BackupStore
        from core.tweaks import Tweak, TweaksEngine
        from test_tweaks_stage45 import _FakeTweakRegistry

        target = tmp_path / "cache"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        (target / "b.tmp").write_text("x")
        os.environ["SWAG_TEST_DIR"] = str(tmp_path)
        engine = TweaksEngine(store=BackupStore(tmp_path / "b"),
                              registry=_FakeTweakRegistry(), build=22631)
        tw = Tweak(id="x.clean", category="winupdate",
                   on=[{"op": "delete_glob",
                        "path": "%SWAG_TEST_DIR%\\cache\\*"}])
        engine.apply(tw, True)
        assert not list(target.iterdir())

    def test_widgets_tweak_shape(self):
        from core.tweaks import load_db
        tw = next(t for t in load_db()
                  if t.id == "personal.hide_taskview_widgets")
        names = [o.get("name") for o in tw.on]
        # TaskbarDa драйверно-защищён, политика Dsh ACL-системной (24H2):
        # рабочий путь - две HKCU-кнопки.
        assert "TaskbarDa" not in names
        assert "AllowNewsAndInterests" not in names
        assert "ShowTaskViewButton" in names and "TaskbarMn" in names
        assert tw.off  # стал обратимым


class TestPresets:
    def test_load_and_apply(self, tmp_path):
        from core.backup import BackupStore
        from core.tweaks import TweaksEngine, load_db, load_presets
        from test_tweaks_stage45 import _FakeTweakRegistry

        presets = load_presets()
        assert len(presets) >= 3
        db = {tw.id: tw for tw in load_db()}
        for preset in presets:
            for tid in preset.tweaks:
                assert tid in db, f"{preset.id}: {tid} нет в базе"

        engine = TweaksEngine(store=BackupStore(tmp_path / "b"),
                              registry=_FakeTweakRegistry(), build=22631)
        preset = next(p for p in presets if p.id == "privacy")
        results = engine.apply_preset(preset, list(db.values()))
        assert all(r["ok"] for r in results)
        assert all("snapshot" in r for r in results)

    def test_preset_continues_past_failure(self, tmp_path):
        from core.backup import BackupStore
        from core.tweaks import Tweak, TweaksEngine, TweakPreset
        from test_tweaks_stage45 import _FakeTweakRegistry

        engine = TweaksEngine(store=BackupStore(tmp_path / "b"),
                              registry=_FakeTweakRegistry(), build=22631)
        good = Tweak(id="a.good", category="x",
                     on=[{"op": "set", "hive": "HKCU", "path": "P",
                          "name": "A", "type": "dword", "value": 1}])
        bad = Tweak(id="b.bad", category="x",
                    on=[{"op": "delete_key", "hive": "HKCU", "path": "P"},
                        {"op": "cmd", "run": "notwhitelisted x"}])
        preset = TweakPreset(id="p", name_en="", name_ru="", desc_en="",
                             desc_ru="", tweaks=("a.good", "b.bad", "c.miss"))
        results = engine.apply_preset(preset, [good, bad])
        assert results[0]["ok"] and not results[1]["ok"]
        assert results[2]["error"] == "нет в базе"


class TestTweakLab:
    def test_report_shape(self, tmp_path, monkeypatch):
        import json
        from core.backup import BackupStore
        import core.tweaklab as lab_mod
        from core.tweaks import Tweak, TweaksEngine
        from test_tweaks_stage45 import _FakeTweakRegistry

        tweaks = [
            Tweak(id="x.one", category="explorer",
                  on=[{"op": "set", "hive": "HKCU", "path": "P",
                       "name": "A", "type": "dword", "value": 1}],
                  off=[{"op": "set", "hive": "HKCU", "path": "P",
                        "name": "A", "type": "dword", "value": 0}]),
            Tweak(id="x.slow", category="sysrec", on=[], off=[]),
            Tweak(id="x.bad", category="explorer",
                  on=[{"op": "cmd", "run": "rundll32 x"}]),
        ]
        import core.tweaks as tweaks_mod
        # run_lab импортирует load_db внутри себя из core.tweaks — патчим там.
        monkeypatch.setattr(tweaks_mod, "load_db", lambda: tweaks)
        # SLOW_IDS содержит настоящие id; подменяем для теста
        monkeypatch.setattr(lab_mod, "SLOW_IDS", frozenset({"x.slow"}))

        engine = TweaksEngine(store=BackupStore(tmp_path / "b"),
                              registry=_FakeTweakRegistry(), build=22631)
        monkeypatch.setattr(TweaksEngine, "available", lambda self, tw: tw)
        out = tmp_path / "report.json"
        report = lab_mod.run_lab(out, engine=engine, log=None)
        by_id = {r["id"]: r for r in report["results"]}
        assert by_id["x.one"]["verdict"] == "ok"
        assert by_id["x.one"]["status_after"] == "on"
        assert by_id["x.slow"]["verdict"] == "skipped_slow"
        assert by_id["x.bad"]["verdict"] == "fail"
        disk = json.loads(out.read_text(encoding="utf-8"))
        assert disk["total"] == 3 and disk["failed"] == ["x.bad"]
