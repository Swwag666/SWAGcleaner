"""Правила-фундамент: советы без модели, детерминированно и офлайн.

Модель в проекте - опция, а советник обязан говорить всегда, даже на
тапке без Ollama и без сети. Здесь живут три детерминированные вещи:

- `system_facts()` - честные факты о машине (ОЗУ, ядра, билд Windows);
- `explain_advisor()` - объяснение плана советника словами из наших же
  данных (числа программ, рекомендации), без единого запроса наружу;
- `recommend_tweaks()` - какие пакеты твиков подходят под железо: правила
  простые и честные, ничего не выдумывают.

Тексты заказываются через переданный `tr` (ui.context), чтобы правила не
знали ни про виджеты, ни про локаль: они отдают ключи и параметры.
"""
from __future__ import annotations

import ctypes
import os
import typing as t


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def ram_gb() -> int:
    """Полный объём ОЗУ в гигабайтах (округлённо вверх). 0 - не удалось."""
    try:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)):
            return 0
        return max(1, -(-status.ullTotalPhys // (1024 ** 3)))
    except Exception:  # noqa: BLE001 - не-Windows в тестах: честно ноль
        return 0


def cpu_count() -> int:
    return os.cpu_count() or 0


def system_facts() -> t.Dict[str, int]:
    """Факты о машине для правил и для промптов модели."""
    from core.tweaks import current_build
    return {
        "ram_gb": ram_gb(),
        "cpus": cpu_count(),
        "build": current_build(),
    }


def explain_advisor(apps: int,
                    recs: t.Sequence[t.Mapping[str, t.Any]],
                    tr: t.Callable[[str], str]) -> str:
    """Объяснение плана советника из наших же чисел, без модели.

    Говорит ровно то, что известно: сколько программ осмотрено, сколько
    рекомендаций и какого типа. Никаких «кажется, у вас лишний антивирус».
    """
    removes = sum(1 for r in recs if r.get("type") == "remove")
    explores = sum(1 for r in recs if r.get("type") == "explore")
    lines = [tr("rules.advisor_apps").format(count=apps)]
    if not recs:
        lines.append(tr("rules.advisor_clean"))
        return "\n".join(lines)
    lines.append(tr("rules.advisor_recs").format(
        total=len(recs), remove=removes, explore=explores))
    for rec in recs[:8]:
        name = str(rec.get("display_name") or rec.get("name") or "?")
        kind = str(rec.get("type", ""))
        reason = str(rec.get("reason") or rec.get("description") or "")
        key = f"rules.rec_{kind}"
        lines.append("- " + tr(key).format(name=name, reason=reason))
    return "\n".join(lines)


def recommend_tweaks(facts: t.Optional[t.Mapping[str, int]] = None,
                     available_ids: t.Optional[t.Container[str]] = None
                     ) -> t.List[t.Tuple[str, str]]:
    """Пакеты твиков под железо: [(id_пресета, ключ_причины), ...].

    Правила честные и скучные: телеметрия не зависит от железа, пакет
    производительности советуем только при тесном ОЗУ или слабом CPU.
    """
    facts = dict(facts or system_facts())
    out: t.List[t.Tuple[str, str]] = [("privacy", "rules.why_privacy")]
    ram = int(facts.get("ram_gb") or 0)
    cpus = int(facts.get("cpus") or 0)
    if (0 < ram <= 8) or (0 < cpus <= 4):
        out.append(("performance", "rules.why_performance"))
    if available_ids is not None:
        out = [pair for pair in out if pair[0] in available_ids]
    return out
