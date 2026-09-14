"""Фасад AI-слоя для интерфейса: `AiAssistant`.

UI работает только с этим классом и никогда не лезет в транспорт. Главное
обещание фасада: он не бросает исключений в поток интерфейса. Любой сбой
модели — это пустая строка или пустой список и запись в лог, а не красное
окно. Ответили «нет ответа» лучше, чем уронили клинер.

Все методы блокирующие (они ходят в сеть и ждут генерацию), поэтому из UI их
вызывают только из фоновых потоков. Единственное исключение — `available()`:
он сделан дешёвым и кэшированным, чтобы им можно было прикрыть тумблер AI
в настройках.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import typing as t

from .provider import (
    AiSettings,
    AiUnavailable,
    Provider,
    make_provider,
)
from .voices import (
    SYSTEM_ANSWER,
    SYSTEM_EXPLAIN,
    SYSTEM_MASCOT,
    build_explain_prompt,
    build_mascot_prompt,
    build_question_prompt,
)

LOGGER = logging.getLogger("swag.ai.assistant")

PING_CACHE_SEC = 30.0
PING_TIMEOUT_SEC = 1.5
MAX_LINE_CHARS = 220
MAX_LINES = 5

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


class AiAssistant:
    """Точка входа AI-слоя: объяснения, ответы на вопросы, реплики маскота.

    `provider` передают в тестах, чтобы не трогать сеть. Если он None,
    провайдер строится из настроек при первом обращении.
    """

    def __init__(
        self,
        settings: t.Optional[AiSettings] = None,
        provider: t.Optional[Provider] = None,
    ) -> None:
        self._settings = settings or AiSettings()
        self._provider = provider
        self._lock = threading.Lock()
        self._ping_ok: t.Optional[bool] = None
        self._ping_at: float = 0.0

    @property
    def settings(self) -> AiSettings:
        """Настройки, с которыми живёт ассистент."""
        return self._settings

    def set_settings(self, settings: AiSettings) -> None:
        """Меняет настройки и сбрасывает провайдера с кэшем ping'а.

        Вызывать из UI, когда человек сохранил настройки AI.
        """
        with self._lock:
            self._settings = settings
            self._provider = None
            self._ping_ok = None
            self._ping_at = 0.0

    def _resolve_provider(self) -> t.Optional[Provider]:
        """Лениво строит провайдер из настроек; кривые настройки — это None."""
        if self._provider is not None:
            return self._provider
        try:
            self._provider = make_provider(self._settings)
        except AiUnavailable as exc:
            LOGGER.warning("AI: провайдер не собрался: %s", exc)
            return None
        return self._provider

    def available(self) -> bool:
        """Можно ли вообще дёргать AI: тумблер включён и сервис отвечает.

        Результат ping'а кэшируется на 30 секунд, чтобы настройка не звонила
        в Ollama при каждой перерисовке. При любом сбое возвращает False —
        исключений наружу не выпускается.
        """
        if not self._settings.enabled:
            return False
        provider = self._resolve_provider()
        if provider is None:
            return False
        now = time.monotonic()
        with self._lock:
            cached = self._ping_ok
            fresh = cached is not None and (now - self._ping_at) < PING_CACHE_SEC
            if fresh:
                return bool(cached)
        try:
            ok = bool(provider.ping(timeout_sec=PING_TIMEOUT_SEC))
        except Exception as exc:
            LOGGER.info("AI: ping не удался: %s", exc)
            ok = False
        with self._lock:
            self._ping_ok = ok
            self._ping_at = now
        return ok

    def invalidate(self) -> None:
        """Забывает результат ping'а — например, после «переподключи»."""
        with self._lock:
            self._ping_ok = None
            self._ping_at = 0.0

    def explain(self, report: t.Dict[str, t.Any], temperature: float = 0.4) -> str:
        """Человеческое объяснение находок скана. Пустая строка, если AI молчит."""
        return self._text(SYSTEM_EXPLAIN, build_explain_prompt(report or {}), temperature)

    def answer(self, report: t.Dict[str, t.Any], question: str, temperature: float = 0.3) -> str:
        """Ответ на вопрос человека по данным скана. Пустая строка при сбое."""
        return self._text(SYSTEM_ANSWER, build_question_prompt(report or {}, question), temperature)

    def mascot_lines(self, state: str, facts: t.Optional[t.Dict[str, t.Any]] = None) -> t.List[str]:
        """Реплики маскота для состояния UI.

        Ждёт от модели JSON-массив строк и вытаскивает его устойчиво: сначала
        честный `json.loads`, потом поиск первого `[` и последней `]` (модели
        любят обрамлять массив пояснениями или markdown). Если массив так и не
        собрался — одна реплика из всего текста. При недоступности AI — пустой
        список, interface молча показывает свою заготовку.
        """
        text = self._text(SYSTEM_MASCOT, build_mascot_prompt(state, facts or {}), 0.8)
        if not text:
            return []
        return _parse_lines(text)

    def _text(self, system: str, user: str, temperature: float) -> str:
        """Один запрос к модели с гарантией: исключений наружу не выпускаем."""
        if not self._settings.enabled:
            return ""
        provider = self._resolve_provider()
        if provider is None:
            return ""
        try:
            answer = provider.complete(system=system, user=user, temperature=temperature)
        except AiUnavailable as exc:
            LOGGER.warning("AI: ответ не получен: %s", exc)
            self.invalidate()
            return ""
        except Exception as exc:
            LOGGER.warning("AI: непредвиденный сбой провайдера: %s", exc)
            return ""
        return (answer or "").strip()


def _parse_lines(text: str) -> t.List[str]:
    """Достаёт из ответа модели список коротких реплик, как получится."""
    cleaned = _strip_code_fence(text)
    for candidate in (cleaned, _array_slice(cleaned)):
        if candidate is None:
            continue
        data = _load_array(candidate)
        if data is not None:
            return _normalize(data)[:MAX_LINES]
    one = " ".join(cleaned.split())
    return [one[:MAX_LINE_CHARS]] if one else []


def _array_slice(text: str) -> t.Optional[str]:
    """Вырезает подстроку от первой `[` до последней `]` — если они есть."""
    match = _JSON_ARRAY_RE.search(text)
    if not match:
        return None
    return match.group(0)


def _strip_code_fence(text: str) -> str:
    """Снимает обёртку ```json … ``` — модель часто оборачивает массив в код-блок."""
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return body.strip("`").strip()


def _load_array(text: t.Optional[str]) -> t.Optional[t.List[t.Any]]:
    """Пытается прочесть текст как JSON-массив. Не вышло или пусто — None."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None


def _normalize(data: t.Any) -> t.List[str]:
    """Приводит распарсенный массив к списку непустых строк разумной длины."""
    if data is None:
        return []
    values = data if isinstance(data, list) else [data]
    out: t.List[str] = []
    for value in values:
        if isinstance(value, str):
            line = " ".join(value.split())
        elif isinstance(value, dict):
            line = " ".join(str(v) for v in value.values() if isinstance(v, (str, int, float)))
            line = " ".join(line.split())
        else:
            line = ""
        if line:
            out.append(line[:MAX_LINE_CHARS])
    return out
