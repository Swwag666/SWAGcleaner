"""Конфиги системы: снимок состояния, файл, применение (этап 7).

Идея человека: переустановил Windows - и одним конфигом вернул всё: твики,
приложения, зависимости, системные настройки (Defender off и прочее - это
тоже твики базы). Конфиг - обычный JSON, живёт в профиле и экспортируется
куда угодно (флешка переживёт переустановку вместе с тобой).

Защита от мисскликов - на трёх уровнях:
1. Применение только явной кнопкой по выбранному конфигу (не кликом по списку).
2. Подтверждение со ВСЕМ составом конфига и счётчиком рискованного.
3. Занятость окна: второе применение параллельно невозможно.

Формат файла:
{
  "version": 1,
  "name": "Мой боевой",
  "note": "",
  "created": 1759000000,
  "build": 26200,
  "tweaks": [{"id": "...", "params": {...}}],
  "apps": ["7zip.7zip"],
  "redists": ["vc2015_x64"]
}
"""
from __future__ import annotations

import json
import os
import re
import time
import typing as t
from pathlib import Path

CONFIG_VERSION = 1
_SLUG_RE = re.compile(r"[^a-zа-яё0-9]+", re.IGNORECASE)


class ConfigError(ValueError):
    """Конфиг битый или чужой: человек увидит причину словами."""


# ---------- хранилище ----------

def configs_dir() -> Path:
    from ai.config import config_dir
    path = Path(config_dir()) / "configs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slug(name: str) -> str:
    base = _SLUG_RE.sub("-", name.strip()).strip("-").lower() or "config"
    return base[:64]


def config_path(name: str) -> Path:
    return configs_dir() / f"{slug(name)}.json"


def build_config(name: str,
                 tweaks: t.Sequence[t.Dict[str, t.Any]],
                 apps: t.Sequence[str],
                 redists: t.Sequence[str],
                 note: str = "",
                 build: int = 0) -> t.Dict[str, t.Any]:
    return {
        "version": CONFIG_VERSION,
        "name": name.strip(),
        "note": note.strip(),
        "created": int(time.time()),
        "build": int(build),
        "tweaks": [{"id": str(x["id"]),
                    "params": dict(x.get("params") or {})} for x in tweaks],
        "apps": [str(a) for a in apps],
        "redists": [str(r) for r in redists],
    }


def validate(config: t.Mapping[str, t.Any]) -> t.Dict[str, t.Any]:
    """Проверить чужой или свой файл; возвращает нормализованный конфиг."""
    if not isinstance(config, dict):
        raise ConfigError("это не JSON-объект")
    version = config.get("version")
    if version != CONFIG_VERSION:
        raise ConfigError(f"версия конфига {version!r}, ждал {CONFIG_VERSION}")
    tweaks = config.get("tweaks")
    if not isinstance(tweaks, list):
        raise ConfigError("нет списка твиков")
    clean_tw: t.List[t.Dict[str, t.Any]] = []
    for item in tweaks:
        if not isinstance(item, dict) or "id" not in item:
            raise ConfigError("твик без id в списке")
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise ConfigError(f"params у {item['id']} - не объект")
        clean_tw.append({"id": str(item["id"]), "params": params})
    apps = config.get("apps") or []
    redists = config.get("redists") or []
    if not isinstance(apps, list) or not all(isinstance(a, str) for a in apps):
        raise ConfigError("список приложений битый")
    if not isinstance(redists, list) or not all(isinstance(r, str)
                                                for r in redists):
        raise ConfigError("список зависимостей битый")
    return build_config(str(config.get("name") or "без имени"),
                        clean_tw, apps, redists,
                        note=str(config.get("note") or ""),
                        build=int(config.get("build") or 0))


def _atomic_write(path: Path, config: t.Mapping[str, t.Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save(config: t.Mapping[str, t.Any]) -> Path:
    clean = validate(config)
    path = config_path(clean["name"])
    _atomic_write(path, clean)
    return path


def load(name: str) -> t.Dict[str, t.Any]:
    path = config_path(name)
    if not path.exists():
        raise ConfigError(f"конфиг «{name}» не найден")
    with open(path, encoding="utf-8") as fh:
        return validate(json.load(fh))


def load_file(path: t.Union[str, Path]) -> t.Dict[str, t.Any]:
    with open(path, encoding="utf-8") as fh:
        return validate(json.load(fh))


def list_configs() -> t.List[t.Dict[str, t.Any]]:
    """Мета всех конфигов профиля, свежие сверху."""
    out: t.List[t.Dict[str, t.Any]] = []
    for path in sorted(configs_dir().glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = validate(json.load(fh))
        except (ConfigError, json.JSONDecodeError, OSError):
            continue
        out.append({
            "name": cfg["name"],
            "file": path.name,
            "created": cfg["created"],
            "build": cfg["build"],
            "note": cfg["note"],
            "tweaks": len(cfg["tweaks"]),
            "apps": len(cfg["apps"]),
            "redists": len(cfg["redists"]),
        })
    return out


def delete(name: str) -> bool:
    path = config_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def export(name: str, dest: t.Union[str, Path]) -> Path:
    cfg = load(name)
    dest = Path(dest)
    _atomic_write(dest, cfg)
    return dest


def import_config(src: t.Union[str, Path]) -> t.Dict[str, t.Any]:
    """Принести файл извне: валидация + копия в профиль."""
    cfg = load_file(src)
    save(cfg)
    return cfg


# ---------- применение ----------

def apply_config(config: t.Mapping[str, t.Any],
                 apply_tweak: t.Callable[[str, t.Dict[str, t.Any]], bool],
                 install_apps: t.Callable[[t.List[str]],
                                          t.List[t.Dict[str, t.Any]]],
                 install_redists: t.Callable[[t.List[str]],
                                             t.List[t.Dict[str, t.Any]]],
                 known_tweaks: t.Optional[t.Set[str]] = None,
                 progress: t.Optional[t.Callable[[int, int, str], None]] = None,
                 cancel: t.Optional[t.Any] = None,
                 ) -> t.Dict[str, t.Any]:
    """Применить конфиг поitem'но с честным отчётом.

    apply_tweak(id, params) -> bool; install_* возвращают списки
    {"id","ok"} из appinstall. Неизвестные id твиков пропускаются с ok=False
    и причиной в отчёте: база могла уехать вперёд файла конфига.
    """
    clean = validate(config)
    tweaks_rep: t.List[t.Dict[str, t.Any]] = []
    total = len(clean["tweaks"]) + (1 if clean["apps"] else 0) \
        + (1 if clean["redists"] else 0)
    done = 0

    def tick(label: str) -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total, label)

    for item in clean["tweaks"]:
        if cancel is not None and cancel.is_set():
            tweaks_rep.append({"id": item["id"], "ok": False,
                               "error": "отменено"})
            tick(item["id"])
            continue
        if known_tweaks is not None and item["id"] not in known_tweaks:
            tweaks_rep.append({"id": item["id"], "ok": False,
                               "error": "нет в базе твиков"})
            tick(item["id"])
            continue
        try:
            ok = bool(apply_tweak(item["id"], item["params"]))
            err = ""
        except Exception as exc:  # noqa: BLE001 - отчёт важнее стектрейса
            ok, err = False, str(exc)
        rep = {"id": item["id"], "ok": ok}
        if err:
            rep["error"] = err
        tweaks_rep.append(rep)
        tick(item["id"])

    apps_rep: t.List[t.Dict[str, t.Any]] = []
    if clean["apps"]:
        if cancel is not None and cancel.is_set():
            apps_rep = [{"id": a, "ok": False, "error": "отменено"}
                        for a in clean["apps"]]
        else:
            apps_rep = [
                {"id": r.get("id", "?"), "ok": bool(r.get("ok")),
                 **({"error": r["error"]} if r.get("error") else {})}
                for r in install_apps(list(clean["apps"]))
            ]
        tick("apps")

    redists_rep: t.List[t.Dict[str, t.Any]] = []
    if clean["redists"]:
        if cancel is not None and cancel.is_set():
            redists_rep = [{"id": r, "ok": False, "error": "отменено"}
                           for r in clean["redists"]]
        else:
            redists_rep = [
                {"id": r.get("id", "?"), "ok": bool(r.get("ok")),
                 **({"error": r["error"]} if r.get("error") else {})}
                for r in install_redists(list(clean["redists"]))
            ]
        tick("redists")

    all_reps = tweaks_rep + apps_rep + redists_rep
    return {
        "tweaks": tweaks_rep,
        "apps": apps_rep,
        "redists": redists_rep,
        "ok": sum(1 for r in all_reps if r["ok"]),
        "fail": sum(1 for r in all_reps if not r["ok"]),
        "cancelled": bool(cancel is not None and cancel.is_set()),
    }
