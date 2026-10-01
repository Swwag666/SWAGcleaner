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
        assert LIGHT_TASKS == frozenset({"ai_test", "ai_models"})
        session.list_ai_models_task(AiSettings())
        session.test_ai_task(AiSettings())
        assert launched == ["ai_models", "ai_test"]
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
        # Тяжёлая завершилась - окно свободно.
        session._settle("cleaner_scan", {})
        assert not session.is_busy()
