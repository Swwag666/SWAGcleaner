"""AI-слой SWAGcleaner: локальная помощница Клиня.

Принцип проекта — «всё локально»: по умолчанию модель ищется в Ollama на
localhost, внешний OpenAI-совместимый эндпоинт человек включает сам и видит
предупреждение, куда уходят его данные. AI выключен по умолчанию (enabled=False),
пока его не попросят.

Наружу отдаётся ровно четыре вещи: настройки (`AiSettings`), фабрика провайдера,
фасад для UI (`AiAssistant`) и чтение конфига. Промпты (`voices`) и транспорт
(`provider`) тоже импортируются — их зовёт `ui/` напрямую за редким исключением.

Все вызовы модели блокирующие: из интерфейса их запускают только в фоновом
потоке.
"""
from __future__ import annotations

from .assistant import AiAssistant
from .config import config_dir, config_path, load_settings, save_settings
from .provider import (
    AiSettings,
    AiUnavailable,
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
    Provider,
    make_provider,
)
from .voices import (
    PERSONA,
    SYSTEM_ANSWER,
    SYSTEM_EXPLAIN,
    SYSTEM_MASCOT,
    build_explain_prompt,
    build_mascot_prompt,
    build_question_prompt,
    clip_json,
)

__all__ = [
    "AiAssistant",
    "AiSettings",
    "AiUnavailable",
    "AnthropicProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "Provider",
    "make_provider",
    "load_settings",
    "save_settings",
    "config_path",
    "config_dir",
    "PERSONA",
    "SYSTEM_EXPLAIN",
    "SYSTEM_ANSWER",
    "SYSTEM_MASCOT",
    "build_explain_prompt",
    "build_question_prompt",
    "build_mascot_prompt",
    "clip_json",
]
