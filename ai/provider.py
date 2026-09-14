"""Транспорт до AI-модели: локальный Ollama или любой OpenAI-совместимый эндпоинт.

Проект живёт по принципу «всё локально», поэтому дефолтный провайдер — Ollama
на http://localhost:11434. Внешний эндпоинт включается только вручную в
настройках, и тогда UI обязан предупредить человека, что данные уходят по
указанному адресу.

Важно про потоки: все методы `complete()` — синхронные и блокирующие. Они
дергают сеть и ждут ответа модели секундами. Вызывать их можно только из
фоновых потоков (QThread, threading.Thread), никогда — из потока интерфейса.

Сетевые и HTTP-ошибки не глотаются: всё выводится в `AiUnavailable` с
человеческим текстом на русском.
"""
from __future__ import annotations

import json
import logging
import socket
import typing as t
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

LOGGER = logging.getLogger("swag.ai.provider")

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CHAT_PATH = "/api/chat"
OLLAMA_TAGS_PATH = "/api/tags"
OPENAI_COMPLETIONS_PATH = "/chat/completions"

DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "llama3.1"
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_TEMPERATURE = 0.4

USER_AGENT = "SWAGcleaner/1.0 (local cleaner assistant)"
PROVIDER_NAMES = ("ollama", "openai")

_HTTP_SCHEMES = ("http://", "https://")


class AiUnavailable(RuntimeError):
    """AI недоступен: сервер не отвечает, адрес пустой, ответ битый.

    Текст исключения написан для человека — его можно показать в UI как есть.
    """


@dataclass
class AiSettings:
    """Настройки AI-слоя. По умолчанию AI выключен — включает человек сам.

    provider — "ollama" (локально) или "openai" (совместимый эндпоинт),
    base_url — адрес сервера без завершающего слэша,
    model — имя модели,
    api_key — ключ для внешнего эндпоинта (для Ollama не нужен),
    timeout_sec — сколько секунд ждать ответ,
    enabled — общий тумблер AI.
    """

    provider: str = DEFAULT_PROVIDER
    base_url: str = OLLAMA_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    enabled: bool = False

    def to_dict(self) -> t.Dict[str, t.Any]:
        """Словарь для сериализации в конфиг."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "timeout_sec": int(self.timeout_sec),
            "enabled": bool(self.enabled),
        }

    def to_json(self) -> str:
        """JSON-строка настроек — ровно то, что кладётся в ai.json."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: t.Optional[t.Mapping[str, t.Any]]) -> "AiSettings":
        """Собирает настройки из словаря, чиня битые значения молча.

        Неизвестные ключи игнорируются, кривые типы приводятся к дефолтам —
        кривой конфиг не должен ронять приложение.
        """
        if not isinstance(data, dict):
            return cls()
        provider = _text(data.get("provider"), DEFAULT_PROVIDER).lower().strip()
        if provider not in PROVIDER_NAMES:
            provider = DEFAULT_PROVIDER
        base_url = _text(data.get("base_url"), OLLAMA_BASE_URL).strip().rstrip("/")
        model = _text(data.get("model"), DEFAULT_MODEL).strip()
        api_key = _text(data.get("api_key"), "").strip()
        timeout_sec = _positive_int(data.get("timeout_sec"), DEFAULT_TIMEOUT_SEC)
        return cls(
            provider=provider,
            base_url=base_url or OLLAMA_BASE_URL,
            model=model or DEFAULT_MODEL,
            api_key=api_key,
            timeout_sec=timeout_sec,
            enabled=_flag(data.get("enabled"), False),
        )

    @classmethod
    def from_json(cls, text: t.Optional[str]) -> "AiSettings":
        """Читает настройки из JSON; битый или пустой текст — это дефолты.

        Починка кривого файла настроек происходит молча, по правилам проекта.
        """
        if not text or not str(text).strip():
            return cls()
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            LOGGER.warning("AI: настройки побиты, беру значения по умолчанию")
            return cls()
        return cls.from_dict(data)

    def is_remote(self) -> bool:
        """True, если данные уходят за пределы машины (внешний адрес)."""
        return not _is_local_host(self.base_url)


def _text(value: t.Any, default: str) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (dict, list, tuple)):
        return default
    return str(value)


def _positive_int(value: t.Any, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    if number <= 0 or number > 3600:
        return default
    return number


def _flag(value: t.Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on", "да"):
            return True
        if lowered in ("0", "false", "no", "off", "нет"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _is_local_host(url: str) -> bool:
    """Локальный ли адрес: localhost, 127.x или ::1 — по хосту из URL."""
    raw = (url or "").strip()
    if not raw:
        return True
    candidate = raw if "://" in raw else "http://" + raw
    try:
        host = (urllib.parse.urlparse(candidate).hostname or "").strip().lower()
    except ValueError:
        host = raw.strip().lower()
    if host in ("localhost", ""):
        return host == "localhost"
    return host.startswith("127.") or host in ("::1", "[::1]") or host.endswith(".localhost")


def _join_url(base: str, tail: str, label: str) -> str:
    cleaned = (base or "").strip().rstrip("/")
    if not cleaned:
        raise AiUnavailable(f"Не задан адрес {label} — укажите его в настройках AI.")
    if not cleaned.lower().startswith(_HTTP_SCHEMES):
        raise AiUnavailable(f"Адрес {cleaned} кривой: нужен http:// или https://")
    return cleaned + tail


def _brief(data: t.Any, limit: int = 240) -> str:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    text = " ".join((text or "").split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(2048)
    except Exception:
        return exc.reason or ""
    if not raw:
        return exc.reason or ""
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        return " ".join(text.split())[:240]
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if value:
                return _brief(value.get("message") if isinstance(value, dict) else value)
    return _brief(data)


def _request_json(
    req: urllib.request.Request,
    timeout_sec: float,
    label: str,
    url: str,
) -> t.Dict[str, t.Any]:
    """Один HTTP-заход с превращением любого сбоя в `AiUnavailable` по-русски."""
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        if exc.code in (401, 403):
            raise AiUnavailable(
                f"{label} отклонил ключ (HTTP {exc.code}). Проверьте api_key в настройках AI."
                + (f" Ответ: {detail}" if detail else "")
            ) from exc
        if exc.code == 404:
            raise AiUnavailable(
                f"{label} ответил 404 по адресу {url}. Проверьте base_url: "
                "для Ollama — порт 11434, для внешнего API — путь обычно заканчивается на /v1."
            ) from exc
        if exc.code == 429:
            raise AiUnavailable(f"{label} ограничил частоту запросов (HTTP 429). Подождите и повторите.") from exc
        raise AiUnavailable(f"{label} вернул ошибку HTTP {exc.code}: {detail or 'без описания'}") from exc
    except socket.timeout as exc:
        raise AiUnavailable(_timeout_message(label, timeout_sec)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None) or exc
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise AiUnavailable(_timeout_message(label, timeout_sec)) from exc
        raise AiUnavailable(_offline_message(label, url, reason)) from exc
    except TimeoutError as exc:
        raise AiUnavailable(_timeout_message(label, timeout_sec)) from exc
    except OSError as exc:
        raise AiUnavailable(_offline_message(label, url, reason=exc)) from exc
    except Exception as exc:
        raise AiUnavailable(f"{label} повёл себя неожиданно: {exc}") from exc

    try:
        text = (raw or b"").decode("utf-8", errors="replace").strip()
    except UnicodeError as exc:
        raise AiUnavailable(f"{label} вернул текст в неизвестной кодировке.") from exc
    if not text:
        raise AiUnavailable(f"{label} вернул пустой ответ — модель, кажется, уснула.")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AiUnavailable(f"{label} вернул не JSON: {_brief(text)}") from exc
    if not isinstance(data, dict):
        raise AiUnavailable(f"{label} вернул неожиданный формат ответа: {_brief(data)}")
    return data


def _timeout_message(label: str, timeout_sec: float) -> str:
    return (
        f"{label} не отвечает дольше {timeout_sec:g} с — таймаут вышел. "
        "Увеличьте таймаут в настройках AI или дайте модели договорить."
    )


def _offline_message(label: str, url: str, reason: t.Any) -> str:
    if label.lower().startswith("ollama"):
        return (
            f"Ollama не отвечает на {url} — запущен ли сервер? "
            "Проверьте команду «ollama serve» и что нужная модель скачана."
        )
    return f"{label} недоступен на {url}: {reason}. Проверьте адрес и ключ в настройках AI."


def _post_json(
    url: str,
    payload: t.Mapping[str, t.Any],
    headers: t.Mapping[str, str],
    timeout_sec: float,
    label: str,
) -> t.Dict[str, t.Any]:
    """POST с JSON-телом; заголовки с пустым значением не отправляются."""
    body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    for key, value in dict(headers or {}).items():
        if value:
            req.add_header(key, value)
    return _request_json(req, timeout_sec, label, url)


def _get_json(url: str, timeout_sec: float, label: str) -> t.Dict[str, t.Any]:
    """GET с JSON-ответом — используется быстрым ping."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    return _request_json(req, timeout_sec, label, url)


class Provider(ABC):
    """Интерфейс генератора текста для AI-слоя.

    Методы блокирующие: работать через них должен только фоновый поток.
    """

    def __init__(self, settings: AiSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> AiSettings:
        """Настройки, из которых собран провайдер."""
        return self._settings

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        json_mode: bool = False,
    ) -> str:
        """Один прямой запрос модели, ответ — строкой.

        Синхронный блокирующий вызов: ждёт сеть и генерацию. Вызывать строго
        из фоновых потоков. При любом сбое бросает `AiUnavailable` с русским
        текстом для человека. `json_mode` просит модель вернуть валидный JSON.
        """

    @abstractmethod
    def ping(self, timeout_sec: float = 1.5) -> bool:
        """Быстрая проверка «жив ли сервис». Никогда не бросает."""


class OllamaProvider(Provider):
    """Локальный Ollama: POST {base}/api/chat без стрима."""

    label = "Ollama"

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        json_mode: bool = False,
    ) -> str:
        settings = self._settings
        payload: t.Dict[str, t.Any] = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": float(temperature)},
        }
        if json_mode:
            payload["format"] = "json"
        url = _join_url(settings.base_url, OLLAMA_CHAT_PATH, self.label)
        data = _post_json(url, payload, {}, settings.timeout_sec, self.label)
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise AiUnavailable(f"Ollama вернула неожиданный ответ: {_brief(data)}")
        return content

    def ping(self, timeout_sec: float = 1.5) -> bool:
        try:
            url = _join_url(self._settings.base_url, OLLAMA_TAGS_PATH, self.label)
        except AiUnavailable as exc:
            LOGGER.info("AI: ping не задался: %s", exc)
            return False
        try:
            data = _get_json(url, timeout_sec, self.label)
        except AiUnavailable as exc:
            LOGGER.info("AI: Ollama не отвечает: %s", exc)
            return False
        return isinstance(data.get("models"), list) or bool(data)


class OpenAIProvider(Provider):
    """OpenAI-совместимый эндпоинт: POST {base}/chat/completions."""

    label = "OpenAI-совместимый сервер"

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        json_mode: bool = False,
    ) -> str:
        settings = self._settings
        payload: t.Dict[str, t.Any] = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(temperature),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        url = _join_url(settings.base_url, OPENAI_COMPLETIONS_PATH, self.label)
        data = _post_json(url, payload, headers, settings.timeout_sec, self.label)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise AiUnavailable(f"{self.label} вернул пустой список ответов: {_brief(data)}")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            content = choices[0].get("text")
        if not isinstance(content, str):
            raise AiUnavailable(f"{self.label} вернул неожиданный ответ: {_brief(data)}")
        return content

    def ping(self, timeout_sec: float = 1.5) -> bool:
        """Сеть не трогает: только проверка, что настройки вообще вменяемые.

        ping'ить внешний API — значит слать запросы наружу без нужды, а у
        проекта принцип «всё локально, наружу только по делу».
        """
        settings = self._settings
        base = (settings.base_url or "").strip().rstrip("/").lower()
        if not base.startswith(_HTTP_SCHEMES):
            LOGGER.info("AI: у внешнего эндпоинта кривой адрес: %s", settings.base_url)
            return False
        if not settings.model.strip():
            LOGGER.info("AI: не указана модель для внешнего эндпоинта")
            return False
        if not settings.api_key:
            LOGGER.info("AI: внешний эндпоинт без ключа — сгодится для локальных прокси-серверов")
        return True


def make_provider(settings: AiSettings) -> Provider:
    """Фабрика: собирает провайдер по настройкам.

    Бросает `AiUnavailable`, если имя провайдера неизвестное или адрес пуст.
    """
    name = (settings.provider or "").strip().lower()
    if name == "ollama":
        return OllamaProvider(settings)
    if name == "openai":
        return OpenAIProvider(settings)
    raise AiUnavailable(
        f"Неизвестный AI-провайдер «{name or 'пусто'}». Доступны: {', '.join(PROVIDER_NAMES)}."
    )
