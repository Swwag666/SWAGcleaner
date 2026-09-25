# -*- coding: utf-8 -*-
"""Этапы 6/7: зависимости (детект, группы) и конфиги системы (файл, применение)."""
from __future__ import annotations

import json
import threading
import typing as t


class _FakeKey:
    def __init__(self, values: t.Dict[str, t.Any]) -> None:
        self._values = values

    def __enter__(self) -> "_FakeKey":
        return self

    def __exit__(self, *exc: t.Any) -> bool:
        return False


class _FakeWinreg:
    """Мини-реестр: хватает на reg_value-детект зависимостей."""

    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_WOW64_64KEY = 2
    KEY_WOW64_32KEY = 4

    def __init__(self, values: t.Dict[str, t.Any]) -> None:
        self._values = values

    def OpenKey(self, hive: t.Any, path: str, *_a: t.Any, **_kw: t.Any) -> _FakeKey:
        if path not in self._values:
            raise OSError(2, "no key")
        return _FakeKey(self._values[path])

    def QueryValueEx(self, key: _FakeKey, name: str) -> t.Tuple[t.Any, int]:
        if name not in key._values:
            raise OSError(2, "no value")
        return (key._values[name], 4)


class TestRedists:
    def test_groups_cover_catalog(self) -> None:
        from core.redists import GROUPS, REDISTS, group_ids
        for group in GROUPS:
            ids = group_ids(group)
            assert ids and all(i in {e.rid for e in REDISTS} for i in ids)
        assert "vc2015_x64" in group_ids("base")
        assert "directx" in group_ids("gamer")
        assert "dotnet_sdk9" in group_ids("dev")

    def test_winget_ids_mapping(self) -> None:
        from core.redists import winget_ids
        ids = winget_ids(["vc2015_x64", "directx", "nope"])
        assert ids == ["Microsoft.VCRedist.2015+.x64", "Microsoft.DirectX"]

    def test_detect_reg_value(self, monkeypatch: t.Any) -> None:
        import core.redists as redists
        fake = _FakeWinreg({
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64":
                {"Installed": 1},
        })
        monkeypatch.setattr(redists, "winreg", fake)
        assert redists.detect(redists.entry("vc2015_x64")) is True
        assert redists.detect(redists.entry("vc2015_x86")) is False

    def test_detect_uninstall(self, monkeypatch: t.Any) -> None:
        import core.redists as redists
        monkeypatch.setattr(redists, "winreg", _FakeWinreg({}))
        monkeypatch.setattr(
            redists, "_uninstall_names",
            lambda: [("Microsoft Windows Desktop Runtime - 8.0.31 (x64)",
                      "8.0.31"), ("Node.js", "24.19.0")])
        assert redists.detect(redists.entry("dotnet_desktop8")) is True
        assert redists.detect(redists.entry("dotnet_sdk9")) is False
        assert redists.detect(redists.entry("node_lts")) is True

    def test_detect_none_without_rule(self, monkeypatch: t.Any) -> None:
        import core.redists as redists
        monkeypatch.setattr(redists, "winreg", _FakeWinreg({}))
        assert redists.detect(redists.entry("directx")) is None

    def test_detect_all_keys(self, monkeypatch: t.Any) -> None:
        import core.redists as redists
        monkeypatch.setattr(redists, "winreg", None)
        status = redists.detect_all()
        assert set(status) == {e.rid for e in redists.REDISTS}
        assert all(v is None for v in status.values())


class TestInstalledIds:
    def test_parses_winget_table(self) -> None:
        from core.appinstall import AppInstaller

        class _Res:
            returncode = 0
            stdout = (
                "Name Id Version\n"
                "------------------------\n"
                "7-Zip 7zip.7zip 24.08\n"
                "Google Chrome Google.Chrome 131.0\n"
            ).encode("utf-8")

        installer = AppInstaller(runner=lambda *a, **kw: _Res())
        assert installer.installed_ids() == {"7zip.7zip", "Google.Chrome"}

    def test_timeout_is_empty(self) -> None:
        import subprocess

        from core.appinstall import AppInstaller

        def boom(*_a: t.Any, **_kw: t.Any) -> t.NoReturn:
            raise subprocess.TimeoutExpired("winget", 1)

        assert AppInstaller(runner=boom).installed_ids() == set()


class TestSysconfigFile:
    def _cfg(self) -> t.Dict[str, t.Any]:
        from core.sysconfig import build_config
        return build_config("Боевой", [{"id": "a.b", "params": {"x": "1"}}],
                            ["7zip.7zip"], ["vc2015_x64"], note="тест",
                            build=26200)

    def test_roundtrip(self, tmp_path: t.Any, monkeypatch: t.Any) -> None:
        import ai.config as cfg
        from core import sysconfig
        monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
        cfg_in = self._cfg()
        path = sysconfig.save(cfg_in)
        assert path.exists()
        back = sysconfig.load("Боевой")
        assert back["tweaks"] == [{"id": "a.b", "params": {"x": "1"}}]
        assert back["apps"] == ["7zip.7zip"]
        assert back["redists"] == ["vc2015_x64"]
        listing = sysconfig.list_configs()
        assert listing and listing[0]["name"] == "Боевой"
        assert listing[0]["tweaks"] == 1

        dest = tmp_path / "out.json"
        sysconfig.export("Боевой", dest)
        imported = sysconfig.import_config(dest)
        assert imported["name"] == "Боевой"
        assert sysconfig.delete("Боевой") is True
        assert sysconfig.delete("Боевой") is False

    def test_validate_rejects_garbage(self, tmp_path: t.Any,
                                      monkeypatch: t.Any) -> None:
        import ai.config as cfg
        from core.sysconfig import ConfigError, validate
        monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
        for bad in ({"version": 2, "tweaks": []},
                    {"version": 1},
                    {"version": 1, "tweaks": [{"params": {}}]},
                    {"version": 1, "tweaks": [], "apps": [1]}):
            try:
                validate(bad)
                raise AssertionError(f"пропустило мусор: {bad}")
            except ConfigError:
                pass

    def test_apply_report_and_cancel(self) -> None:
        from core.sysconfig import apply_config
        cfg = self._cfg()
        seen: t.List[str] = []

        def apply_tweak(tid: str, params: t.Dict[str, t.Any]) -> bool:
            seen.append(tid)
            if tid == "a.b":
                raise RuntimeError("реестр закрыт")
            return True

        cancel = threading.Event()
        cancel.set()
        report = apply_config(
            cfg, apply_tweak,
            lambda ids: [{"id": i, "ok": True} for i in ids],
            lambda rids: [{"id": r, "ok": True} for r in rids],
            known_tweaks={"a.b"}, cancel=cancel)
        assert report["fail"] == 3  # твик упал, приложения и зависимости отменены
        assert report["ok"] == 0
        assert report["cancelled"] is True

    def test_apply_unknown_tweak(self) -> None:
        from core.sysconfig import apply_config
        cfg = self._cfg()
        report = apply_config(
            cfg, lambda tid, params: True,
            lambda ids: [{"id": i, "ok": True} for i in ids],
            lambda rids: [{"id": r, "ok": True} for r in rids],
            known_tweaks=set())
        assert report["tweaks"][0]["ok"] is False
        assert "базе" in report["tweaks"][0]["error"]
        assert report["ok"] == 2  # приложения и зависимости доехали


class TestSessionConfigs:
    def _session(self, tmp_path: t.Any, monkeypatch: t.Any) -> t.Any:
        import ai.config as cfg
        from core.swagscan import SwagscanClient
        from ui.session import Session
        monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)

        class _FakeClient(SwagscanClient):
            def __init__(self) -> None:
                pass

            def available(self) -> bool:
                return False

        return Session(client=_FakeClient())

    def test_export_import_delete(self, qapp: t.Any, tmp_path: t.Any,
                                  monkeypatch: t.Any) -> None:
        from core import sysconfig
        session = self._session(tmp_path, monkeypatch)
        sysconfig.save(sysconfig.build_config(
            "Перенос", [{"id": "x.y", "params": {}}], [], []))
        dest = tmp_path / "export.json"
        session.config_export("Перенос", str(dest))
        assert dest.exists()
        sysconfig.delete("Перенос")
        cfg = session.config_import(str(dest))
        assert cfg["name"] == "Перенос"
        assert [c["name"] for c in session.config_list()] == ["Перенос"]
        meta = session.config_meta("Перенос")
        assert meta["tweaks"][0]["id"] == "x.y"
        assert session.config_delete("Перенос") is True


class TestTweaksTabSections:
    def _page(self) -> t.Any:
        from ui.tabs import TweaksTab
        page = TweaksTab()
        page.set_tweaks([], [], [], sys_tweaks=[{
            "id": "security.disable_firewall", "category": "security",
            "name_ru": "Файрвол", "name_en": "Firewall", "risk": "high",
            "reboot": False, "explorer_restart": False,
        }])
        return page

    def test_redist_group_buttons(self, qapp: t.Any) -> None:
        from core.redists import group_ids
        page = self._page()
        page._pick_redist_group("gamer")
        checked = {rid for rid, box in page._redist_boxes.items()
                   if box.isChecked()}
        assert checked == set(group_ids("gamer"))
        fired: t.List[t.List[str]] = []
        page.installRedistsRequested.connect(lambda rids: fired.append(rids))
        page._emit_redists()
        assert fired and fired[0] == sorted(set(fired[0])) or fired[0]

    def test_configs_combo_gates_apply(self, qapp: t.Any) -> None:
        page = self._page()
        assert not page._config_apply_btn.isEnabled()
        page.setConfigs([{"name": "A", "tweaks": 2, "apps": 1,
                          "redists": 0, "created": 1, "build": 0,
                          "note": "", "file": "a.json"}])
        assert page._config_apply_btn.isEnabled()
        assert page.current_config_name() == "A"
        fired: t.List[str] = []
        page.configApplyRequested.connect(lambda name: fired.append(name))
        page._emit_config_apply()
        assert fired == ["A"]
        page.setConfigs([])
        assert not page._config_apply_btn.isEnabled()

    def test_security_category_rows(self, qapp: t.Any) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton
        page = self._page()
        texts = [w.text() for w in page.findChildren(QLabel)]
        texts += [getattr(w, "fullText", w.text)()
                  for w in page.findChildren(QPushButton)]
        assert any("Файрвол" in x for x in texts)
        assert any("Безопасность" in x for x in texts)
