# -*- coding: utf-8 -*-
"""Этап 5: каталог моделей, правила-фундамент, секция AI в настройках."""
from __future__ import annotations

import typing as t

from ui.context import ctx


class TestListModels:
    def test_ollama_catalog(self, monkeypatch: t.Any) -> None:
        from ai.provider import AiSettings, OllamaProvider
        import ai.provider as prov

        seen = {}

        def fake_get(url, timeout_sec, label):
            seen["url"] = url
            return {"models": [{"name": "qwen2.5:3b"}, {"name": "llama3.1"},
                               {"model": "phi3"}]}

        monkeypatch.setattr(prov, "_get_json", fake_get)
        names = OllamaProvider(AiSettings()).list_models()
        assert names == ["qwen2.5:3b", "llama3.1", "phi3"]
        assert seen["url"].endswith("/api/tags")

    def test_openai_catalog(self, monkeypatch: t.Any) -> None:
        from ai.provider import AiSettings, OpenAIProvider
        import ai.provider as prov

        headers = {}

        def fake_request(req, timeout_sec, label, url):
            headers.update(dict(req.header_items()))
            return {"data": [{"id": "gpt-4o-mini"}, {"id": "local/qwen"}]}

        monkeypatch.setattr(prov, "_request_json", fake_request)
        settings = AiSettings(provider="openai",
                              base_url="https://api.example.com/v1",
                              api_key="sk-test")
        names = OpenAIProvider(settings).list_models()
        assert names == ["gpt-4o-mini", "local/qwen"]
        assert headers.get("Authorization") == "Bearer sk-test"

    def test_base_provider_empty(self) -> None:
        from ai.provider import AiSettings, Provider

        class Dummy(Provider):
            def complete(self, system, user, temperature=0.4,
                         json_mode=False):
                return ""

            def ping(self, timeout_sec=1.5):
                return True

        assert Dummy(AiSettings()).list_models() == []


class TestRules:
    def test_explain_advisor_counts(self) -> None:
        from ai.rules import explain_advisor
        recs = [{"type": "remove", "name": "Foo Bar", "reason": "шелл"},
                {"type": "explore", "name": "Bloatware X",
                 "reason": "похоже на шелл"}]
        text = explain_advisor(42, recs, lambda key: f"<{key}>")
        assert "<rules.advisor_apps>" in text
        assert "<rules.rec_remove>" in text
        assert "<rules.rec_explore>" in text

    def test_explain_advisor_clean(self) -> None:
        from ai.rules import explain_advisor
        text = explain_advisor(10, [], lambda key: f"<{key}>")
        assert "<rules.advisor_clean>" in text

    def test_recommend_tweaks_low_ram(self) -> None:
        from ai.rules import recommend_tweaks
        recs = recommend_tweaks({"ram_gb": 4, "cpus": 8, "build": 26200},
                                {"privacy", "performance"})
        assert [pid for pid, _ in recs] == ["privacy", "performance"]

    def test_recommend_tweaks_fat_machine(self) -> None:
        from ai.rules import recommend_tweaks
        recs = recommend_tweaks({"ram_gb": 32, "cpus": 16, "build": 26200},
                                {"privacy", "performance"})
        assert [pid for pid, _ in recs] == ["privacy"]

    def test_recommend_filters_unknown_presets(self) -> None:
        from ai.rules import recommend_tweaks
        recs = recommend_tweaks({"ram_gb": 2, "cpus": 2, "build": 26200},
                                {"performance"})
        assert [pid for pid, _ in recs] == ["performance"]

    def test_ram_gb_sane(self) -> None:
        from ai.rules import ram_gb
        value = ram_gb()
        assert isinstance(value, int) and value >= 0


class TestSessionAi:
    def test_save_and_hot_swap(self, qapp: t.Any, tmp_path: t.Any,
                               monkeypatch: t.Any) -> None:
        import ai.config as cfg
        from ai.provider import AiSettings
        from ui.session import Session

        monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
        from core.swagscan import SwagscanClient

        class _FakeClient(SwagscanClient):
            def __init__(self) -> None:
                pass

            def available(self) -> bool:
                return False

        session = Session(client=_FakeClient())
        settings = AiSettings(enabled=True, model="qwen2.5:3b")
        assert session.save_ai_settings(settings) is True
        assert (tmp_path / "ai.json").exists()
        assert session.ai().settings.model == "qwen2.5:3b"
        from ai import load_settings
        assert load_settings().model == "qwen2.5:3b"

    def test_test_ai_ok_text(self, qapp: t.Any, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        from ai.provider import AiSettings
        from ui.session import Session
        from core.swagscan import SwagscanClient

        class _FakeClient(SwagscanClient):
            def __init__(self) -> None:
                pass

            def available(self) -> bool:
                return False

        monkeypatch.setattr(
            prov, "_get_json",
            lambda url, timeout_sec, label: {"models": [{"name": "a"},
                                                         {"name": "b"}]})
        session = Session(client=_FakeClient())
        text = session.test_ai(AiSettings())
        assert "2" in text  # число моделей из шаблона ai_test_ok


class TestSettingsTabAi:
    def _page(self) -> t.Any:
        from ui.tabs import SettingsTab
        return SettingsTab()

    def test_roundtrip_widgets(self, qapp: t.Any) -> None:
        from ai.provider import AiSettings
        page = self._page()
        settings = AiSettings(enabled=True, provider="openai",
                              base_url="https://api.example.com/v1",
                              model="gpt-4o-mini", api_key="sk-x",
                              timeout_sec=90)
        page.setAiSettings(settings)
        back = page.collect_ai_settings()
        assert back.provider == "openai"
        assert back.base_url == "https://api.example.com/v1"
        assert back.model == "gpt-4o-mini"
        assert back.api_key == "sk-x"
        assert back.timeout_sec == 90
        assert back.enabled is True

    def test_remote_warning_visibility(self, qapp: t.Any) -> None:
        from ai.provider import AiSettings
        page = self._page()
        page.setAiSettings(AiSettings(base_url="http://localhost:11434"))
        assert page._ai_remote_warn.isHidden()
        page.setAiSettings(AiSettings(base_url="https://api.example.com/v1"))
        assert not page._ai_remote_warn.isHidden()

    def test_key_hidden_for_ollama(self, qapp: t.Any) -> None:
        from ai.provider import AiSettings
        page = self._page()
        page.setAiSettings(AiSettings(provider="ollama"))
        assert page._ai_key.isHidden()
        assert page._ai_key_label.isHidden()
        page.setAiSettings(AiSettings(provider="openai"))
        assert not page._ai_key.isHidden()
        assert not page._ai_key_label.isHidden()

    def test_save_signal(self, qapp: t.Any) -> None:
        page = self._page()
        fired: t.List[t.Any] = []
        page.aiSaveRequested.connect(lambda s: fired.append(s))
        page._emit_ai_save()
        assert len(fired) == 1 and fired[0].provider == "ollama"

    def test_models_fill_keeps_current(self, qapp: t.Any) -> None:
        from ai.provider import AiSettings
        page = self._page()
        page.setAiSettings(AiSettings(model="my-custom:7b"))
        page.setAiModels(["qwen2.5:3b", "llama3.1"])
        texts = [page._ai_model.itemText(i)
                 for i in range(page._ai_model.count())]
        assert "my-custom:7b" in texts
        assert page._ai_model.currentText() == "my-custom:7b"

    def test_provider_combo_has_anthropic(self, qapp: t.Any) -> None:
        page = self._page()
        data = [page._ai_provider.itemData(i)
                for i in range(page._ai_provider.count())]
        assert data == ["ollama", "openai", "anthropic"]

    def test_key_visible_for_anthropic(self, qapp: t.Any) -> None:
        from ai.provider import AiSettings
        page = self._page()
        page.setAiSettings(AiSettings(provider="anthropic"))
        assert not page._ai_key.isHidden()
        assert not page._ai_key_label.isHidden()

    def test_set_ai_busy_disables_request_buttons(self, qapp: t.Any) -> None:
        page = self._page()
        assert page._ai_models_btn.isEnabled()
        assert page._ai_test_btn.isEnabled()
        page.setAiBusy(True)
        assert not page._ai_models_btn.isEnabled()
        assert not page._ai_test_btn.isEnabled()
        assert page._ai_status.text() == ctx().tr("settings.ai_busy")
        page.setAiBusy(False)
        assert page._ai_models_btn.isEnabled()
        assert page._ai_test_btn.isEnabled()

    def test_model_label_says_model_id(self, qapp: t.Any) -> None:
        page = self._page()
        label = next(lb for lb, key in page._section_labels
                     if key == "settings.ai_model_label")
        assert label.text() == ctx().tr("settings.ai_model_label")
        assert "ID" in label.text()

    def test_model_field_placeholder(self, qapp: t.Any) -> None:
        page = self._page()
        placeholder = page._ai_model.lineEdit().placeholderText()
        assert placeholder == ctx().tr("settings.ai_model_placeholder")
        assert placeholder

    def test_models_fill_opens_popup_when_visible(
            self, qapp: t.Any, monkeypatch: t.Any) -> None:
        page = self._page()
        shown: t.List[int] = []
        monkeypatch.setattr(page._ai_model, "showPopup",
                            lambda: shown.append(1))
        page.setAiModels(["qwen2.5:3b", "llama3.1"])
        assert shown == []  # страница скрыта: список сами не раскрываем
        page.show()
        qapp.processEvents()
        page.setAiModels(["qwen2.5:3b", "llama3.1"])
        assert shown == [1]
        page.setAiModels([])
        assert shown == [1]  # пустой каталог не раскрываем
        page.hide()

    def test_click_in_model_field_opens_popup(
            self, qapp: t.Any, monkeypatch: t.Any) -> None:
        from PySide6.QtCore import QEvent, QPoint, Qt
        from PySide6.QtGui import QMouseEvent

        page = self._page()
        shown: t.List[int] = []
        monkeypatch.setattr(page._ai_model, "showPopup",
                            lambda: shown.append(1))
        line = page._ai_model.lineEdit()
        page.show()
        qapp.processEvents()

        def click() -> None:
            press = QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(4, 4),
                                line.mapToGlobal(QPoint(4, 4)),
                                Qt.MouseButton.LeftButton,
                                Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            qapp.sendEvent(line, press)

        click()
        qapp.processEvents()  # singleShot(0) из фильтра срабатывает здесь
        assert shown == [1]
        # клик при открытом списке фильтр не дублирует
        monkeypatch.setattr(page._ai_model.view(), "isVisible",
                            lambda: True)
        click()
        qapp.processEvents()
        assert shown == [1]
        page.hide()


class TestAiLightTasks:
    """Лёгкие AI-задачи идут мимо гейта занятости тяжёлых."""

    def _session(self) -> t.Any:
        from ui.session import Session
        from core.swagscan import SwagscanClient

        class _FakeClient(SwagscanClient):
            def __init__(self) -> None:
                pass

            def available(self) -> bool:
                return False

        return Session(client=_FakeClient())

    def test_models_task_runs_while_heavy_busy(self, qapp: t.Any,
                                               monkeypatch: t.Any) -> None:
        from ai.provider import AiSettings
        session = self._session()
        # Тяжёлая задача «идёт»: гейт закрыт.
        session._set_busy(True)
        assert session.is_busy()

        launched: t.List[str] = []

        def fake_run(name, func):
            launched.append(name)
            return True

        monkeypatch.setattr(session, "_run", fake_run)
        from ui.session import LIGHT_TASKS
        # Лёгкие задачи идут мимо гейта занятости: AI-запросы и опись
        # карантина (чтение манифестов - сотни записей, не скан диска).
        assert LIGHT_TASKS == frozenset(
            {"ai_test", "ai_models", "ai_ask", "quarantine_list"})
        session.list_ai_models_task(AiSettings())
        session.test_ai_task(AiSettings())
        session.ask_ai_task("что за файлы?")
        assert launched == ["ai_models", "ai_test", "ai_ask"]
        # Гейт занятости лёгкими задачами не трогается.
        assert session.is_busy()

    def test_light_settle_keeps_heavy_busy(self, qapp: t.Any) -> None:
        session = self._session()
        session._set_busy(True)
        # Лёгкая задача завершилась - флаг тяжёлой остаётся.
        session._settle("ai_models", {"names": []})
        assert session.is_busy()
        session._settle_failed("ai_test", "oops")
        assert session.is_busy()
        session._settle("ai_ask", {"text": ""})
        assert session.is_busy()
        # Тяжёлая завершилась - окно свободно.
        session._settle("cleaner_scan", {})
        assert not session.is_busy()


class TestSessionAsk:
    """Вопрос Клинне: контекст данных, лёгкая задача, честный пустой ответ."""

    def _session(self) -> t.Any:
        from ui.session import Session
        from core.swagscan import SwagscanClient

        class _FakeClient(SwagscanClient):
            def __init__(self) -> None:
                pass

            def available(self) -> bool:
                return False

        return Session(client=_FakeClient())

    def _fake_assistant(self, monkeypatch: t.Any, session: t.Any,
                        enabled: bool = True,
                        answer: str = "это кеш, вернётся сам") -> t.Dict[str, t.Any]:
        seen: t.Dict[str, t.Any] = {}

        class _FakeAssistant:
            def __init__(self, ok: bool, reply: str) -> None:
                self._ok = ok
                self._reply = reply

            def available(self) -> bool:
                return self._ok

            def answer(self, context, question):
                seen["context"] = context
                seen["question"] = question
                return self._reply

        monkeypatch.setattr(session, "_ai", _FakeAssistant(enabled, answer),
                            raising=False)
        return seen

    def test_ask_task_answer_with_context(self, qapp: t.Any,
                                          monkeypatch: t.Any) -> None:
        from tests.fakes import make_scan
        from ui.session import DupGroup
        from ui.session import PurgeReport

        session = self._session()
        seen = self._fake_assistant(monkeypatch, session)
        session._last_scan = make_scan()
        session._last_groups = [DupGroup(hash="h", size=4 * 1024 * 1024,
                                         paths=[r"C:\a.png", r"C:\b.png",
                                                r"C:\c.png"])]
        session._last_recs = [{"type": "remove", "name": "Bloat",
                               "reason": "пустая папка"}]
        session._last_purge = PurgeReport(dry_run=False, removed=5,
                                          freed_bytes=2048)

        captured: t.Dict[str, t.Any] = {}
        monkeypatch.setattr(
            session, "_run",
            lambda name, func: captured.update(name=name, func=func))
        session.ask_ai_task("что за файлы в Temp?")

        assert captured["name"] == "ai_ask"
        result = captured["func"]()
        assert result["question"] == "что за файлы в Temp?"
        assert result["text"] == "это кеш, вернётся сам"

        context = seen["context"]
        assert seen["question"] == "что за файлы в Temp?"
        cats = context["cleaner"]["categories"]
        assert {c["id"] for c in cats} == {"temp.app", "installers"}
        by_id = {c["id"]: c for c in cats}
        assert by_id["temp.app"]["examples"][0].endswith("a.tmp")
        assert by_id["temp.app"]["risk"] == "low"
        assert by_id["installers"]["lane"] == "trash"
        assert context["dupes"]["groups_total"] == 1
        assert context["dupes"]["samples"][0]["copies"] == 3
        assert context["advisor_plan"][0]["name"] == "Bloat"
        assert context["last_purge"]["removed"] == 5

    def test_ask_context_examples_capped(self, qapp: t.Any) -> None:
        from ui.session import CandidateItem, CategorySummary, ScanResult
        from tests.fakes import make_scan

        scan = make_scan()
        scan.items = [
            CandidateItem(path=rf"C:\Temp\{i}.tmp", size=1,
                          categories=["temp.app"])
            for i in range(10)
        ]
        session = self._session()
        session._last_scan = scan
        context = session.ask_context()
        examples = [c for c in context["cleaner"]["categories"]
                    if c["id"] == "temp.app"][0]["examples"]
        assert len(examples) == 3
        assert examples[0] == r"C:\Temp\0.tmp"

    def test_ask_context_empty_without_scans(self, qapp: t.Any) -> None:
        session = self._session()
        context = session.ask_context()
        assert context == {"note": "сканов пока не было: данных о машине нет"}

    def test_ask_task_ai_off_gives_empty_text(self, qapp: t.Any,
                                              monkeypatch: t.Any) -> None:
        session = self._session()
        self._fake_assistant(monkeypatch, session, enabled=False)
        captured: t.Dict[str, t.Any] = {}
        monkeypatch.setattr(
            session, "_run",
            lambda name, func: captured.update(name=name, func=func))
        session.ask_ai_task("что тут?")
        result = captured["func"]()
        assert result["text"] == ""

    def test_ask_task_purge_dry_run_skipped(self, qapp: t.Any) -> None:
        from ui.session import PurgeReport
        from tests.fakes import make_scan

        session = self._session()
        session._last_scan = make_scan()
        session._last_purge = PurgeReport(dry_run=True, planned=4)
        context = session.ask_context()
        assert "last_purge" not in context
        assert "cleaner" in context
