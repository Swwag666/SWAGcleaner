"""Тесты AI-слоя: настройки, транспорт, промпты, фасад и конфиг.

Сеть не трогается нигде: фасад проверяется через подставленный фейковый
провайдер, транспорт — через передачу неверных настроек (исключение до
попытки соединения) и прямой разбор ответов-структур.
"""
from __future__ import annotations

import json
import typing as t
from pathlib import Path

import pytest

from ai.assistant import AiAssistant, _parse_lines
from ai.config import config_path, load_settings, save_settings
from ai.provider import (
    AiSettings,
    AiUnavailable,
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
    make_provider,
)
from ai.voices import (
    MAX_JSON_CHARS,
    SYSTEM_ANSWER,
    SYSTEM_EXPLAIN,
    SYSTEM_MASCOT,
    build_explain_prompt,
    build_mascot_prompt,
    build_question_prompt,
    clip_json,
)


class FakeProvider:
    """Заглушка транспорта: возвращает заранее заготовленный ответ или падает.

    Реализует интерфейс `Provider` (complete + ping), сеть не трогает.
    """

    def __init__(
        self,
        answer: str = "",
        error: bool = False,
        alive: bool = True,
    ) -> None:
        self._answer = answer
        self._error = error
        self._alive = alive
        self.calls: t.List[t.Dict[str, t.Any]] = []
        self.pings = 0

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.4,
        json_mode: bool = False,
    ) -> str:
        self.calls.append(
            {"system": system, "user": user, "temperature": temperature, "json_mode": json_mode}
        )
        if self._error:
            raise AiUnavailable("Ollama не отвечает на http://localhost:11434 — запущен ли сервер?")
        return self._answer

    def ping(self, timeout_sec: float = 1.5) -> bool:
        self.pings += 1
        return self._alive


def make_report(size: int = 5) -> t.Dict[str, t.Any]:
    """Мини-отчёт сканера для тестов промптов."""
    return {
        "total_freeable_mb": 4096,
        "categories": [
            {
                "name": f"temp_{i}",
                "size_mb": 100 * i,
                "files": 10 * i,
                "risk": "low",
                "path": f"C:/Users/test/AppData/Local/Temp/{i}",
            }
            for i in range(size)
        ],
    }


class TestSettingsSerialization:
    def test_defaults(self) -> None:
        s = AiSettings()
        assert s.provider == "ollama"
        assert s.base_url == "http://localhost:11434"
        assert s.model == "llama3.1"
        assert s.api_key == ""
        assert s.timeout_sec == 60
        # AI по умолчанию выключен — человек включает сам
        assert s.enabled is False

    def test_to_json_roundtrip(self) -> None:
        s = AiSettings(
            provider="openai",
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
            api_key="sk-test-123",
            timeout_sec=120,
            enabled=True,
        )
        restored = AiSettings.from_json(s.to_json())
        assert restored == s

    def test_from_json_repairs_garbage(self) -> None:
        s = AiSettings.from_json('{"provider": "claude", "timeout_sec": -5, "enabled": "maybe"}')
        assert s.provider == "ollama"
        assert s.timeout_sec == 60
        assert s.enabled is False

    def test_from_json_handles_non_json(self) -> None:
        assert AiSettings.from_json("не json вообще") == AiSettings()
        assert AiSettings.from_json("") == AiSettings()
        assert AiSettings.from_json(None) == AiSettings()

    def test_to_dict_keys(self) -> None:
        data = AiSettings().to_dict()
        assert set(data) == {"provider", "base_url", "model", "api_key", "timeout_sec", "enabled"}

    def test_is_remote(self) -> None:
        assert AiSettings().is_remote() is False
        assert AiSettings(base_url="http://127.0.0.1:11434").is_remote() is False
        assert AiSettings(base_url="https://api.openai.com/v1").is_remote() is True


class TestFactory:
    def test_make_ollama(self) -> None:
        assert isinstance(make_provider(AiSettings()), OllamaProvider)

    def test_make_openai(self) -> None:
        assert isinstance(make_provider(AiSettings(provider="openai")), OpenAIProvider)

    def test_make_anthropic(self) -> None:
        provider = make_provider(AiSettings(provider="anthropic"))
        assert isinstance(provider, AnthropicProvider)

    def test_make_unknown_raises(self) -> None:
        with pytest.raises(AiUnavailable):
            make_provider(AiSettings(provider="claude"))


class TestProviderOfflineBehavior:
    """Транспорт без сети: проверка, что кривые настройки падают по-русски
    ДО любой попытки соединения, а openai-ping вообще не ходит в сеть.
    """

    def test_ollama_empty_base_url(self) -> None:
        with pytest.raises(AiUnavailable) as info:
            OllamaProvider(AiSettings(base_url="  ")).complete("с", "о")
        assert "адрес" in str(info.value).lower() or "Настройки" in str(info.value)

    def test_openai_bad_scheme(self) -> None:
        with pytest.raises(AiUnavailable):
            OpenAIProvider(AiSettings(provider="openai", base_url="ftp://wrong")).complete("с", "о")

    def test_openai_ping_checks_settings_only(self) -> None:
        assert OpenAIProvider(AiSettings(provider="openai", base_url="not-a-url")).ping() is False
        assert OpenAIProvider(AiSettings(provider="openai", model="  ")).ping() is False
        # Вменяемые настройки внешнего API: ping без api_key допустим (локальные прокси)
        assert OpenAIProvider(
            AiSettings(provider="openai", base_url="http://127.0.0.1:8080/v1", model="x")
        ).ping() is True

    def test_ollama_ping_dead_port(self) -> None:
        # Порт 1 на localhost заведомо никого не слушает — ответ должен быть False,
        # исключение наружу не выпускается.
        assert OllamaProvider(AiSettings(base_url="http://127.0.0.1:1")).ping() is False

    def test_ollama_complete_unreachable_raises_russian(self) -> None:
        with pytest.raises(AiUnavailable) as info:
            OllamaProvider(AiSettings(base_url="http://127.0.0.1:1", timeout_sec=2)).complete("с", "о")
        text = str(info.value)
        assert "Ollama" in text and "не отвечает" in text


class TestAnthropicProvider:
    """Messages API: хвосты путей, заголовки, разбор ответов - без сети."""

    def _settings(self, base: str = "https://api.anthropic.com") -> AiSettings:
        return AiSettings(provider="anthropic", base_url=base,
                          model="claude-sonnet-4", api_key="sk-ant-key")

    def test_messages_url_with_plain_base(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        seen = {}

        def fake_post(url, payload, headers, timeout_sec, label):
            seen["url"] = url
            seen["payload"] = payload
            seen["headers"] = headers
            return {"content": [{"type": "text", "text": "привет"}]}

        monkeypatch.setattr(prov, "_post_json", fake_post)
        answer = AnthropicProvider(self._settings()).complete("система", "вопрос")
        assert answer == "привет"
        assert seen["url"] == "https://api.anthropic.com/v1/messages"
        assert seen["payload"]["model"] == "claude-sonnet-4"
        assert seen["payload"]["system"] == "система"
        assert seen["payload"]["messages"] == [{"role": "user", "content": "вопрос"}]
        assert seen["headers"]["x-api-key"] == "sk-ant-key"
        assert seen["headers"]["anthropic-version"] == "2023-06-01"

    def test_messages_url_with_v1_base(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        seen = {}

        def fake_post(url, payload, headers, timeout_sec, label):
            seen["url"] = url
            return {"content": [{"type": "text", "text": "ок"}]}

        monkeypatch.setattr(prov, "_post_json", fake_post)
        AnthropicProvider(
            self._settings(base="https://api.anthropic.com/v1")).complete("с", "о")
        assert seen["url"] == "https://api.anthropic.com/v1/messages"

    def test_json_mode_appends_instruction(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        seen = {}

        def fake_post(url, payload, headers, timeout_sec, label):
            seen["payload"] = payload
            return {"content": [{"type": "text", "text": "{}"}]}

        monkeypatch.setattr(prov, "_post_json", fake_post)
        AnthropicProvider(self._settings()).complete("ты ассистент", "дай json",
                                                     json_mode=True)
        system = seen["payload"]["system"]
        assert system.startswith("ты ассистент")
        assert "JSON" in system

    def test_content_blocks_joined(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        monkeypatch.setattr(
            prov, "_post_json",
            lambda url, payload, headers, timeout_sec, label:
                {"content": [{"type": "text", "text": "часть "},
                             {"type": "tool_use", "id": "x"},
                             {"type": "text", "text": "вторая"}]})
        answer = AnthropicProvider(self._settings()).complete("с", "о")
        assert answer == "часть вторая"

    def test_empty_content_raises_russian(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        monkeypatch.setattr(prov, "_post_json",
                            lambda url, payload, headers, timeout_sec, label:
                                {"content": []})
        with pytest.raises(AiUnavailable) as info:
            AnthropicProvider(self._settings()).complete("с", "о")
        assert "пустой ответ" in str(info.value)

    def test_list_models_headers_and_url(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        seen = {}

        def fake_request(req, timeout_sec, label, url):
            seen["url"] = url
            seen["headers"] = dict(req.header_items())
            return {"data": [{"id": "claude-sonnet-4"}, {"id": "claude-opus-4"}]}

        monkeypatch.setattr(prov, "_request_json", fake_request)
        names = AnthropicProvider(self._settings()).list_models()
        assert names == ["claude-sonnet-4", "claude-opus-4"]
        assert seen["url"] == "https://api.anthropic.com/v1/models"
        headers = {k.lower(): v for k, v in seen["headers"].items()}
        assert headers.get("x-api-key") == "sk-ant-key"
        assert headers.get("anthropic-version") == "2023-06-01"

    def test_list_models_with_v1_base(self, monkeypatch: t.Any) -> None:
        import ai.provider as prov
        seen = {}

        def fake_request(req, timeout_sec, label, url):
            seen["url"] = url
            return {"data": [{"id": "m"}]}

        monkeypatch.setattr(prov, "_request_json", fake_request)
        AnthropicProvider(
            self._settings(base="https://rustvy.xyz/v1")).list_models()
        assert seen["url"] == "https://rustvy.xyz/v1/models"

    def test_ping_checks_settings_only(self) -> None:
        assert AnthropicProvider(
            AiSettings(provider="anthropic", base_url="not-a-url")).ping() is False
        assert AnthropicProvider(
            AiSettings(provider="anthropic", model="  ")).ping() is False
        assert AnthropicProvider(
            AiSettings(provider="anthropic", base_url="http://127.0.0.1:8082",
                       model="x")).ping() is True

    def test_settings_roundtrip_anthropic(self) -> None:
        settings = AiSettings(provider="anthropic",
                              base_url="https://api.anthropic.com",
                              model="claude-sonnet-4")
        data = json.loads(settings.to_json())
        assert data["provider"] == "anthropic"
        restored = AiSettings.from_json(settings.to_json())
        assert restored.provider == "anthropic"
        assert restored.base_url == "https://api.anthropic.com"


class TestPromptBuilders:
    def test_explain_prompt_small_json_not_truncated(self) -> None:
        prompt = build_explain_prompt({"a": 1})
        assert '"a":1' in prompt
        assert "обрезаны" not in prompt

    def test_explain_prompt_clips_big_json(self) -> None:
        report = make_report(600)
        raw = json.dumps(report, ensure_ascii=False)
        assert len(raw) > MAX_JSON_CHARS  # заготовка действительно большая
        prompt = build_explain_prompt(report)
        assert "обрезаны" in prompt
        assert len(prompt) < len(raw)

    def test_clip_json_boundary(self) -> None:
        assert clip_json({"x": "y"}, limit=100) == '{"x":"y"}'
        big = clip_json([{"n": i, "s": "д" * 50} for i in range(500)], limit=1200)
        assert len(big) <= 1200
        assert big.endswith("]") or "обрезаны" in big

    def test_question_prompt_contains_question(self) -> None:
        prompt = build_question_prompt({"a": 1}, "Это можно удалять?")
        assert "Это можно удалять?" in prompt
        assert '"a":1' in prompt
        # Пустой вопрос не ломает промт: подставляется нейтральная формулировка
        empty = build_question_prompt({}, "   ")
        assert "Расскажи, что нашёл сканер." in empty

    def test_mascot_prompt_state_normalized(self) -> None:
        assert "idle" in build_mascot_prompt("  IDLE  ")
        assert "idle" in build_mascot_prompt("unknown_state")
        assert "panic" in build_mascot_prompt("panic", {"free_gb": 0.4})

    def test_system_prompts_are_russian_and_distinct(self) -> None:
        assert "Клиня" in SYSTEM_EXPLAIN
        assert SYSTEM_EXPLAIN != SYSTEM_ANSWER != SYSTEM_MASCOT
        assert "JSON-массивом" in SYSTEM_MASCOT


class TestAssistant:
    def test_disabled_never_calls_provider(self) -> None:
        fake = FakeProvider(answer="привет")
        assistant = AiAssistant(AiSettings(enabled=False), provider=fake)
        assert assistant.available() is False
        assert assistant.explain({"a": 1}) == ""
        assert assistant.answer({}, "вопрос") == ""
        assert assistant.mascot_lines("idle") == []
        assert fake.calls == []

    def test_available_uses_provider_ping_and_caches(self) -> None:
        fake = FakeProvider(alive=True)
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert assistant.available() is True
        assert assistant.available() is True
        # Кэш на 30 секунд: второй вызов не должен звонить в сервис заново.
        assert fake.pings == 1
        assistant.invalidate()
        assert assistant.available() is True
        assert fake.pings == 2

    def test_available_false_when_ping_dead(self) -> None:
        assistant = AiAssistant(AiSettings(enabled=True), provider=FakeProvider(alive=False))
        assert assistant.available() is False

    def test_explain_uses_explain_prompt(self) -> None:
        fake = FakeProvider(answer="  Тут в основном временное, снесём и ничего не потеряется.  ")
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        text = assistant.explain(make_report())
        assert text.startswith("Тут в основном")
        assert fake.calls[0]["system"] == SYSTEM_EXPLAIN
        assert "total_freeable_mb" in fake.calls[0]["user"]

    def test_answer_routes_question(self) -> None:
        fake = FakeProvider(answer="Это кеш, вернётся сам.")
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert assistant.answer({"a": 1}, "А это что?") == "Это кеш, вернётся сам."
        assert fake.calls[0]["system"] == SYSTEM_ANSWER
        assert "А это что?" in fake.calls[0]["user"]

    def test_mascot_lines_clean_json_array(self) -> None:
        fake = FakeProvider(answer='["Скучаю", "Мусора нет", "Обращайся"]')
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert assistant.mascot_lines("idle", {}) == ["Скучаю", "Мусора нет", "Обращайся"]
        assert fake.calls[0]["system"] == SYSTEM_MASCOT

    def test_mascot_lines_json_with_noise(self) -> None:
        fake = FakeProvider(answer='Вот массив:\n```json\n["Один", "Два"]\n```\nНа здоровье!')
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert assistant.mascot_lines("scan") == ["Один", "Два"]

    def test_mascot_lines_not_json_falls_back_to_single_line(self) -> None:
        fake = FakeProvider(answer="Просто текст без всякого массива")
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert assistant.mascot_lines("done") == ["Просто текст без всякого массива"]

    def test_mascot_lines_trims_to_max_five(self) -> None:
        fake = FakeProvider(answer=json.dumps([f"реплика {i}" for i in range(9)]))
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert len(assistant.mascot_lines("idle")) == 5

    def test_ai_unavailable_folds_to_safe_values(self) -> None:
        assistant = AiAssistant(AiSettings(enabled=True), provider=FakeProvider(error=True))
        assert assistant.explain({"a": 1}) == ""
        assert assistant.answer({}, "q") == ""
        assert assistant.mascot_lines("panic", {}) == []

    def test_provider_construction_failure_is_safe(self) -> None:
        # Провайдер не собирается (неизвестный вендор) — фасад не падает.
        assistant = AiAssistant(AiSettings(enabled=True, provider="claude"))
        assert assistant.available() is False
        assert assistant.explain({"a": 1}) == ""
        assert assistant.mascot_lines("idle") == []

    def test_set_settings_resets_provider(self) -> None:
        fake = FakeProvider(answer="раз")
        assistant = AiAssistant(AiSettings(enabled=True), provider=fake)
        assert assistant.explain({}) == "раз"
        assistant.set_settings(AiSettings(enabled=False))
        assert assistant.explain({}) == ""
        assistant.set_settings(AiSettings(enabled=True, provider="claude"))
        # Провайдер пересобран из настроек и не собрался — фолбэк, не исключение.
        assert assistant.explain({}) == ""


class TestParseLinesDirectly:
    def test_object_array_flattened(self) -> None:
        assert _parse_lines('[{"text": "Привет"}, {"text": "Пока"}]') == ["Привет", "Пока"]

    def test_empty_array(self) -> None:
        assert _parse_lines("[]") == []

    def test_array_without_strings_is_empty(self) -> None:
        # Массив распарсился, но строк нет: в UI несём пустой список,
        # а не буквально показываем "[1,2,3]" как реплику.
        assert _parse_lines("[1, 2, 3]") == []

    def test_long_line_is_capped(self) -> None:
        lines = _parse_lines('["' + "д" * 400 + '"]')
        assert len(lines) == 1
        assert len(lines[0]) == 220


class TestConfigRoundtrip:
    def test_missing_file_gives_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "ai.json") == AiSettings()

    def test_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "ai.json"
        s = AiSettings(provider="openai", base_url="https://x.example/v1", model="m", api_key="k", timeout_sec=30, enabled=True)
        assert save_settings(s, target) is True
        assert load_settings(target) == s

    def test_directory_passed_as_path(self, tmp_path: Path) -> None:
        assert config_path(tmp_path) == tmp_path / "ai.json"
        assert config_path(tmp_path / "custom.json") == tmp_path / "custom.json"

    def test_broken_json_repaired_silently(self, tmp_path: Path) -> None:
        target = tmp_path / "ai.json"
        target.write_text("{битый json!!", encoding="utf-8")
        assert load_settings(target) == AiSettings()

    def test_save_failure_returns_false(self, tmp_path: Path) -> None:
        # «Папка» внутри обычного файла: каталог не создать, save честно возвращает False.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        assert save_settings(AiSettings(), blocker / "deep" / "ai.json") is False

    def test_default_path_uses_appdata(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPDATA", str(tmp_path))
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert config_path(None) == tmp_path / "SWAGcleaner" / "ai.json"
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert config_path(None) == tmp_path / "SWAGcleaner" / "ai.json"
