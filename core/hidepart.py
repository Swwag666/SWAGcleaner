"""Скрытие букв дисков в «Этот компьютер» — битмаска NoDrives.

A=1, B=2, C=4, D=8, ... Z=2^25. Буквы B и C не отдаём наружу: B - исторический
флоппи, C - системный, прятать их - искать себе приключений. Буквы, которых
в системе нет, прятать бессмысленно, но не вредно - диалог показывает только
существующие диски.
"""
from __future__ import annotations

import string
import typing as t

_BANNED = frozenset("BC")


def letters_to_mask(letters: t.Iterable[str]) -> int:
    """Буквы → битмаска. Регистр не важен, лишнее отбрасывается."""
    mask = 0
    for letter in letters:
        up = letter.upper()
        if up in _BANNED:
            raise ValueError(f"букву {up} прятать нельзя")
        if up in string.ascii_uppercase:
            mask |= 1 << (ord(up) - ord("A"))
    return mask


def mask_to_letters(mask: int) -> t.List[str]:
    """Битмаска → отсортированный список букв."""
    return [c for c in string.ascii_uppercase
            if mask & (1 << (ord(c) - ord("A")))]


def present_drives() -> t.List[str]:
    """Буквы существующих дисков (без B и C), отсортированные."""
    import ctypes
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    out = []
    for i, c in enumerate(string.ascii_uppercase):
        if c in _BANNED:
            continue
        if bitmask & (1 << i):
            out.append(c)
    return out
